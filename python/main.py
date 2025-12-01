from arduino.app_utils import App, Bridge  # type: ignore
from arduino.app_bricks.web_ui import WebUI  # type: ignore
from arduino.app_bricks.video_objectdetection import VideoObjectDetection  # type: ignore
from datetime import datetime, UTC
import time
import threading

ui = WebUI()
detection_stream = VideoObjectDetection(confidence=0.6, debounce_sec=0.0)

bridge = Bridge()

LED_ON = False
last_detection = 0.0
LOCK = threading.Lock()
DEBOUNCE_SECONDS = 30

# Register a callback for when all objects are detected
def print_detections(detections: dict):
  global last_detection, LED_ON
  current_time = time.time()
  
  for key, value in detections.items():
    # Only print if it's a bottle and debounce time has passed
    if key.lower() == "bottle":
      with LOCK:
        if current_time - last_detection >= DEBOUNCE_SECONDS:
          confidence_percent = value.get("confidence", 0) * 100
          print(f"Detected: {key} (Confidence: {confidence_percent:.1f}%)")
          last_detection = current_time
          if not LED_ON:
              try:
                  bridge.call("setLedState", True)
                  LED_ON = True
                  # print("LED ON")
              except Exception as e:
                  print("Bridge error:", e)

detection_stream.on_detect_all(print_detections)

# --- Thread that turns off the LED after 10 seconds without detection ---
def led_watcher():
    global LED_ON, last_detection
    while True:
        time.sleep(0.5)
        with LOCK:
            if LED_ON and (time.time() - last_detection > DEBOUNCE_SECONDS):
                try:
                    bridge.call("setLedState", False)
                    LED_ON = False
                    # print("LED OFF (10 s sans personne)")
                except Exception as e:
                    print("Bridge error:", e)

threading.Thread(target=led_watcher, daemon=True).start()

App.run()