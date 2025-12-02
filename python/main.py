from arduino.app_utils import App, Bridge # type: ignore
from arduino.app_bricks.web_ui import WebUI # type: ignore
from arduino.app_bricks.video_objectdetection import VideoObjectDetection # type: ignore
from threading import Timer
import time

# Configuration
DEBOUNCE_SECONDS = 60
DETECTION_CONFIDENCE = 0.6

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
    """Handle detections: print all objects, turn LED on for bottles, schedule auto-off."""
    global last_detection_time
    current_time = time.time()
    
    # Print all detected objects
    for key, value in detections.items():
        confidence_percent = value.get("confidence", 0) * 100
        print(f"{key} (Confidence: {confidence_percent:.1f}%)")
    
    # Bottle-specific logic
    bottle = detections.get("bottle") or detections.get("Bottle")
    if bottle and current_time - last_detection_time >= DEBOUNCE_SECONDS:
        last_detection_time = current_time
        if not led_on:
            set_led(True)
        schedule_led_timeout()


detection_stream.on_detect_all(on_detections)

App.run()