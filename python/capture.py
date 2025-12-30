import base64
import math
import os
import socket
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
        # Only write if it doesn't contain the websocket-client warning
        if 'websocket-client' not in message.lower():
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

# Use environment variables if available, otherwise defaults
VIDEO_STREAM_PORT = int(os.environ.get("VIDEO_RUNNER_PORT", 4912))
VIDEO_WS_HOST = os.environ.get("VIDEO_RUNNER_HOST", "ei-video-obj-detection-runner")
MODEL_INPUT_SIZE = 416  # YOLO input dimension used by the Brick

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

# Staleness handling
STALE_FRAME_MAX_AGE = 10.0  # seconds (increased from 5.0 for stability)
FRESH_RETRY_TOTAL = 0.75   # seconds
FRESH_RETRY_SLEEP = 0.05   # seconds
# If no fresh frame arrives for this long while "connected", force reconnect
STALE_RECONNECT_AGE = 30.0  # seconds (increased from 15.0 for stability)
STALE_CHECK_INTERVAL = 5.0  # seconds


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
            print("[CAPTURE] ! Socket.IO disconnect event received")
            _sio_connected = False
            # Don't immediately clear frames on disconnect - polling transport disconnects frequently
            # Frames will be cleared by staleness check if they're actually old
            # Suppress disconnect messages to reduce terminal noise (polling disconnects frequently)

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


_local_ip_cache = None

def _get_local_ip():
    """Get the local IP address of this device (cached)."""
    global _local_ip_cache
    if _local_ip_cache:
        return _local_ip_cache

    try:
        # This doesn't actually connect, just gets the local interface IP used for routing
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        _local_ip_cache = ip
        return ip
    except Exception:
        return None


def _connect_socketio():
    """Connect to the video stream via Socket.IO."""
    global _sio_client, _sio_connected

    if _sio_connected:
        return True

    # Use a lock to prevent race conditions between background thread and main capture calls
    with _connect_lock:
        # Double-check connection state after acquiring lock
        if _sio_connected:
            return True

        if _sio_client is None and not _setup_socketio():
            return False

        # Attempt a range of possible hostnames and IPs
        # Priority: environment variable, then known working IP, then fallbacks
        sio_urls = [
            f"http://{VIDEO_WS_HOST}:{VIDEO_STREAM_PORT}",
            f"http://192.168.30.223:{VIDEO_STREAM_PORT}",  # Known video runner IP
            f"http://127.0.0.1:{VIDEO_STREAM_PORT}",
            f"http://localhost:{VIDEO_STREAM_PORT}",
        ]

        local_ip = _get_local_ip()
        if local_ip:
            sio_urls.append(f"http://{local_ip}:{VIDEO_STREAM_PORT}")
        
        sio_urls.extend([
            f"http://172.17.0.1:{VIDEO_STREAM_PORT}",
            f"http://host.docker.internal:{VIDEO_STREAM_PORT}",
            f"http://unoq.local:{VIDEO_STREAM_PORT}",
        ])

        # Remove duplicates while preserving order
        sio_urls = list(dict.fromkeys(sio_urls))

        for url in sio_urls:
            try:
                # Suppress "Trying Socket.IO" message to reduce noise
                # Try connecting with increased timeout for stability
                # Note: Allowing automatic transport selection (websocket or polling)
                # Suppress websocket-client warning during connection
                
                _sio_client.connect(
                    url, 
                    wait_timeout=20
                )
                time.sleep(1.0) # Give it a moment to stabilize

                if _sio_connected:
                    # Only print on first successful connection to reduce noise
                    print(f"[CAPTURE] ✓ Socket.IO connected to {url} (fresh connection)")
                    return True
            except Exception as e:
                # Handle the "Already connected" case which can happen if state gets out of sync
                # catch both string match and the specific ValueError from python-socketio
                err_str = str(e)
                if "Already connected" in err_str or "disconnected state" in err_str:
                    print(f"[CAPTURE] ! Socket.IO reported '{err_str}' for {url}. State mismatched.")
                    print(f"[CAPTURE] ! Destroying client instance to force fresh start.")
                    try:
                        _sio_client.disconnect()
                    except Exception:
                        pass
                    _sio_client = None
                    _sio_connected = False
                    return False
                
                # Only log real connection failures if we are debugging or every few attempts 
                # to avoid spam, but the user requested logs for this investigation.
                # using a slightly different format to distinguish from the zombie case
                print(f"[CAPTURE] Connection failed to {url}: {e} (Client ID: {id(_sio_client)})")

            finally:
                if not _sio_connected and _sio_client is not None:
                    try:
                        _sio_client.disconnect()
                    except Exception:
                        pass

        # If we get here, all connection attempts failed.
        # Destroy the client instance to ensure we start fresh on the next attempt.
        # This fixes issues where the client gets into a zombie state (e.g. after ConnectionResetError).
        _sio_client = None
        _sio_connected = False
        return False


def capture_frame():
    """Capture a single frame from the video stream via Socket.IO."""
    global _latest_frame, _sio_connected, _latest_frame_time

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

    return None


def _reconnect_loop():
    """Background reconnect loop to recover the video stream when idle."""
    global _sio_initialized, _last_connect_attempt, _sio_connected
    while True:
        now = time.time()
        if not _sio_connected and (now - _last_connect_attempt) >= _reconnect_interval:
            _sio_initialized = True
            _last_connect_attempt = now
            # Suppress reconnection messages to reduce terminal noise
            if _connect_socketio():
                pass  # Connection succeeded silently
            else:
                pass  # Connection failed silently
        time.sleep(1.0)


def _stale_watchdog_loop():
    """Force reconnect if we appear connected but no fresh frames arrive for too long."""
    global _latest_frame, _latest_frame_time, _last_connect_attempt, _sio_connected, _sio_client
    while True:
        time.sleep(STALE_CHECK_INTERVAL)
        now = time.time()
        age = _frame_age(now)
        
        # Don't kill connection if we JUST connected (within STALE_RECONNECT_AGE)
        # This gives time for the first frame to arrive (or HTTP fallback to work)
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
            print(f"[CAPTURE] WATCHDOG: Stale state detected (Frame Age={age:.1f}s, Connected={_sio_connected}, ClientConnected={client_connected_status})")
            print(f"[CAPTURE] WATCHDOG: Forcing aggressive reconnect and destroying client instance.")
            
            try:
                _sio_connected = False
                if _sio_client is not None:
                    _sio_client.disconnect()
            except Exception as e:
                print(f"[CAPTURE] WATCHDOG: Error during disconnect: {e}")
            
            # AGGRESSIVE FIX: Destroy the old client to ensure a fresh clean state
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
    deadline = time.time() + max(0.0, timeout)
    while True:
        frame = capture_frame()
        if frame is not None:
            return frame
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
