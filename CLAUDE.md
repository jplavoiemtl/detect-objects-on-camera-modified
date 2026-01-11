# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Guidelines

1. **Think first, then read**: Before making any changes, think through the problem and read relevant files in the codebase.

2. **Check in before major changes**: Before making any major changes, check in with the user to verify the plan.

3. **Explain changes at a high level**: At every step, provide a high-level explanation of what changes were made.

4. **Keep it simple**: Make every task and code change as simple as possible. Avoid massive or complex changes. Every change should impact as little code as possible. Simplicity is paramount.

5. **Maintain architecture documentation**: Keep a documentation file that describes how the architecture of the app works inside and out.

6. **Never speculate about unread code**: Never make claims about code you haven't opened. If a specific file is referenced, read it before answering. Investigate and read relevant files BEFORE answering questions about the codebase. Give grounded, hallucination-free answers.

## Project Overview

This is an Arduino UNO Q object detection application that runs on the Arduino App Lab platform. It detects objects from a USB camera feed using the `video_objectdetection` Brick and provides a web-based UI for real-time monitoring.

## Running the Application

The app runs on Arduino UNO Q hardware via Arduino App Lab:
```bash
arduino-app-cli app start user:detect-objects-on-camera-modified
arduino-app-cli app stop user:detect-objects-on-camera-modified
```

Access the web UI at `<board-hostname>.local:7000` (e.g., `arduino-q.local:7000`).

**Note**: Windows users need [Bonjour](https://support.apple.com/kb/DL999) installed for `.local` hostname resolution. Alternatively, add `192.168.30.223 arduino-q.local` to your hosts file.

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
- `health_monitor.py` - Watchdog that monitors MQTT connectivity and attempts device reboot if MQTT is down for 5 minutes
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

## Docker Architecture

The app runs as two Docker containers managed by Arduino App Lab:

1. **Main container** (`detect-objects-on-camera-modified-main-1`) - Runs the Python app on port 7000
2. **Video runner container** (`detect-objects-on-camera-modified-ei-video-obj-detection-runner-1`) - Runs the video/inference service on port 4912

### Video Runner Recovery

The video runner can get stuck in a crash loop (GStreamer failures). When this happens:
- Container shows as `(unhealthy)` in `docker ps`
- WebSocket connections fail with "did not receive a valid HTTP response"
- The `VideoObjectDetection` brick fails to connect

**Automatic recovery via host cron job**: The main app container is sandboxed and cannot restart sibling containers. Instead, a cron job runs on the Arduino UNO Q host every 2 minutes to check for unhealthy containers and restart them:

```bash
# Installed on host via: ssh arduino@arduino-q.local
# View with: crontab -l
*/2 * * * * docker ps --filter "name=detect-objects-on-camera-modified-ei-video-obj-detection-runner-1" --filter "health=unhealthy" -q | grep -q . && docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1 >> /tmp/video_restart.log 2>&1
```

The video runner has a built-in healthcheck (pings port 4912 every 2s, 25 retries). After ~50 seconds of failures, it's marked unhealthy and the cron job restarts it. The main app's Socket.IO reconnection logic (`capture.py`) automatically reconnects once the container recovers.

**Manual recovery**:
```bash
docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1
```

**Check restart log**:
```bash
cat /tmp/video_restart.log
```

## Environment Variables

- `VIDEO_RUNNER_PORT` - Override video stream port (default: 4912)
- `VIDEO_RUNNER_HOST` - Override video runner hostname
- `VIDEO_RUNNER_IP` - Force specific IP when DNS fails

## Artifact Archival Rule

When modifying `task.md`, `implementation_plan.md`, or `walkthrough.md` in internal planning directories, copy them to `project_plans/` directory in the project root.
