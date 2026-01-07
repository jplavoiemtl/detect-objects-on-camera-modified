# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an Arduino UNO Q object detection application that runs on the Arduino App Lab platform. It detects objects from a USB camera feed using the `video_objectdetection` Brick and provides a web-based UI for real-time monitoring.

## Running the Application

The app runs on Arduino UNO Q hardware via Arduino App Lab:
```bash
arduino-app-cli app start user:detect-objects-on-camera-modified
arduino-app-cli app stop user:detect-objects-on-camera-modified
```

Access the web UI at `<board-hostname>.local:7000` (e.g., `unoq.local:7000`).

## Architecture

### Backend (Python)

**Entry Points:**
- `python/main.py` - Supervisor wrapper that auto-restarts `inner_main.py` on crash (exit code 1)
- `python/inner_main.py` - Main application logic

**Core Modules:**
- `capture.py` - Video frame capture via Socket.IO from the video runner service (port 4912). Handles reconnection, staleness detection, and bbox scaling
- `mqtt_client.py` - MQTT client for publishing detection events and device status
- `mqtt_secrets.py` - MQTT credentials (broker IP, port, username, password, client ID)
- `persistence.py` - Detection history storage in `data/imageslist.log` (JSON lines) and image rotation
- `health_monitor.py` - Watchdog that reboots the device if MQTT is down and no progress for 5 minutes
- `ui_handlers.py` - WebSocket event handlers for frontend communication

**Arduino App Bricks Used:**
- `WebUI` - Hosts the web interface and Socket.IO transport
- `VideoObjectDetection` - Runs YOLO-based object detection on video frames
- `Bridge` - Controls hardware (LED state, animations)

### Frontend (assets/)

- `index.html` - Main page with video iframe, confidence slider, label dropdown, and detection history navigation
- `app.js` - Socket.IO client handling detection events, history browsing, and UI updates
- `style.css` - Styling (not shown but referenced)

### Key Configuration Constants

In `inner_main.py`:
- `DEBOUNCE_SECONDS = 60` - LED stays on this long after detection
- `DETECTION_CONFIDENCE = 0.6` - Default detection threshold
- `DETECTION_LABEL = "bottle"` - Target object label
- `LOCAL_TIMEZONE = 'America/Montreal'` - Timestamp timezone

In `capture.py`:
- `VIDEO_STREAM_PORT = 4912` - Video runner Socket.IO port
- `MODEL_INPUT_SIZE = 416` - YOLO input dimensions for bbox scaling
- `WATCHDOG_MAX_OFFLINE = 300` - Seconds before video stream watchdog triggers reboot

In `persistence.py`:
- `MAX_DETECTION_IMAGES = 40` - Max saved detection images before rotation
- Detection images saved to `assets/images/` (served by WebUI)
- Log file at `data/imageslist.log`

### WebSocket Events (Frontend <-> Backend)

**Backend to Frontend:**
- `detection_saved` - New detection captured with image
- `history_list` - Full detection history array
- `labels` - Available detection labels and current selection
- `threshold` - Current confidence threshold
- `image_data` - Single detection record by index

**Frontend to Backend:**
- `override_th` - Change detection confidence threshold
- `override_label` - Change target detection label
- `request_labels`, `request_history`, `request_threshold`, `request_image` - Data requests

### MQTT Topics

- `unoq/status` - Device heartbeat (online/offline/active/idle)
- `unoq/detection` - Detection events with label, confidence, bbox

## Environment Variables

- `VIDEO_RUNNER_PORT` - Override video stream port (default: 4912)
- `VIDEO_RUNNER_HOST` - Override video runner hostname
- `VIDEO_RUNNER_IP` - Force specific IP when DNS fails

## Artifact Archival Rule

When modifying `task.md`, `implementation_plan.md`, or `walkthrough.md` in internal planning directories, copy them to `project_plans/` directory in the project root.
