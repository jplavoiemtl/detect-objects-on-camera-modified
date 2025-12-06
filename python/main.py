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
        time.sleep(60)

# Configuration
DEBOUNCE_SECONDS = 60
DETECTION_CONFIDENCE = 0.6
DETECTION_LABEL = "bottle"     # change this to "bottle", "car", "person", etc.

# Components
ui = WebUI()
detection_stream = VideoObjectDetection(confidence=DETECTION_CONFIDENCE, debounce_sec=0.0)
bridge = Bridge()

# State
led_on = False
last_detection_time = 0.0
timeout_timer = None
WATCHDOG_THRESHOLD = 30  # Seconds since last detection to consider the system idle

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
    for key, value in detections.items():
        confidence_percent = value.get("confidence", 0) * 100
        print(f"{key} (Confidence: {confidence_percent:.1f}%)")        
        if key.lower() == DETECTION_LABEL.lower():
            det = value
            break

    if det:
        last_detection_time = current_time

        # Turn LED on if not already on
        if not led_on:
            set_led(True)

            confidence = det.get("confidence", 0)

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

        # Reset timeout timer – extends delay on each detection
        schedule_led_timeout()


detection_stream.on_detect_all(on_detections)

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