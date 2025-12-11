import json
import os
from typing import List, Tuple

MAX_DETECTION_IMAGES = 40  # Maximum number of saved detection images
DATA_DIR = "data"
IMAGES_DIR = os.path.join("assets", "images")  # Save to assets so WebUI can serve them
LOG_FILE = os.path.join(DATA_DIR, "imageslist.log")


def init_data_directories():
    """Create data directories if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    print(f"✅ Data directories initialized: {DATA_DIR}, {IMAGES_DIR}")


def load_detection_history() -> Tuple[List[dict], int]:
    """Load existing detection history from log file on startup.

    Returns:
        (history_list, next_id)
    """
    detection_history: List[dict] = []
    next_detection_id = 1

    if not os.path.exists(LOG_FILE):
        print("[HISTORY] No existing log file found, starting fresh")
        return detection_history, next_detection_id

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        detection_history.append(entry)
                    except json.JSONDecodeError:
                        continue

        # Trim history to configured maximum to avoid unbounded growth
        if len(detection_history) > MAX_DETECTION_IMAGES:
            trimmed = detection_history[-MAX_DETECTION_IMAGES:]
            removed = len(detection_history) - len(trimmed)
            detection_history = trimmed
            print(f"[HISTORY] Trimmed {removed} old records to respect MAX_DETECTION_IMAGES={MAX_DETECTION_IMAGES}")
            rewrite_log_file(detection_history)

        if detection_history:
            # Set next ID based on highest existing ID (after trimming)
            max_id = max(entry.get("id", 0) for entry in detection_history)
            next_detection_id = max_id + 1

        print(f"✅ Loaded {len(detection_history)} detection records from history")
    except Exception as e:
        print(f"[HISTORY] Error loading log file: {e}")

    return detection_history, next_detection_id


def save_detection_to_log(entry: dict):
    """Append a detection entry to the log file."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[HISTORY] Error saving to log: {e}")


def rewrite_log_file(detection_history: List[dict]):
    """Rewrite the entire log file from detection_history (used after rotation)."""
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            for entry in detection_history:
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[HISTORY] Error rewriting log file: {e}")


def delete_oldest_detection(detection_history: List[dict]) -> None:
    """Delete the oldest detection image and remove from history."""
    if not detection_history:
        return

    oldest = detection_history.pop(0)
    image_path = os.path.join(IMAGES_DIR, oldest.get("filename", ""))

    try:
        if os.path.exists(image_path):
            os.remove(image_path)
            print(f"[HISTORY] Deleted oldest image: {oldest.get('filename')}")
    except Exception as e:
        print(f"[HISTORY] Error deleting image: {e}")

    rewrite_log_file(detection_history)

