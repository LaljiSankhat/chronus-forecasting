
from pathlib import Path
from huggingface_hub import snapshot_download

# Project directory
PROJECT_DIR = Path(__file__).resolve().parent

# Local directory for Chronos-2 model files
MODEL_DIR = PROJECT_DIR / "models" / "chronos-2"

MODEL_ID = "amazon/chronos-2"


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading model: {MODEL_ID}")
    print(f"Destination: {MODEL_DIR}")
    print("This may take a few minutes...")

    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=str(MODEL_DIR),
    )

    print("\nModel downloaded successfully!")
    print(f"Local model path: {MODEL_DIR}")


if __name__ == "__main__":
    main()
