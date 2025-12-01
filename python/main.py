from arduino.app_utils import App  # type: ignore
from arduino.app_bricks.web_ui import WebUI  # type: ignore
from arduino.app_bricks.video_objectdetection import VideoObjectDetection  # type: ignore
from datetime import datetime, UTC
import time

ui = WebUI()
detection_stream = VideoObjectDetection(confidence=0.6, debounce_sec=0.0)

# Track last bottle detection time for debouncing
last_bottle_detection_time = 0
DEBOUNCE_SECONDS = 30

# Register a callback for when all objects are detected
def print_detections(detections: dict):
  global last_bottle_detection_time
  current_time = time.time()
  
  for key, value in detections.items():
    # Only print if it's a bottle and debounce time has passed
    if key.lower() == "bottle":
      if current_time - last_bottle_detection_time >= DEBOUNCE_SECONDS:
        confidence_percent = value.get("confidence", 0) * 100
        print(f"Detected: {key} (Confidence: {confidence_percent:.1f}%)")
        last_bottle_detection_time = current_time

detection_stream.on_detect_all(print_detections)

App.run()