import json
import os
import tempfile
import threading
from typing import List, Tuple

MAX_DETECTION_IMAGES = 40  # Maximum number of saved detection images
DATA_DIR = "data"
IMAGES_DIR = os.path.join("assets", "images")  # Save to assets so WebUI can serve them
VIDEOS_DIR = os.path.join("assets", "videos")  # Save to assets so WebUI can serve them
LOG_FILE = os.path.join(DATA_DIR, "imageslist.log")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


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


def load_settings(defaults: dict) -> dict:
    """Load settings from settings.json, falling back to defaults if missing/corrupt."""
    if not os.path.exists(SETTINGS_FILE):
        print("[SETTINGS] No settings file found, using defaults")
        return dict(defaults)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        merged = dict(defaults)
        merged.update(saved)
        print(f"[SETTINGS] Loaded settings: {merged}")
        return merged
    except Exception as e:
        print(f"[SETTINGS] Warning: could not read {SETTINGS_FILE}: {e}, using defaults")
        return dict(defaults)


SETTINGS_SAVE_DEBOUNCE = 3  # seconds to wait before writing settings to disk

_pending_settings = None
_save_timer = None
_save_lock = threading.Lock()


def _write_settings_to_disk(settings: dict):
    """Atomically write settings to settings.json (write-to-temp-then-rename)."""
    try:
        fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp_path, SETTINGS_FILE)
        print(f"[SETTINGS] Saved settings: {settings}")
    except Exception as e:
        print(f"[SETTINGS] Error saving settings: {e}")


def _debounce_fire():
    """Timer callback: write the pending settings to disk."""
    global _pending_settings, _save_timer
    with _save_lock:
        if _pending_settings is not None:
            _write_settings_to_disk(_pending_settings)
            _pending_settings = None
        _save_timer = None


def save_settings(settings: dict):
    """Schedule a debounced settings write. Only the last call within the debounce window is saved."""
    global _pending_settings, _save_timer
    with _save_lock:
        _pending_settings = dict(settings)
        if _save_timer is not None:
            _save_timer.cancel()
        _save_timer = threading.Timer(SETTINGS_SAVE_DEBOUNCE, _debounce_fire)
        _save_timer.daemon = True
        _save_timer.start()


def flush_settings():
    """Immediately write any pending settings to disk. Call this on shutdown."""
    global _pending_settings, _save_timer
    with _save_lock:
        if _save_timer is not None:
            _save_timer.cancel()
            _save_timer = None
        if _pending_settings is not None:
            _write_settings_to_disk(_pending_settings)
            _pending_settings = None


def delete_oldest_detection(detection_history: List[dict]) -> None:
    """Delete the oldest detection image, its associated video, and remove from history."""
    if not detection_history:
        return

    oldest = detection_history.pop(0)
    image_path = os.path.join(IMAGES_DIR, oldest.get("filename", ""))
    video_path = os.path.join(VIDEOS_DIR, oldest.get("video_filename", ""))

    try:
        if os.path.exists(image_path):
            os.remove(image_path)
            print(f"[HISTORY] Deleted oldest image: {oldest.get('filename')}")
    except Exception as e:
        print(f"[HISTORY] Error deleting image: {e}")

    try:
        if oldest.get("video_filename") and os.path.exists(video_path):
            os.remove(video_path)
            print(f"[HISTORY] Deleted oldest video: {oldest.get('video_filename')}")
    except Exception as e:
        print(f"[HISTORY] Error deleting video: {e}")

    rewrite_log_file(detection_history)

