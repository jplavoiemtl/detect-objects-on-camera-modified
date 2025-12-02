from arduino.app_utils import App, Bridge # type: ignore
from arduino.app_bricks.web_ui import WebUI # type: ignore
from arduino.app_bricks.video_objectdetection import VideoObjectDetection # type: ignore
from threading import Timer

from mqtt_secrets import SERVERMQTT, SERVERPORT, USERNAME, KEY, CLIENT_ID

import time
import json
import paho.mqtt.client as mqtt  # type: ignore
import threading

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
        mqtt_client.publish(
            STATUS_TOPIC,
            json.dumps({
                "device": CLIENT_ID,
                "status": "online",
                "timestamp": int(time.time())
            }),
            retain=True
        )
        time.sleep(30)

threading.Thread(target=heartbeat, daemon=True).start()

# Configuration
DEBOUNCE_SECONDS = 60
DETECTION_CONFIDENCE = 0.6
DETECTION_LABEL = "bottle"     # change this to "bottle", "car", etc.

# Components
ui = WebUI()
detection_stream = VideoObjectDetection(confidence=DETECTION_CONFIDENCE, debounce_sec=0.0)
bridge = Bridge()

# State
led_on = False
last_detection_time = 0.0
timeout_timer = None


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

        # Reset timeout timer – extends delay on each detection
        schedule_led_timeout()


detection_stream.on_detect_all(on_detections)

App.run()