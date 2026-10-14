# Chronos-2 + TradingView: Real-Time XAUUSD Forecasting Guide

This guide documents how to connect a TradingView chart to a local
Chronos-2 forecasting API. TradingView sends confirmed candle data to
FastAPI through a webhook; the API runs Chronos-2 and exposes the latest
forecast.

> **Important:** This setup forecasts the next closing price from
> historical closing prices. It does not yet forecast the full
> next-candle OHLC or display the returned forecast directly inside
> TradingView Pine Script. Forecasts are estimates, not guaranteed
> trading signals.

## 1. Project structure

``` text
chronos-tradingview/
├── pyproject.toml          # dependency contract (uv / PEP 621)
├── uv.lock                 # exact resolved versions, commit this
├── .python-version         # pins the interpreter uv uses (3.13)
├── model_api.py
├── download_model.py
├── .env
├── models/
│   └── chronos-2/
│       ├── config.json
│       ├── model.safetensors
│       └── ...
└── .venv/                  # created by `uv sync`, never committed
```

Recommended `.gitignore` entries:

``` gitignore
.env
.venv/
models/
__pycache__/
```

### Installing dependencies

The environment is fully described by `pyproject.toml` + `uv.lock`. From a
fresh clone, one command recreates `.venv` with exactly these versions:

``` bash
uv sync
```

`uv` creates and manages `.venv` itself — do not create it manually and do
not `pip install` into it, or the environment will drift from the lock file.
Run tools through the managed environment:

``` bash
uv run python download_model.py
uv run uvicorn model_api:app --host 0.0.0.0 --port 8000
```

To add or upgrade a dependency, use `uv add` / `uv lock --upgrade-package <name>`
so `uv.lock` stays in sync, then commit both `pyproject.toml` and `uv.lock`.

## 2. Configure the webhook token

Generate a strong secret:

``` bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put it in `.env`:

``` env
TV_WEBHOOK_TOKEN=replace_with_your_generated_secret
```

The API loads it with `python-dotenv`:

``` python
import os
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_TOKEN = os.environ.get("TV_WEBHOOK_TOKEN")
if not WEBHOOK_TOKEN:
    raise RuntimeError("Set the TV_WEBHOOK_TOKEN environment variable")
```

**Security:** Never commit or publish the token. If it has been shared,
rotate it and update both `.env` and the TradingView webhook URL.

## 3. Load Chronos-2 from the local directory

In `model_api.py`, load the model from the downloaded project directory
instead of the Hugging Face model ID:

``` python
from pathlib import Path
import torch
from chronos import Chronos2Pipeline

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_DIR = Path(__file__).resolve().parent / "models" / "chronos-2"

if not MODEL_DIR.exists():
    raise FileNotFoundError(f"Chronos model directory not found: {MODEL_DIR}")

model = Chronos2Pipeline.from_pretrained(
    str(MODEL_DIR),
    device_map=DEVICE,
    local_files_only=True,
)
```

Ensure the initial download finished and the model weights are present
before setting `local_files_only=True`.

## 4. Start the FastAPI server

``` bash
uv run uvicorn model_api:app --host 0.0.0.0 --port 8000
```

(`uv run` uses `.venv` automatically. If you prefer a normal shell, `source
.venv/bin/activate` still works and then run `uvicorn model_api:app ...`.)

Open the API documentation at <http://localhost:8000/docs>.

The API should provide: - `POST /tv-webhook/{token}` --- receives candle
data from TradingView. - `GET /latest?symbol=...&timeframe=...` ---
returns the most recent forecast for a symbol and timeframe.

Keep the server terminal running.

## 5. Pine Script: send real-time candle data

In TradingView, open Pine Editor, paste this script, save it, and choose
**Add to chart**.

``` pine
//@version=6
indicator("Chronos Live Candle Feed", overlay=true)

historyBars = input.int(64, "Historical closes", minval=20, maxval=128)

if barstate.isconfirmed and barstate.isrealtime and bar_index >= historyBars - 1
    string closesJson = "["
    for i = 0 to historyBars - 1
        int offset = historyBars - 1 - i
        closesJson += (i > 0 ? "," : "") + str.tostring(close[offset], format.mintick)
    closesJson += "]"

    string payload = "{\"symbol\":\"" + syminfo.tickerid +
      "\",\"timeframe\":\"" + timeframe.period +
      "\",\"bar_time\":" + str.tostring(time_close) +
      ",\"open\":" + str.tostring(open, format.mintick) +
      ",\"high\":" + str.tostring(high, format.mintick) +
      ",\"low\":" + str.tostring(low, format.mintick) +
      ",\"close\":" + str.tostring(close, format.mintick) +
      ",\"closes\":" + closesJson + "}"

    alert(payload, alert.freq_once_per_bar_close)
```

At each confirmed real-time candle close, the script sends the
TradingView symbol, timeframe, close timestamp, current candle OHLC, and
the most recent 64 closing prices (oldest first). It does not backfill
historical candles into the API; it starts sending when the alert is
active and a real-time candle closes.

## 6. Expose the local API over HTTPS

For development, use a tunnel such as ngrok. In a second terminal:

``` bash
ngrok http 8000
```

Copy the HTTPS forwarding address shown by ngrok, for example:

``` text
https://YOUR-NGROK-DOMAIN.ngrok-free.app
```

Keep both the FastAPI server and tunnel running. A temporary tunnel URL
can change when the tunnel restarts.

## 7. Create the TradingView alert

1.  Open the intended XAUUSD chart and choose the timeframe you want to
    forecast (start with 5 minutes).

2.  Add **Chronos Live Candle Feed** to the chart.

3.  Select **Create Alert**.

4.  Choose the indicator and **Any alert() function call**.

5.  Enable the webhook URL.

6.  Enter this URL, replacing both placeholders:

    ``` text
    https://YOUR-NGROK-DOMAIN.ngrok-free.app/tv-webhook/YOUR_SECRET_TOKEN
    ```

7.  Create the alert and wait for a confirmed real-time candle close.

The token in the URL must match `TV_WEBHOOK_TOKEN` in `.env`. Treat the
full webhook URL as a secret.

## 8. Verify that real prices are arriving

For temporary debugging, add this inside `forecast()` after calculating
`current_close`:

``` python
print(
    f"[FORECAST INPUT] symbol={payload.symbol}, "
    f"timeframe={payload.timeframe}, "
    f"current_close={current_close}"
)
```

Restart the server after editing. When a new alert arrives, check the
server terminal and confirm that the symbol and closing price match your
TradingView chart.

Use the exact symbol identifier that arrived in the webhook when
querying `/latest`. For example:

``` bash
curl "http://localhost:8000/latest?symbol=OANDA%3AXAUUSD&timeframe=5"
```

Replace `OANDA:XAUUSD` with the actual symbol from your chart. Different
providers can use different symbol names and quote feeds.

The response may contain: - `current_close` - `predicted_next_close` -
`predicted_median_close` - `direction` - `lower_quantile_10` -
`upper_quantile_90` - `predicted_change_percent`

The endpoint may return `404` before the first forecast for that exact
symbol/timeframe has completed. If it returns an `error`, inspect the
API terminal for the underlying exception.

## 9. Test the API with synthetic data (optional)

Synthetic values are only for testing; they are **not live gold
prices**.

``` bash
curl -X POST "http://localhost:8000/tv-webhook/YOUR_SECRET_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "TEST:XAUUSD",
    "timeframe": "5",
    "bar_time": 1791972000000,
    "open": 2650.0,
    "high": 2653.0,
    "low": 2648.0,
    "close": 2651.0,
    "closes": [
      2632,2634,2631,2636,2638,
      2635,2640,2639,2642,2641,
      2645,2643,2646,2648,2645,
      2649,2647,2650,2648,2651
    ]
  }'
```

A successful immediate response is typically:

``` json
{"status":"queued"}
```

After the worker completes, query:

``` bash
curl "http://localhost:8000/latest?symbol=TEST%3AXAUUSD&timeframe=5"
```

## 10. Troubleshooting

### `Could not import module "model_api"`

-   Confirm `model_api.py` exists in the current directory.
-   Run it with the managed environment: `uv run python -c "import model_api"`
    to reveal the underlying import error.
-   If the environment looks stale or hand-edited, rebuild it from the lock
    file with `uv sync --reinstall`.

### `Set the TV_WEBHOOK_TOKEN environment variable`

-   Confirm `.env` is in the project root.
-   Confirm `load_dotenv()` runs before reading `os.environ`.
-   Confirm the variable is named exactly `TV_WEBHOOK_TOKEN`.

### Webhook is queued but `/latest` contains an error

-   The webhook was accepted, but forecasting failed in the background
    worker.
-   Inspect the FastAPI terminal for the exception.
-   Check that the local model loads and that the Chronos-2 inference
    call matches the installed package's API.

### Forecast prices are on the wrong scale

-   Synthetic test data produces synthetic-scale forecasts.
-   Confirm the webhook's `symbol` and `current_close` match the live
    chart.
-   Do not mix candles from different providers or instruments.
-   Check that the alert is using the intended TradingView chart and
    timeframe.

### No new forecast appears

-   Confirm the alert is active.
-   Wait for the next confirmed real-time candle close.
-   Keep the API and HTTPS tunnel running.
-   Confirm the symbol and timeframe in the `/latest` query exactly
    match the webhook payload.

## 11. Current limitations and next steps

This is a prototype. The current model predicts the next close from a
sequence of closes; it does not predict the next candle's open, high,
low, and close independently.

A standard Pine Script indicator cannot directly call the Python API and
read the response back to plot the forecast. The current workflow sends
data to the API and lets you inspect the result through `/latest`. To
visualize forecasts, build a companion dashboard or use a charting
platform that supports a custom data feed.

Before using forecasts for trading: 1. Store each forecast with its
timestamp and input candle data. 2. Compare it with the candle that
actually forms next. 3. Evaluate direction accuracy and price error
across market conditions. 4. Include transaction costs and slippage when
backtesting any strategy. 5. Do not treat forecast quantiles as
guarantees or a calibrated probability of profit.

## Quick start checklist

-   [ ] Chronos-2 model files exist in `models/chronos-2/`.
-   [ ] `.env` contains `TV_WEBHOOK_TOKEN`.
-   [ ] `model_api.py` loads the local model.
-   [ ] FastAPI starts on port 8000.
-   [ ] The Pine connector is added to the correct chart.
-   [ ] An HTTPS tunnel is running.
-   [ ] The TradingView alert webhook URL uses the current token.
-   [ ] The received symbol and price match the chart.
-   [ ] `/latest` returns a forecast for that exact symbol and
    timeframe.
-   [ ] Forecasts are logged and evaluated against future actual
    candles.