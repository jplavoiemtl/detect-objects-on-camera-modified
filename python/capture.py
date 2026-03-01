import base64
import math
import os
import sys
import threading
import time
import warnings
from datetime import datetime
from typing import List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

# Suppress the "websocket-client package not installed" warning from socketio
warnings.filterwarnings('ignore', message='.*websocket-client.*')

# Create a custom stderr filter to suppress websocket-client warnings
class _StderrFilter:
    """Filter stderr to suppress websocket-client warnings."""
    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
    
    def write(self, message):
        msg_lower = message.lower()
        if 'websocket-client' not in msg_lower and 'packet queue' not in msg_lower:
            self.original_stderr.write(message)
    
    def flush(self):
        self.original_stderr.flush()
    
    def fileno(self):
        return self.original_stderr.fileno()

# Install the stderr filter globally for this module
sys.stderr = _StderrFilter(sys.stderr)

from persistence import (
    IMAGES_DIR,
    MAX_DETECTION_IMAGES,
    delete_oldest_detection,
    save_detection_to_log,
)
from health_monitor import restart_video_runner_container

# Use environment variables if available, otherwise defaults
VIDEO_STREAM_PORT = int(os.environ.get("VIDEO_RUNNER_PORT", 4912))
VIDEO_WS_HOST = os.environ.get("VIDEO_RUNNER_HOST", "ei-video-obj-detection-runner")
MODEL_INPUT_SIZE = 416  # YOLO input dimension used by the Brick

# Log configuration on startup
print(f"[CAPTURE] Config: HOST={VIDEO_WS_HOST}, PORT={VIDEO_STREAM_PORT}")

# Frame capture state
_sio_initialized = False
_latest_frame = None
_latest_frame_time = 0.0
_sio_connected = False
_sio_client = None
_last_connect_attempt = 0.0
_reconnect_interval = 5.0
_reconnector_started = False
_stale_watchdog_started = False
_connect_lock = threading.Lock()
_connection_attempt_count = 0  # Track attempts for log throttling

# Connection URL
_video_url = f"http://{VIDEO_WS_HOST}:{VIDEO_STREAM_PORT}"

# Staleness handling
STALE_FRAME_MAX_AGE = 10.0  # seconds (increased from 5.0 for stability)
FRESH_RETRY_TOTAL = 5.0    # seconds – enough time for reconnect + first frame arrival
FRESH_RETRY_SLEEP = 0.1    # seconds
# If no fresh frame arrives for this long while "connected", force reconnect
STALE_RECONNECT_AGE = 30.0  # seconds (increased from 15.0 for stability)
STALE_CHECK_INTERVAL = 5.0  # seconds
WATCHDOG_MAX_OFFLINE = 300.0 # 5 minutes max offline time before self-restart

def _setup_socketio():
    """Set up Socket.IO client for video stream."""
    global _sio_client, _sio_connected, _latest_frame

    try:
        import socketio  # type: ignore
        import engineio  # type: ignore

        # Set logger=False and engineio_logger=False to keep the terminal clean
        # Configure for stable polling transport with appropriate timeouts
        # Disable auto-reconnection so we can manage the lifecycle manually ("nuclear option")
        _sio_client = socketio.Client(
            logger=False, 
            engineio_logger=False,
            reconnection=False,  # We handle reconnection manually
            request_timeout=10   # Shorter timeout
        )

        @_sio_client.event
        def connect():
            global _sio_connected
            _sio_connected = True
            print("[CAPTURE] ✓ Socket.IO connected to video stream")
            # Try to wake up the stream if it's passive
            try:
                _sio_client.emit('start')
                _sio_client.emit('start-stream')
            except Exception:
                pass

        @_sio_client.event
        def disconnect():
            global _sio_connected
            _sio_connected = False

        @_sio_client.on("*")
        def catch_all(event, *args):
            """Catch all events to find frame data."""
            global _latest_frame
            had_frame = _latest_frame is not None
            
            # Process all arguments passed with the event
            for arg in args:
                _process_frame_data(arg)
            
            # Log first frame received (helpful for debugging)
            if not had_frame and _latest_frame is not None:
                print(f"[CAPTURE] ✓ First frame received via event: {event}")

        return True
    except ImportError:
        print("[CAPTURE] Socket.IO client not available")
        return False
    except Exception as e:
        print(f"[CAPTURE] Socket.IO setup error: {e}")
        return False


def _process_frame_data(data):
    """Process incoming frame data from Socket.IO."""
    global _latest_frame, _latest_frame_time
    try:
        # If data is already a numpy array (e.g. from a brick directly)
        if isinstance(data, np.ndarray):
            _latest_frame = data.copy()
            _latest_frame_time = time.time()
            return

        # data might be the dict or the raw base64 string
        img_data = None
        if isinstance(data, dict):
            # Find image data using any common key
            img_data = next(
                (data[k] for k in ["frame", "image", "data", "img", "jpeg", "jpg", "png"] if k in data),
                None
            )
        elif isinstance(data, str):
            img_data = data
        
        if not isinstance(img_data, str):
            return

        # Strip base64 prefix if present
        if "base64," in img_data:
            img_data = img_data.split("base64,", 1)[1]
        
        # Decode and convert to frame
        img_bytes = base64.b64decode(img_data)
        frame = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        
        if frame is not None:
            _latest_frame = frame
            _latest_frame_time = time.time()
    except Exception:
        pass


def _frame_age(now: float) -> float:
    """Return age in seconds of the latest frame, or a large number if none."""
    if _latest_frame_time <= 0:
        return float("inf")
    return now - _latest_frame_time


def _connect_socketio():
    """Connect to the video stream via Socket.IO."""
    global _sio_client, _sio_connected, _connection_attempt_count

    if _sio_connected:
        return True

    # Use a lock to prevent race conditions between background thread and main capture calls
    with _connect_lock:
        # Double-check connection state after acquiring lock
        if _sio_connected:
            return True

        if _sio_client is None and not _setup_socketio():
            return False

        _connection_attempt_count += 1
        should_log = _connection_attempt_count <= 1 or _connection_attempt_count % 5 == 0

        try:
            _sio_client.connect(_video_url, wait_timeout=5)
            time.sleep(0.5)

            if _sio_connected:
                if should_log:
                    print(f"[CAPTURE] ✓ Connected to {_video_url} (attempt #{_connection_attempt_count})")
                _connection_attempt_count = 0  # Reset on success
                return True
        except Exception as e:
            err_str = str(e)
            if "Already connected" in err_str or "disconnected state" in err_str:
                print(f"[CAPTURE] ! Socket.IO state mismatch. Resetting client.")
                try:
                    _sio_client.disconnect()
                except Exception:
                    pass
                _sio_client = None
                _sio_connected = False
                return False

            # Cleanup partially failed connection attempt
            try:
                if _sio_client.eio:
                    _sio_client.eio.disconnect(abort=True)
            except:
                pass

        # Log failure (only periodically to reduce spam)
        if should_log:
            print(f"[CAPTURE] ⚠ Connection failed (attempt #{_connection_attempt_count}): {_video_url}")

        # Destroy client to ensure fresh start
        try:
            if _sio_client:
                _sio_client.disconnect()
        except:
            pass

        _sio_client = None
        _sio_connected = False
        return False


def capture_frame():
    """Capture a single frame from the video stream via Socket.IO."""
    global _latest_frame, _sio_connected, _latest_frame_time, _last_connect_attempt

    now = time.time()

    # NOTE: Synchronous connection attempts here were causing massive lag.
    # We now rely STRICTLY on the background thread (_reconnect_loop) to handle connections.
    # This function is now non-blocking and only returns a frame if one is available.
    
    if not _sio_connected:
        return None

    # If the frame is reasonably fresh (e.g. from working socketio), use it
    if _latest_frame is not None:
        age = _frame_age(now)
        if age < 1.0:
            return _latest_frame.copy()
            

    # Use stale frame if within acceptable limits
    if _latest_frame is not None:
        age = _frame_age(now)
        if age <= STALE_FRAME_MAX_AGE:
            return _latest_frame.copy()
        else:
            # Stale frame, treat as unavailable to force retry
            # Only log excessively stale frames once in a while to avoid spam
            if age > 10.0 and int(age) % 5 == 0:
                print(f"[CAPTURE] Stale frame age={age:.1f}s; ignoring")

            # If the frame is EXTREMELY stale (e.g. > 10s), force a disconnect
            if age > 10.0 and _sio_connected:
                print(f"[CAPTURE] Frame extremely stale ({age:.1f}s); forcing disconnect")
                try:
                    _sio_connected = False
                    if _sio_client:
                        _sio_client.disconnect()
                except Exception:
                    pass
                # Trigger immediate reconnect attempt
                _last_connect_attempt = 0.0

    return None


def _reconnect_loop():
    """Background reconnect loop to recover the video stream when idle.
       Implements exponential backoff to reduce load during extended outages.
    """
    global _sio_initialized, _last_connect_attempt, _sio_connected
    
    current_wait = _reconnect_interval
    max_wait = 60.0
    disconnect_start_time = None
    
    while True:
        now = time.time()
        
        # Only attempt reconnect if disconnected and wait time has passed
        if not _sio_connected:
            
            # Watchdog tracking
            if disconnect_start_time is None:
                disconnect_start_time = now
            elif (now - disconnect_start_time) > WATCHDOG_MAX_OFFLINE:
                print(f"[CAPTURE] Video stream unavailable for >{WATCHDOG_MAX_OFFLINE}s. Restarting video runner container...")
                # Restart the video runner container to recover the video service
                if restart_video_runner_container():
                    # Reset watchdog timer to give the container time to recover
                    disconnect_start_time = now
                else:
                    print(f"[CAPTURE] Container restart failed, will retry on next cycle")

            if (now - _last_connect_attempt) >= current_wait:
                _sio_initialized = True
                _last_connect_attempt = now
                
                if _connect_socketio():
                    # Reset backoff on success
                    current_wait = _reconnect_interval
                    disconnect_start_time = None # Reset watchdog
                else:
                    # Exponential backoff on failure: 5s -> 10s -> 20s -> 40s -> 60s
                    current_wait = min(current_wait * 2, max_wait)
                    if _connection_attempt_count % 10 == 0 or _connection_attempt_count < 5:
                       # Only print occasionally to avoid spam
                       pass
            
            # Shorter sleep to remain responsive to shutdown, but don't spin tight
            time.sleep(1.0)
        else:
            # While connected, check periodically but don't do anything
            # Reset backoff so next failure starts fresh
            if current_wait > _reconnect_interval:
                current_wait = _reconnect_interval
            disconnect_start_time = None
            time.sleep(1.0)


def _stale_watchdog_loop():
    """Force reconnect if we appear connected but no fresh frames arrive for too long."""
    global _latest_frame, _latest_frame_time, _last_connect_attempt, _sio_connected, _sio_client
    while True:
        time.sleep(STALE_CHECK_INTERVAL)
        now = time.time()
        age = _frame_age(now)
        
        # Don't kill connection if we JUST connected (within STALE_RECONNECT_AGE)
        # This gives time for the first frame to arrive
        connection_age = now - _last_connect_attempt
        if connection_age < STALE_RECONNECT_AGE:
             continue

        # If we think we are connected but the client says otherwise, or if frames are too old
        client_connected_status = False
        if _sio_client is not None:
            try:
                client_connected_status = _sio_client.connected
            except:
                pass

        needs_reconnect = (_sio_connected and age > STALE_RECONNECT_AGE) or \
                          (_sio_connected and _sio_client is not None and not client_connected_status)
        
        if needs_reconnect:
            print(f"[CAPTURE] Stale frame ({age:.1f}s), reconnecting...")

            try:
                _sio_connected = False
                if _sio_client is not None:
                    _sio_client.disconnect()
            except Exception as e:
                print(f"[CAPTURE] Reconnect error: {e}")

            _sio_client = None
            _latest_frame = None
            _latest_frame_time = 0.0
            # Trigger immediate reconnect attempt
            _last_connect_attempt = now - _reconnect_interval


def start_capture_reconnect_daemon(reconnect_interval: float = 5.0):
    """Start background reconnect attempts to keep the video stream alive."""
    global _reconnect_interval, _reconnector_started, _stale_watchdog_started
    if _reconnector_started:
        return
    _reconnector_started = True
    _reconnect_interval = max(1.0, reconnect_interval)
    threading.Thread(target=_reconnect_loop, daemon=True).start()
    if not _stale_watchdog_started:
        _stale_watchdog_started = True
        threading.Thread(target=_stale_watchdog_loop, daemon=True).start()


def _get_fresh_frame(timeout: float = FRESH_RETRY_TOTAL, sleep_s: float = FRESH_RETRY_SLEEP):
    """Attempt to obtain a fresh frame within the timeout window."""
    global _last_connect_attempt
    deadline = time.time() + max(0.0, timeout)
    triggered_reconnect = False
    while True:
        frame = capture_frame()
        if frame is not None:
            return frame
        # If disconnected, trigger immediate reconnect attempt (once)
        if not _sio_connected and not triggered_reconnect:
            _last_connect_attempt = 0.0
            triggered_reconnect = True
        if time.time() >= deadline:
            return None
        time.sleep(max(0.0, sleep_s))


def scale_bbox_to_frame(
    bbox_xyxy,
    frame_shape: Optional[Tuple[int, int, int]],
    model_input_size: int = MODEL_INPUT_SIZE,
) -> Optional[List[float]]:
    """Scale bbox coordinates to the captured frame, handling normalization and letterboxing.

    Supports three cases:
    - Normalized [0,1] coordinates
    - Pixel coordinates in the model's square input size (with possible letterboxing)
    - Pixel coordinates already in frame space
    """
    if not bbox_xyxy or frame_shape is None or len(frame_shape) < 2:
        return None

    try:
        coords = [float(c) for c in bbox_xyxy]
    except (TypeError, ValueError):
        return None

    if len(coords) != 4 or any(math.isnan(c) or math.isinf(c) for c in coords):
        return None

    h, w = frame_shape[:2]
    if h <= 0 or w <= 0:
        return None

    x1, y1, x2, y2 = coords
    max_coord = max(coords)
    epsilon = 1e-6

    # Normalized coordinates [0,1]
    if 0 <= max_coord <= 1.0 + epsilon:
        x1, x2 = x1 * w, x2 * w
        y1, y2 = y1 * h, y2 * h
    # Model-space coordinates (e.g., 416x416) with possible letterboxing
    elif max_coord <= model_input_size + epsilon:
        scale = min(model_input_size / w, model_input_size / h)
        scaled_w = w * scale
        scaled_h = h * scale
        pad_x = (model_input_size - scaled_w) / 2.0
        pad_y = (model_input_size - scaled_h) / 2.0
        x1 = (x1 - pad_x) / scale
        x2 = (x2 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        y2 = (y2 - pad_y) / scale
    # Else: assume already in frame pixel space

    # Clamp and validate
    x1 = max(0.0, min(w - 1, x1))
    y1 = max(0.0, min(h - 1, y1))
    x2 = max(0.0, min(w - 1, x2))
    y2 = max(0.0, min(h - 1, y2))

    if x2 <= x1 or y2 <= y1:
        return None

    return [x1, y1, x2, y2]


def capture_and_save_detection(
    label: str,
    confidence: float,
    bbox_xyxy=None,
    *,
    detection_history: List[dict],
    next_detection_id: int,
    timezone,
    frame=None,
    model_input_size: int = MODEL_INPUT_SIZE,
) -> Tuple[Optional[dict], int]:
    """Capture current frame and save as a detection image.

    Optionally draws the provided bounding box (x1, y1, x2, y2) on the frame before saving.

    Returns:
        (entry or None, updated_next_detection_id)
    """
    current_time = time.time()

    # Capture frame (prefer provided, else try fresh)
    frame = frame if frame is not None else _get_fresh_frame()
    if frame is None:
        print("[CAPTURE] No fresh frame available, skipping save")
        return None, next_detection_id

    bbox_scaled = scale_bbox_to_frame(
        bbox_xyxy, frame.shape, model_input_size=model_input_size
    )

    # Generate timestamped filename using local timezone
    now = datetime.now(timezone)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    filename = f"detection_{timestamp_str}_{next_detection_id:03d}.jpg"
    filepath = os.path.join(IMAGES_DIR, filename)

    # Draw bounding box if provided
    if bbox_scaled:
        x1, y1, x2, y2 = bbox_scaled
        cv2.rectangle(
            frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 1
        )

    # Save image
    try:
        cv2.imwrite(filepath, frame)
    except Exception as e:
        print(f"[CAPTURE] Failed to save image: {e}")
        return None, next_detection_id

    # Create log entry
    entry = {
        "id": next_detection_id,
        "filename": filename,
        "label": label,
        "confidence": confidence,
        "timestamp": current_time,
        "time_formatted": now.strftime("%d %b %Y, %H:%M:%S").lstrip("0"),
    }
    if bbox_scaled:
        entry["bbox_xyxy"] = [int(x1), int(y1), int(x2), int(y2)]

    # Add to history
    detection_history.append(entry)
    save_detection_to_log(entry)

    # Update state
    next_detection_id += 1

    # Rotate if needed
    while len(detection_history) > MAX_DETECTION_IMAGES:
        delete_oldest_detection(detection_history)

    print(f"✅ Detection saved: {filename} ({label}, {confidence:.2f})")

    return entry, next_detection_id
