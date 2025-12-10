from arduino.app_utils import App, Bridge # type: ignore
from arduino.app_bricks.web_ui import WebUI # type: ignore
from arduino.app_bricks.video_objectdetection import VideoObjectDetection # type: ignore
from threading import Timer

from mqtt_secrets import SERVERMQTT, SERVERPORT, USERNAME, KEY, CLIENT_ID

import time
import json
import paho.mqtt.client as mqtt  # type: ignore
import threading
import signal
import sys
import os
from datetime import datetime
import cv2  # type: ignore
import pytz  # type: ignore

# Timezone configuration - change this to your timezone
LOCAL_TIMEZONE = pytz.timezone('America/Montreal')

# ================= MQTT CONFIG =================
STATUS_TOPIC = "unoq/status"
MQTT_DETECTION_TOPIC = "unoq/detection"

mqtt_client = mqtt.Client(client_id=CLIENT_ID)
mqtt_client.username_pw_set(USERNAME, KEY)

# LWT must be set BEFORE connect
mqtt_client.will_set(
    STATUS_TOPIC,
    json.dumps({"device": CLIENT_ID, "status": "offline"}),
    retain=True
)

mqtt_client.connect(SERVERMQTT, SERVERPORT, 60)
mqtt_client.loop_start()

# Announce online status
mqtt_client.publish(
    STATUS_TOPIC,
    json.dumps({"device": CLIENT_ID, "status": "online"}),
    retain=True
)

print(f"✅ MQTT connected to {SERVERMQTT}:{SERVERPORT} as {CLIENT_ID}")

# ================= HEARTBEAT THREAD =================

def heartbeat():
    while True:
        current_time = time.time()
        detection_age = current_time - last_detection_time
        status = "active" if detection_age <= WATCHDOG_THRESHOLD else "idle"

        mqtt_client.publish(
            STATUS_TOPIC,
            json.dumps({
                "device": CLIENT_ID,
                "status": status,  # active = recent detection; idle = no detection recently
                "timestamp": int(current_time),
                "last_detection_ts": int(last_detection_time),
                "last_detection_age": int(detection_age)
            }),
            retain=True
        )
        print(f"[HEARTBEAT] status={status} last_detection_age={int(detection_age)}s payload_topic={STATUS_TOPIC}")
        time.sleep(60)

# Configuration
DEBOUNCE_SECONDS = 60
DETECTION_CONFIDENCE = 0.6
DETECTION_LABEL = "bottle"     # change this to "bottle", "car", "person", etc.
detected_labels = {DETECTION_LABEL.lower()}
labels_emitted_once = False

# ================= DETECTION HISTORY CONFIG =================
MAX_DETECTION_IMAGES = 40  # Maximum number of saved detection images
DATA_DIR = "data"
IMAGES_DIR = os.path.join("assets", "images")  # Save to assets so WebUI can serve them
LOG_FILE = os.path.join(DATA_DIR, "imageslist.log")

# Detection history state
detection_history = []
next_detection_id = 1


def init_data_directories():
    """Create data directories if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    print(f"✅ Data directories initialized: {DATA_DIR}, {IMAGES_DIR}")


def load_detection_history():
    """Load existing detection history from log file on startup."""
    global detection_history, next_detection_id
    detection_history = []
    
    if not os.path.exists(LOG_FILE):
        print("[HISTORY] No existing log file found, starting fresh")
        return
    
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
        
        if detection_history:
            # Set next ID based on highest existing ID
            max_id = max(entry.get("id", 0) for entry in detection_history)
            next_detection_id = max_id + 1
        
        print(f"✅ Loaded {len(detection_history)} detection records from history")
    except Exception as e:
        print(f"[HISTORY] Error loading log file: {e}")


def save_detection_to_log(entry: dict):
    """Append a detection entry to the log file."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[HISTORY] Error saving to log: {e}")


def rewrite_log_file():
    """Rewrite the entire log file from detection_history (used after rotation)."""
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            for entry in detection_history:
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[HISTORY] Error rewriting log file: {e}")


def delete_oldest_detection():
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
    
    rewrite_log_file()


# Initialize data directories and load history on startup
init_data_directories()
load_detection_history()

# ================= FRAME CAPTURE =================
# Video stream runs on port 4912 via Socket.IO
VIDEO_STREAM_PORT = 4912
VIDEO_WS_HOST = "ei-video-obj-detection-runner"  # Docker container name

import numpy as np  # type: ignore
import base64

# Frame capture state
_sio_initialized = False
_latest_frame = None
_sio_connected = False
_sio_client = None


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
        
        @_sio_client.on('*')
        def catch_all(event, data):
            """Catch all events to find frame data."""
            _process_frame_data(data)
        
        # Common video frame event names
        for event_name in ['frame', 'image', 'video', 'snapshot', 'data', 'stream']:
            @_sio_client.on(event_name)
            def on_frame_event(data, name=event_name):
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
        img_data = None
        
        if isinstance(data, bytes):
            img_data = data
        elif isinstance(data, dict):
            for key in ['frame', 'image', 'data', 'img', 'jpeg', 'jpg', 'png']:
                if key in data:
                    img_data = data[key]
                    break
        elif isinstance(data, str):
            img_data = data
        
        if img_data:
            if isinstance(img_data, str):
                if 'base64,' in img_data:
                    img_data = img_data.split('base64,')[1]
                img_bytes = base64.b64decode(img_data)
            else:
                img_bytes = img_data
            
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is not None:
                _latest_frame = frame
    except Exception:
        pass


def _connect_socketio():
    """Connect to the video stream via Socket.IO."""
    global _sio_client, _sio_connected
    
    if _sio_connected:
        return True
    
    if _sio_client is None:
        if not _setup_socketio():
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
            try:
                _sio_client.disconnect()
            except:
                pass
    
    return False


def capture_frame():
    """Capture a single frame from the video stream via Socket.IO."""
    global _sio_initialized, _latest_frame
    
    # Connect to Socket.IO on first call
    if not _sio_initialized:
        _sio_initialized = True
        print("[CAPTURE] Connecting to video stream via Socket.IO...")
        if _connect_socketio():
            print("[CAPTURE] Socket.IO connection established!")
        else:
            print("[CAPTURE] Socket.IO connection failed")
    
    # Return latest frame if available
    if _latest_frame is not None:
        return _latest_frame.copy()
    
    return None


def capture_and_save_detection(label: str, confidence: float, bbox_xyxy=None):
    """Capture current frame and save as a detection image.

    Optionally draws the provided bounding box (x1, y1, x2, y2) on the frame before saving.
    """
    global next_detection_id
    
    current_time = time.time()
    
    # Capture frame
    frame = capture_frame()
    if frame is None:
        print("[CAPTURE] No frame available, skipping save")
        return None
    
    # Generate timestamped filename using local timezone
    now = datetime.now(LOCAL_TIMEZONE)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    filename = f"detection_{timestamp_str}_{next_detection_id:03d}.jpg"
    filepath = os.path.join(IMAGES_DIR, filename)
    
    # Draw bounding box if provided
    if bbox_xyxy and len(bbox_xyxy) == 4:
        x1, y1, x2, y2 = bbox_xyxy
        # Convert to int and clamp to frame bounds
        h, w = frame.shape[:2]
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w - 1, int(x2)), min(h - 1, int(y2))
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 1)

    # Save image
    try:
        cv2.imwrite(filepath, frame)
    except Exception as e:
        print(f"[CAPTURE] Failed to save image: {e}")
        return None
    
    # Create log entry
    entry = {
        "id": next_detection_id,
        "filename": filename,
        "label": label,
        "confidence": confidence,
        "timestamp": current_time,
        "time_formatted": now.strftime("%d %b %Y, %H:%M:%S").lstrip("0")
    }
    
    # Add to history
    detection_history.append(entry)
    save_detection_to_log(entry)
    
    # Update state
    next_detection_id += 1
    
    # Rotate if needed
    while len(detection_history) > MAX_DETECTION_IMAGES:
        delete_oldest_detection()
    
    print(f"✅ Detection saved: {filename} ({label}, {confidence:.2f})")
    
    # Emit to UI
    emit_detection_saved(entry)
    
    return entry


def emit_detection_saved(entry: dict):
    """Notify UI that a new detection was saved."""
    payload = {
        "entry": entry,
        "total": len(detection_history)
    }
    try:
        ui.send_message("detection_saved", message=payload)
    except Exception as e:
        print(f"[UI] Failed to emit detection_saved: {e}")


def emit_history_list():
    """Send full detection history list to UI."""
    payload = {
        "history": detection_history,
        "total": len(detection_history)
    }
    try:
        ui.send_message("history_list", message=payload)
    except Exception as e:
        print(f"[UI] Failed to emit history_list: {e}")


def emit_threshold():
    """Send current detection confidence threshold to UI."""
    payload = {"value": DETECTION_CONFIDENCE}
    try:
        ui.send_message("threshold", message=payload)
    except Exception as e:
        print(f"[UI] Failed to emit threshold: {e}")


# Components
ui = WebUI()
detection_stream = VideoObjectDetection(confidence=DETECTION_CONFIDENCE, debounce_sec=0.0)
bridge = Bridge()


def handle_confidence_override(_sid, value):
    """Handle confidence override messages from the Web UI."""
    global DETECTION_CONFIDENCE
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        print(f"[UI] Ignoring confidence override (not a number): {value}")
        return

    if not 0.0 <= threshold <= 1.0:
        print(f"[UI] Ignoring confidence override outside [0,1]: {threshold}")
        return

    detection_stream.override_threshold(threshold)
    DETECTION_CONFIDENCE = threshold
    print(f"[UI] Detection confidence updated to {threshold:.2f}")


def emit_detected_labels():
    """Broadcast the current detected label list and selected label to the UI."""
    labels_payload = {
        "labels": sorted(detected_labels),
        "selected": DETECTION_LABEL.lower()
    }
    print(f"[DEBUG] Emitting labels: {labels_payload}")
    try:
        ui.send_message("labels", message=labels_payload)
        print("[DEBUG] Labels emitted successfully")
    except Exception as e:
        print(f"[UI] Failed to emit labels: {e}")


def handle_label_override(_sid, value):
    """Handle label override from UI dropdown."""
    global DETECTION_LABEL
    if not isinstance(value, str):
        print(f"[UI] Ignoring label override (not a string): {value}")
        return

    label = value.strip().lower()
    if not label:
        print("[UI] Ignoring label override (empty)")
        return

    if label not in detected_labels:
        print(f"[UI] Ignoring label override (unknown): {label}")
        return

    DETECTION_LABEL = label
    print(f"[UI] Detection label updated to '{DETECTION_LABEL}'")
    emit_detected_labels()


def handle_labels_request(_sid, _value):
    """Send current detected labels list to requesting client."""
    print(f"[DEBUG] request_labels received from client sid={_sid}")
    emit_detected_labels()


def handle_history_request(_sid, _value):
    """Send detection history list to requesting client."""
    print(f"[DEBUG] request_history received from client sid={_sid}")
    emit_history_list()


def handle_threshold_request(_sid, _value):
    """Send current detection threshold to requesting client."""
    print(f"[DEBUG] request_threshold received from client sid={_sid}")
    emit_threshold()


def handle_image_request(_sid, value):
    """Send specific detection record by index."""
    try:
        index = int(value) if value is not None else -1
    except (TypeError, ValueError):
        index = -1
    
    if not detection_history:
        return
    
    # Handle negative index (from end)
    if index < 0:
        index = len(detection_history) + index
    
    if 0 <= index < len(detection_history):
        entry = detection_history[index]
        payload = {
            "entry": entry,
            "index": index,
            "total": len(detection_history)
        }
        try:
            ui.send_message("image_data", message=payload)
        except Exception as e:
            print(f"[UI] Failed to emit image_data: {e}")

# State
led_on = False
last_detection_time = 0.0
timeout_timer = None
WATCHDOG_THRESHOLD = 90  # Seconds since last detection to consider the system idle

# Start heartbeat after state is initialized to avoid NameError in thread
threading.Thread(target=heartbeat, daemon=True).start()


def set_led(state: bool):
    """Control LED via bridge with error handling."""
    global led_on
    try:
        bridge.call("setLedState", state)
        led_on = state
        print(f"LED {'ON' if state else 'OFF'}")
    except Exception as e:
        print(f"Bridge error: {e}")

def playAnimation():
    """Play the animation via bridge with error handling."""
    try:
        bridge.call("playAnimation")
    except Exception as e:
        print(f"Bridge error: {e}")


def turn_off_led():
    """Timer callback to turn off LED after timeout."""
    if led_on:
        set_led(False)


def schedule_led_timeout():
    """Schedule LED to turn off after DEBOUNCE_SECONDS."""
    global timeout_timer
    if timeout_timer:
        timeout_timer.cancel()
    timeout_timer = Timer(DEBOUNCE_SECONDS, turn_off_led)
    timeout_timer.daemon = True
    timeout_timer.start()


def on_detections(detections: dict):
    """Handle detections: print all objects, turn LED on for bottles, extend timeout on each detection."""
    global last_detection_time
    current_time = time.time()

    det = None

    # Look for the label in any casing (e.g., bottle, Bottle, BOTTLE)
    # Print all detected objects with confidence percentage
    global labels_emitted_once
    previous_len = len(detected_labels)
    for key, value in detections.items():
        confidence_percent = value.get("confidence", 0) * 100
        print(f"{key} (Confidence: {confidence_percent:.1f}%)")

        canonical_label = key.strip().lower()
        if canonical_label:
            detected_labels.add(canonical_label)

        # Keep the first match for the selected detection label
        if det is None and canonical_label == DETECTION_LABEL.lower():
            det = value

    if len(detected_labels) != previous_len or not labels_emitted_once:
        emit_detected_labels()
        labels_emitted_once = True

    if det:
        last_detection_time = current_time
        confidence = det.get("confidence", 0)

        # Turn LED on if not already on
        if not led_on:
            set_led(True)

            # Correct handling for YOLO XYXY format
            bbox_xyxy = det.get("bounding_box_xyxy", [])

            if len(bbox_xyxy) == 4:
                x1, y1, x2, y2 = bbox_xyxy
                bbox = {
                    "x": int(x1),
                    "y": int(y1),
                    "w": int(x2 - x1),
                    "h": int(y2 - y1)
                }
            else:
                bbox = {"x": 0, "y": 0, "w": 0, "h": 0}

            mqtt_payload = {
                "label": DETECTION_LABEL,
                "confidence": confidence,
                "bbox": bbox
            }

            mqtt_client.publish(MQTT_DETECTION_TOPIC, json.dumps(mqtt_payload))
            print(f"✅ MQTT message published to {MQTT_DETECTION_TOPIC}: {DETECTION_LABEL} detected (confidence: {confidence:.2f})")

            # Save detection image at the same time we publish MQTT
            capture_and_save_detection(DETECTION_LABEL, confidence, bbox_xyxy)

            playAnimation()

        # Reset timeout timer – extends delay on each detection
        schedule_led_timeout()


detection_stream.on_detect_all(on_detections)
ui.on_message("override_th", handle_confidence_override)
ui.on_message("override_label", handle_label_override)
ui.on_message("request_labels", handle_labels_request)
ui.on_message("request_history", handle_history_request)
ui.on_message("request_threshold", handle_threshold_request)
ui.on_message("request_image", handle_image_request)
emit_detected_labels()

# ================= GRACEFUL SHUTDOWN =================

def shutdown_handler(signum, frame):
    """Handle shutdown signals to ensure clean exit."""
    print("\n🛑 Shutdown signal received. Cleaning up...")
    
    # Turn off LED
    if led_on:
        set_led(False)
    
    # Publish offline status
    try:
        mqtt_client.publish(
            STATUS_TOPIC,
            json.dumps({"device": CLIENT_ID, "status": "offline"}),
            retain=True
        )
        mqtt_client.disconnect()
        mqtt_client.loop_stop()
        print("✅ MQTT disconnected and offline status sent.")
    except Exception as e:
        print(f"Error during MQTT shutdown: {e}")

    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

App.run()