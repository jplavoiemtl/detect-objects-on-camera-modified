from arduino.app_utils import App, Bridge  # type: ignore
from arduino.app_bricks.web_ui import WebUI  # type: ignore
from arduino.app_bricks.video_objectdetection import VideoObjectDetection  # type: ignore
from threading import Timer

import time
import json
import threading
import signal
import sys
import os
from datetime import datetime
import pytz  # type: ignore

from mqtt_client import (
    CLIENT_ID,
    MQTT_DETECTION_TOPIC,
    STATUS_TOPIC,
    get_client,
    mqtt_connect_with_retry,
    safe_publish,
)
from persistence import (
    DATA_DIR,
    IMAGES_DIR,
    LOG_FILE,
    MAX_DETECTION_IMAGES,
    delete_oldest_detection,
    init_data_directories,
    load_detection_history,
    rewrite_log_file,
    save_detection_to_log,
)
from capture import capture_and_save_detection, start_capture_reconnect_daemon
from health_monitor import mark_progress, start_health_monitor
from ui_handlers import (
    emit_detected_labels,
    emit_detection_saved,
    emit_history_list,
    emit_threshold,
    handle_confidence_override,
    handle_history_request,
    handle_image_request,
    handle_label_override,
    handle_labels_request,
    handle_threshold_request,
)

# Timezone configuration - change this to your timezone
LOCAL_TIMEZONE = pytz.timezone('America/Montreal')

# Initialize MQTT connection
mqtt_connect_with_retry()

# ================= HEARTBEAT THREAD =================

def heartbeat():
    while True:
        current_time = time.time()
        detection_age = current_time - last_detection_time
        status = "active" if detection_age <= WATCHDOG_THRESHOLD else "idle"

        timestamp_str = datetime.now(LOCAL_TIMEZONE).strftime("%d %b %Y, %H:%M:%S")

        safe_publish(
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
        print(f"{timestamp_str} [HEARTBEAT] status={status} last_detection_age={int(detection_age)}s payload_topic={STATUS_TOPIC}")
        mark_progress("heartbeat")
        time.sleep(60)

# Configuration
DEBOUNCE_SECONDS = 60
DETECTION_CONFIDENCE = 0.6
DETECTION_LABEL = "bottle"     # change this to "bottle", "car", "person", etc.
detected_labels = {DETECTION_LABEL.lower()}
labels_emitted_once = False

# Detection history state
detection_history = []
next_detection_id = 1

# Initialize data directories and load history on startup
init_data_directories()
detection_history, next_detection_id = load_detection_history()

# Components
ui = WebUI()
detection_stream = VideoObjectDetection(confidence=DETECTION_CONFIDENCE, debounce_sec=0.0)
bridge = Bridge()

# State
led_on = False
last_detection_time = 0.0
timeout_timer = None
WATCHDOG_THRESHOLD = 90  # Seconds since last detection to consider the system idle

# Start heartbeat after state is initialized to avoid NameError in thread
threading.Thread(target=heartbeat, daemon=True).start()
start_health_monitor()
start_capture_reconnect_daemon()


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
    global last_detection_time, next_detection_id
    current_time = time.time()

    det = None

    def normalize_detection_value(val):
        """Return (confidence, bbox_xyxy) for mixed payload shapes."""
        if isinstance(val, dict):
            confidence_val = val.get("confidence", val.get("score", 0.0))
            bbox_xyxy = val.get("bounding_box_xyxy") or val.get("bbox") or []
        elif isinstance(val, (int, float)):
            confidence_val = float(val)
            bbox_xyxy = []
        else:
            confidence_val = 0.0
            bbox_xyxy = []
        return float(confidence_val), bbox_xyxy

    # Look for the label in any casing (e.g., bottle, Bottle, BOTTLE)
    # Print all detected objects with confidence percentage
    global labels_emitted_once
    previous_len = len(detected_labels)
    try:
        for key, value in detections.items():
            confidence_val, bbox_xyxy = normalize_detection_value(value)
            confidence_percent = confidence_val * 100
            print(f"{key} (Confidence: {confidence_percent:.1f}%)")

            canonical_label = key.strip().lower()
            if canonical_label:
                detected_labels.add(canonical_label)

            # Keep the first match for the selected detection label
            if det is None and canonical_label == DETECTION_LABEL.lower():
                det = {
                    "confidence": confidence_val,
                    "bounding_box_xyxy": bbox_xyxy
                }
    except Exception as e:
        print(f"[DETECTION] Error parsing detections: {e}")

    if len(detected_labels) != previous_len or not labels_emitted_once:
        emit_detected_labels(ui, detected_labels, DETECTION_LABEL)
        labels_emitted_once = True

    if det:
        last_detection_time = current_time
        confidence = det.get("confidence", 0)
        mark_progress("detection")

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

            if safe_publish(MQTT_DETECTION_TOPIC, json.dumps(mqtt_payload)):
                print(f"✅ MQTT message published to {MQTT_DETECTION_TOPIC}: {DETECTION_LABEL} detected (confidence: {confidence:.2f})")
            else:
                print(f"[MQTT] Failed to publish detection for {DETECTION_LABEL}")

            # Save detection image at the same time we publish MQTT
            entry, next_detection_id = capture_and_save_detection(
                DETECTION_LABEL,
                confidence,
                bbox_xyxy,
                detection_history=detection_history,
                next_detection_id=next_detection_id,
                timezone=LOCAL_TIMEZONE,
            )
            if entry:
                emit_detection_saved(ui, detection_history, entry)

            playAnimation()

        # Reset timeout timer – extends delay on each detection
        schedule_led_timeout()


detection_stream.on_detect_all(on_detections)
ui.on_message(
    "override_th",
    lambda sid, val: handle_confidence_override(
        detection_stream,
        lambda v: globals().__setitem__("DETECTION_CONFIDENCE", v),
        sid,
        val,
    ),
)
ui.on_message(
    "override_label",
    lambda sid, val: handle_label_override(
        detected_labels,
        lambda v: globals().__setitem__("DETECTION_LABEL", v),
        sid,
        val,
        lambda: emit_detected_labels(ui, detected_labels, DETECTION_LABEL),
    ),
)
ui.on_message(
    "request_labels",
    lambda sid, val: handle_labels_request(
        lambda: emit_detected_labels(ui, detected_labels, DETECTION_LABEL),
        sid,
        val,
    ),
)
ui.on_message(
    "request_history",
    lambda sid, val: handle_history_request(
        lambda: emit_history_list(ui, detection_history), sid, val
    ),
)
ui.on_message(
    "request_threshold",
    lambda sid, val: handle_threshold_request(
        lambda: emit_threshold(ui, DETECTION_CONFIDENCE), sid, val
    ),
)
ui.on_message(
    "request_image",
    lambda sid, val: handle_image_request(ui, detection_history, sid, val),
)
emit_detected_labels(ui, detected_labels, DETECTION_LABEL)

# ================= GRACEFUL SHUTDOWN =================

def shutdown_handler(signum, frame):
    """Handle shutdown signals to ensure clean exit."""
    print("\n🛑 Shutdown signal received. Cleaning up...")
    
    # Turn off LED
    if led_on:
        set_led(False)
    
    # Publish offline status
    try:
        safe_publish(
            STATUS_TOPIC,
            json.dumps({"device": CLIENT_ID, "status": "offline"}),
            retain=True
        )
        client = get_client()
        client.disconnect()
        client.loop_stop()
        print("✅ MQTT disconnected and offline status sent.")
    except Exception as e:
        print(f"Error during MQTT shutdown: {e}")

    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

App.run()