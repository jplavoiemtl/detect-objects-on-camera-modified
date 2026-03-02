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

- `capture.py` - Video frame capture via Socket.IO from the video runner container (`ei-video-obj-detection-runner:4912`). Handles reconnection, staleness detection, bbox scaling, and frame retry with immediate reconnect triggering
- `mqtt_client.py` - MQTT client for publishing detection events and device status
- `mqtt_secrets.py` - MQTT credentials (broker IP, port, username, password, client ID)
- `persistence.py` - Detection history storage in `data/imageslist.log` (JSON lines), image rotation, and persistent settings (`data/settings.json`) with debounced atomic writes
- `health_monitor.py` - Watchdog that monitors MQTT connectivity and attempts device reboot if MQTT is down for 5 minutes
- `ui_handlers.py` - WebSocket event handlers for frontend communication

**Arduino App Bricks Used:**

- `WebUI` - Hosts the web interface and Socket.IO transport
- `VideoObjectDetection` - Runs YOLO-based object detection on video frames. Uses `on_detect_all` callback which sends all detections regardless of confidence. Detection values are wrapped in a list of dicts (firmware change). Python-side threshold filtering is applied in `inner_main.py`
- `Bridge` - Controls hardware (LED state, animations)

### Frontend (assets/)

- `index.html` - Main page with video iframe, confidence slider, label dropdown, and detection history navigation
- `app.js` - Socket.IO client handling detection events, history browsing, and UI updates
- `style.css` - Styling (not shown but referenced)

### Key Configuration Constants

In `inner_main.py`:

- `DEBOUNCE_SECONDS = 60` - LED stays on this long after detection
- `_DEFAULT_CONFIDENCE = 0.6` - Default detection threshold (overridden by `data/settings.json` if present)
- `_DEFAULT_LABEL = "bottle"` - Default target object label (overridden by `data/settings.json` if present)
- `LOCAL_TIMEZONE = 'America/Montreal'` - Timestamp timezone

In `capture.py`:

- `VIDEO_STREAM_PORT = 4912` - Video runner Socket.IO port
- `VIDEO_WS_HOST = "ei-video-obj-detection-runner"` - Video runner Docker hostname
- `MODEL_INPUT_SIZE = 416` - YOLO input dimensions for bbox scaling
- `FRESH_RETRY_TOTAL = 5.0` - Seconds to retry frame capture during detection save (triggers immediate reconnect if disconnected)

In `persistence.py`:

- `MAX_DETECTION_IMAGES = 40` - Max saved detection images before rotation
- `SETTINGS_SAVE_DEBOUNCE = 3` - Seconds to wait before writing settings to disk (coalesces rapid changes)
- Detection images saved to `assets/images/` (served by WebUI)
- Log file at `data/imageslist.log`
- Settings file at `data/settings.json` (persists confidence & label across restarts)

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

**Automatic recovery via host cron job**: The main app container is sandboxed and cannot restart sibling containers. Instead, a cron job runs on the Arduino UNO Q host every 2 minutes to verify the video runner is listening on port 4912 and restarts it if not:

```bash
# Installed on host via: ssh arduino@arduino-q.local
# View with: crontab -l
*/2 * * * * docker exec detect-objects-on-camera-modified-ei-video-obj-detection-runner-1 netstat -tuln | grep -q :4912 || docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1 >> /tmp/video_restart.log 2>&1
```

**Note**: The container's built-in Docker healthcheck (`netstat -tuln | grep :5050`) is broken — it checks port 5050 (TCP camera input, not a persistent listener) instead of port 4912 (the actual service port). This causes the container to always show as `(unhealthy)` despite working correctly. The cron job above uses the correct port check and does not rely on Docker's health status. The main app's Socket.IO reconnection logic (`capture.py`) automatically reconnects once the container recovers.

To install/replace the cron job:

```bash
crontab -r
(crontab -l 2>/dev/null; echo '*/2 * * * * docker exec detect-objects-on-camera-modified-ei-video-obj-detection-runner-1 netstat -tuln | grep -q :4912 || docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1 >> /tmp/video_restart.log 2>&1') | crontab -
```

**Manual recovery:**

```bash
docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1
```

**Check restart log:**

```bash
cat /tmp/video_restart.log
```

## Environment Variables

- `VIDEO_RUNNER_PORT` - Override video stream port (default: 4912)
- `VIDEO_RUNNER_HOST` - Override video runner hostname (default: `ei-video-obj-detection-runner`)

## Planning and Documentation Rules

- **All implementation plans must be written to `project_plans/`** in the project root. When creating plans for new features or changes, write them as markdown files in `project_plans/` (e.g., `project_plans/persistent_settings_plan.md`).
- When modifying `task.md`, `implementation_plan.md`, or `walkthrough.md` in internal planning directories, also copy them to `project_plans/`.
