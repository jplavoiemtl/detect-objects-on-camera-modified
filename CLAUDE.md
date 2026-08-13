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
- `health_monitor.py` - Watchdog that monitors MQTT connectivity and attempts device reboot if MQTT is down for 5 minutes. Also provides `restart_video_runner_container()`, used by `capture.py` after a sustained stream outage
- `ui_handlers.py` - WebSocket event handlers for frontend communication
- `video_recorder.py` - Circular JPEG pre-buffer plus MP4/WebM clip writer for detection videos. The pre-buffer is bounded by **age**, not frame count, and clips are never encoded across a stream outage (see `_trim_to_contiguous`) — both guard against the clip-duration failure documented in `project_plans/video_clip_duration_fix.md`

**Arduino App Bricks Used:**

- `WebUI` - Hosts the web interface and Socket.IO transport
- `VideoObjectDetection` - Runs YOLO-based object detection on video frames. Uses `on_detect_all` callback which sends all detections regardless of confidence. Python-side threshold filtering is applied in `inner_main.py`. **Payload conventions changed with the App Lab SDK update** — two separate changes: (1) detection values are wrapped in a list of dicts, and (2) `bounding_box_xyxy` is now in **source-frame pixels** (640x480), where it was previously model-input space (416x416). The raw bbox is also what gets published to MQTT in `inner_main.py`, so those coordinates changed units too
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
- `LOW_FPS_WARN = 5.0` - Stream fps below this logs a `[STREAM]` warning. A degraded stream must be loud; the previous check only fired at exactly 0 fps and stayed silent for two months

In `capture.py`:

- `VIDEO_STREAM_PORT = 4912` - Video runner Socket.IO port
- `VIDEO_WS_HOST = "ei-video-obj-detection-runner"` - Video runner Docker hostname
- `MODEL_INPUT_SIZE = 416` - YOLO input dimensions. **No longer used for bbox scaling** — the brick reports frame-space pixels, so `scale_bbox_to_frame()` passes them through unchanged. Do not reintroduce magnitude-based guessing between model space and frame space; the two ranges overlap and the guess is wrong ~half the time (see `project_plans/bbox_coordinate_space_fix.md`)
- `FRESH_RETRY_TOTAL = 5.0` - Seconds to retry frame capture during detection save (triggers immediate reconnect if disconnected)

In `video_recorder.py`:

- `BUFFER_SECONDS = 2` - Seconds of pre-detection footage, enforced by frame **age**
- `POST_SECONDS = 8` - Seconds of post-detection footage
- `MAX_FPS_ESTIMATE = 30` - Safety cap on buffer length only; not the eviction policy
- `MAX_STREAM_GAP = 2.0` - Frames spaced wider than this mark a stream outage; everything before the last such gap is dropped rather than spliced into the clip
- `FINALIZE_GRACE = 2.0` - Seconds past the post deadline before the watchdog thread forces the write (a stalled stream must not leave `_recording_active` stuck True)

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

The video runner can get stuck (GStreamer failures). When this happens:

- WebSocket connections fail with "did not receive a valid HTTP response"
- The `VideoObjectDetection` brick fails to connect

**Recovery is handled in-app.** `capture.py` detects a sustained outage and calls
`restart_video_runner_container()` (`health_monitor.py`) after `WATCHDOG_MAX_OFFLINE`
(300s) of no connection, restarting the runner via the Docker Unix socket. Its
Socket.IO client then reconnects automatically.

**Manual recovery:**

```bash
docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1
```

#### ⚠️ Do not install a host cron watchdog

Earlier versions of this file documented a cron job that ran every 2 minutes:

```bash
# REMOVED 2026-08-12 — DO NOT REINSTATE IN THIS FORM
*/2 * * * * docker exec ...-runner-1 netstat -tuln | grep -q :4912 || docker restart ...-runner-1
```

After the Arduino App Lab update to brick assets `0.11.0`, `netstat` was no longer
present in the runner image, so `docker exec` exited 126 and the `||` branch fired
**every single time**. It restarted a perfectly healthy runner 720 times a day
(1461 consecutive restarts were recorded over one 2-day uptime), taking the video
stream down for ~28s out of every 120s. This corrupted detection clips — see
`project_plans/video_clip_duration_fix.md` for the full investigation.

Two lessons encoded here:

- A health check whose *failure mode* is indistinguishable from an unhealthy
  target is worse than no health check. `||` fires on any non-zero exit,
  including "command not found".
- The runner image ships `curl`, `wget` and `python3` but **no** `netstat`, `ss`,
  or `nc`. Port 4912 is published on the host, so any future check should test it
  from the host (`bash -c '</dev/tcp/127.0.0.1/4912'`) rather than via `docker exec`.

Also note: the container's built-in healthcheck probes port **5050**, which is a
real listener — the GStreamer TCP server that accepts the camera feed
(`[GST] In tcp server mode, waiting on 0.0.0.0:5050 for a connection`). An earlier
version of this file wrongly called that healthcheck "broken", which is what
motivated the cron job in the first place.

## Environment Variables

- `VIDEO_RUNNER_PORT` - Override video stream port (default: 4912)
- `VIDEO_RUNNER_HOST` - Override video runner hostname (default: `ei-video-obj-detection-runner`)

## Planning and Documentation Rules

- **All implementation plans must be written to `project_plans/`** in the project root. When creating plans for new features or changes, write them as markdown files in `project_plans/` (e.g., `project_plans/persistent_settings_plan.md`).
- When modifying `task.md`, `implementation_plan.md`, or `walkthrough.md` in internal planning directories, also copy them to `project_plans/`.
