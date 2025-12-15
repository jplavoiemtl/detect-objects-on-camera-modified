import base64
import math
import os
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from persistence import (
    IMAGES_DIR,
    MAX_DETECTION_IMAGES,
    delete_oldest_detection,
    save_detection_to_log,
)

VIDEO_STREAM_PORT = 4912
VIDEO_WS_HOST = "ei-video-obj-detection-runner"  # Docker container name
MODEL_INPUT_SIZE = 416  # YOLO input dimension used by the Brick

# Frame capture state
_sio_initialized = False
_latest_frame = None
_sio_connected = False
_sio_client = None
_last_connect_attempt = 0.0
_reconnect_interval = 5.0
_reconnector_started = False


def _setup_socketio():
    """Set up Socket.IO client for video stream."""
    global _sio_client, _sio_connected, _latest_frame

    try:
        import socketio  # type: ignore

        _sio_client = socketio.Client(logger=False, engineio_logger=False)

        @_sio_client.event
        def connect():
            global _sio_connected
            _sio_connected = True
            print("[CAPTURE] ✓ Socket.IO connected to video stream")

        @_sio_client.event
        def disconnect():
            global _sio_connected
            _sio_connected = False
            print("[CAPTURE] Socket.IO disconnected")

        @_sio_client.on("*")
        def catch_all(event, data):
            """Catch all events to find frame data."""
            _process_frame_data(data)

        return True
    except ImportError:
        print("[CAPTURE] Socket.IO client not available")
        return False
    except Exception as e:
        print(f"[CAPTURE] Socket.IO setup error: {e}")
        return False


def _process_frame_data(data):
    """Process incoming frame data from Socket.IO."""
    global _latest_frame
    try:
        if not isinstance(data, dict):
            return

        # Find image data using any common key
        img_data = next(
            (data[k] for k in ["frame", "image", "data", "img", "jpeg", "jpg", "png"] if k in data),
            None
        )
        
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
    except Exception:
        pass


def _connect_socketio():
    """Connect to the video stream via Socket.IO."""
    global _sio_client, _sio_connected

    if _sio_connected:
        return True

    if _sio_client is None and not _setup_socketio():
        return False

    sio_urls = [
        f"http://{VIDEO_WS_HOST}:{VIDEO_STREAM_PORT}",
        f"http://172.17.0.1:{VIDEO_STREAM_PORT}",
        f"http://localhost:{VIDEO_STREAM_PORT}",
    ]

    for url in sio_urls:
        try:
            print(f"[CAPTURE] Trying Socket.IO: {url}")
            _sio_client.connect(url, wait_timeout=3)
            time.sleep(0.5)

            if _sio_connected:
                return True
        except Exception as e:
            print(f"[CAPTURE] Socket.IO connection failed for {url}: {type(e).__name__}")
        finally:
            if not _sio_connected:
                try:
                    _sio_client.disconnect()
                except Exception:
                    pass

    return False


def capture_frame():
    """Capture a single frame from the video stream via Socket.IO."""
    global _sio_initialized, _latest_frame, _last_connect_attempt

    now = time.time()

    # Connect to Socket.IO on first call or retry periodically if disconnected
    should_attempt = (not _sio_initialized) or (
        not _sio_connected and (now - _last_connect_attempt >= _reconnect_interval)
    )
    if should_attempt:
        _sio_initialized = True
        _last_connect_attempt = now
        print("[CAPTURE] Connecting to video stream via Socket.IO...")
        if _connect_socketio():
            print("[CAPTURE] Socket.IO connection established!")
        else:
            print("[CAPTURE] Socket.IO connection failed")

    # Return latest frame if available
    if _latest_frame is not None:
        return _latest_frame.copy()

    return None


def _reconnect_loop():
    """Background reconnect loop to recover the video stream when idle."""
    global _sio_initialized, _last_connect_attempt
    while True:
        now = time.time()
        if not _sio_connected and (now - _last_connect_attempt) >= _reconnect_interval:
            _sio_initialized = True
            _last_connect_attempt = now
            print("[CAPTURE] Background reconnect attempt to video stream...")
            if _connect_socketio():
                print("[CAPTURE] Socket.IO reconnection succeeded")
            else:
                print("[CAPTURE] Socket.IO reconnection failed")
        time.sleep(1.0)


def start_capture_reconnect_daemon(reconnect_interval: float = 5.0):
    """Start background reconnect attempts to keep the video stream alive."""
    global _reconnect_interval, _reconnector_started
    if _reconnector_started:
        return
    _reconnector_started = True
    _reconnect_interval = max(1.0, reconnect_interval)
    threading.Thread(target=_reconnect_loop, daemon=True).start()


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

    # Capture frame
    frame = frame if frame is not None else capture_frame()
    if frame is None:
        print("[CAPTURE] No frame available, skipping save")
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

