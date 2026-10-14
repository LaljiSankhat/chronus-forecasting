
import os
import re
import queue
import threading

import pandas as pd
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from chronos import Chronos2Pipeline
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Chronos TradingView Forecast API")

# Set a strong secret in your environment before starting.
WEBHOOK_TOKEN = os.environ.get("TV_WEBHOOK_TOKEN")
if not WEBHOOK_TOKEN:
    raise RuntimeError("Set the TV_WEBHOOK_TOKEN environment variable")


from pathlib import Path

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Load Chronos-2 from the local project directory.
MODEL_DIR = Path(__file__).resolve().parent / "models" / "chronos-2"

if not MODEL_DIR.exists():
    raise FileNotFoundError(
        f"Chronos model directory not found: {MODEL_DIR}"
    )

print(f"Loading Chronos-2 from: {MODEL_DIR}")
print(f"Using device: {DEVICE}")

model = Chronos2Pipeline.from_pretrained(
    str(MODEL_DIR),
    device_map=DEVICE,
    local_files_only=True,
)

print("Chronos-2 loaded successfully!")


jobs = queue.Queue()
latest_forecasts = {}


class CandlePayload(BaseModel):
    symbol: str
    timeframe: str
    bar_time: int
    open: float
    high: float
    low: float
    close: float
    closes: list[float] = Field(min_length=20, max_length=128)


def timeframe_delta(tf: str) -> pd.Timedelta:
    """Approximate the interval between chart bars."""
    tf = tf.upper()

    if tf.isdigit():
        return pd.Timedelta(minutes=int(tf))

    match = re.fullmatch(r"(\d*)([SDWM])", tf)
    if not match:
        return pd.Timedelta(minutes=1)

    n = int(match.group(1) or "1")
    unit = match.group(2)

    units = {
        "S": pd.Timedelta(seconds=n),
        "D": pd.Timedelta(days=n),
        "W": pd.Timedelta(days=7 * n),
        "M": pd.Timedelta(days=30 * n),
    }
    return units[unit]



def forecast(payload: CandlePayload) -> dict:
    closes = payload.closes

    if len(closes) < 20:
        raise ValueError("At least 20 closing prices are required")

    if not all(
        torch.isfinite(torch.tensor(value)).item()
        for value in closes
    ):
        raise ValueError("Closing prices must be finite numbers")

    current_close = float(closes[-1])

    print(
        f"[FORECAST INPUT] symbol={payload.symbol}, "
        f"timeframe={payload.timeframe}, "
        f"current_close={current_close}"
    )

    # Feed historical closes directly into Chronos-2.
    context = torch.tensor(closes, dtype=torch.float32)

    with torch.inference_mode():
        quantiles, means = model.predict_quantiles(
            inputs=[context],
            prediction_length=1,
            quantile_levels=[0.1, 0.5, 0.9],
        )

    # One input series, one future candle.
    predicted_close = float(means[0][0, 0].item())
    lower = float(quantiles[0][0, 0, 0].item())
    median = float(quantiles[0][0, 0, 1].item())
    upper = float(quantiles[0][0, 0, 2].item())

    direction = (
        "UP" if predicted_close > current_close
        else "DOWN" if predicted_close < current_close
        else "FLAT"
    )

    return {
        "symbol": payload.symbol,
        "timeframe": payload.timeframe,
        "current_close": current_close,
        "predicted_next_close": predicted_close,
        "predicted_median_close": median,
        "direction": direction,
        "lower_quantile_10": lower,
        "upper_quantile_90": upper,
        "predicted_change_percent": (
            (predicted_close / current_close - 1) * 100
            if current_close != 0 else None
        ),
        "model": "amazon/chronos-2",
        "bar_time": payload.bar_time,
    }

def worker():
    while True:
        payload = jobs.get()
        try:
            key = f"{payload.symbol}|{payload.timeframe}"
            latest_forecasts[key] = forecast(payload)
        except Exception as exc:
            key = f"{payload.symbol}|{payload.timeframe}"
            latest_forecasts[key] = {"error": str(exc)}
        finally:
            jobs.task_done()


threading.Thread(target=worker, daemon=True).start()


@app.post("/tv-webhook/{token}")
def receive_candle(token: str, payload: CandlePayload):
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Respond promptly to TradingView and forecast in the worker.
    jobs.put(payload.model_copy(deep=True))
    return {"status": "queued"}


@app.get("/latest")
def latest(symbol: str, timeframe: str):
    key = f"{symbol}|{timeframe}"
    result = latest_forecasts.get(key)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No forecast available yet",
        )

    return result
