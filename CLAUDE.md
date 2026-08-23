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
- `health_monitor.py` - Watchdog that monitors MQTT connectivity and attempts device reboot if MQTT is down for 5 minutes. Also provides `restart_video_runner_container()`, called by `capture.py` after a sustained stream outage — **this call can never succeed**; the container has no Docker access, and its unbounded retry loop floods the log. See "In-app runner restart cannot work" below
- `ui_handlers.py` - WebSocket event handlers for frontend communication
- `video_recorder.py` - Circular JPEG pre-buffer plus MP4/WebM clip writer for detection videos. The pre-buffer is bounded by **age**, not frame count, and clips are never encoded across a stream outage (see `_trim_to_contiguous`) — both guard against the clip-duration failure documented in `project_plans/video_clip_duration_fix.md`

**Arduino App Bricks Used:**

- `WebUI` - Hosts the web interface and Socket.IO transport
- `VideoObjectDetection` - **Opens the camera in this container** (`/dev/video0`) and pushes JPEG frames over TCP to the runner on port 5050; inference results come back over Socket.IO on 4912. See "Data flow" under Docker Architecture. Runs YOLO-based object detection on video frames. Uses `on_detect_all` callback which sends all detections regardless of confidence. Python-side threshold filtering is applied in `inner_main.py`. **Payload conventions changed with the App Lab SDK update** — two separate changes: (1) detection values are wrapped in a list of dicts, and (2) `bounding_box_xyxy` is now in **source-frame pixels** (640x480), where it was previously model-input space (416x416). The raw bbox is also what gets published to MQTT in `inner_main.py`, so those coordinates changed units too
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
2. **Video runner container** (`detect-objects-on-camera-modified-ei-video-obj-detection-runner-1`) - Edge Impulse inference service on port 4912

### Data flow — the main container owns the camera

This is the **reverse** of what earlier revisions of this file claimed. Verified
2026-08-23 by reading the brick source inside the running container:

```
 main container                                    runner container
 ───────────────────────────────                   ────────────────────────────
 VideoObjectDetection brick
   Camera() ── opens /dev/video0
   camera_loop() ──── JPEG frames ──TCP 5050──▶  gst tcpserversrc ! jpegdec
                                                   └▶ yolo-x inference
 capture.py  ◀────── Socket.IO 4912 ──────────────  serves ws/http on :4912
```

- `arduino/app_bricks/video_objectdetection/__init__.py:63` — `Camera()` opens
  `/dev/video0` (set by `VIDEO_DEVICE` in `.cache/app-compose-overrides.yaml`)
- `:183 camera_loop()` — connects to `runner:5050`, sends a priming black frame,
  then streams JPEG frames captured from the camera
- The runner's actual pipeline is
  `tcpserversrc host=0.0.0.0 port=5050 ! jpegdec`. The
  `gst-launch-1.0 v4l2src device=/dev/video0 ...` lines it prints on failure are
  **example hints in its troubleshooting text**, not what it runs.

Consequences worth remembering:

- `inner_main.py` holding an open fd on `/dev/video0` is **correct and expected** —
  it is the brick doing its job. It is not a leak.
- The runner never touches the camera. A camera problem shows up in the **main**
  container, not the runner.
- Restarting the runner does not restart the camera feed.

### The healthcheck is a readiness gate, not a liveness probe

From the brick's compose
(`/var/lib/arduino-app-cli/assets/0.11.0/compose/arduino/video_object_detection/brick_compose.yaml`):

```yaml
healthcheck:
  test: ["CMD-SHELL", "grep -i ':13BA' /proc/net/tcp | grep ' 0A ' || exit 1"]
  interval: 2s
  retries: 25
```

Paired in the generated `app-compose.yaml` with:

```yaml
main:
  depends_on:
    ei-video-obj-detection-runner:
      condition: service_healthy
```

Its job is to hold `main` back until the runner is ready to **accept** the TCP feed.
For that purpose it is correct. It goes false the moment the feed connects, because
a single-connection TCP server stops listening once it accepts.

So as a **liveness** signal it is exactly inverted, and both readings are verified:

| Runner state | Port 5050 | Docker health |
|---|---|---|
| Streaming normally | `01` ESTABLISHED only | `unhealthy` (failing streak 15533) |
| Crash-looping, no feed | `0A` LISTEN, waiting | **`healthy`** |

Never restart on `health=unhealthy`. The first cron watchdog did exactly that, for
seven weeks. Port 4912 is the real service port and holds a genuine `0A` listener.

### In-app runner restart cannot work

`health_monitor.restart_video_runner_container()` **cannot succeed and never has.**
The main container is sandboxed with no Docker socket, no Docker CLI and no Docker
API. Every fallback fails, every cycle:

```
[HEALTH] Docker socket not found at /var/run/docker.sock
[HEALTH] Docker API at 172.17.0.1:2375 failed: [Errno 111] Connection refused
[HEALTH] Trying docker CLI as last resort...
sh: 1: docker: not found
[HEALTH] ✗ All container restart methods failed
```

Commit `d380c87` (2026-01-10) documented this limitation; a later revision of this
file wrongly claimed the restart works "via the Docker Unix socket". It does not.

Worse, the retry loop is unbounded: on 2026-08-23 it wrote **36,087 log lines in 55
minutes** (3,222 of them `sh: 1: docker: not found`), rotating the main container's
log past the point of the failure it was reacting to and destroying the evidence.

**Manual recovery:**

```bash
docker restart detect-objects-on-camera-modified-ei-video-obj-detection-runner-1
```

### Known failure: /dev/shm exhaustion

The runner writes decoded frames to `multifilesink location=resized%05d.jpg` with a
working directory of `/dev/shm/edge-impulse-cli*` — a **64 MB tmpfs** (Docker's
default `ShmSize`, not overridden by the brick compose).

If the runner is killed abnormally, its frames are left behind and the tmpfs fills.
Every subsequent start then fails on its first write (~70 ms after accepting the
feed) with *"GStreamer stopped before emitting any images"*, leaking one more empty
temp dir per attempt. **The loop is self-sustaining — it never recovers on its own.**

Diagnose:

```bash
docker exec ...-runner-1 df -h /dev/shm        # 100% full is the tell
docker exec ...-runner-1 ls -d /dev/shm/edge-impulse-cli* | wc -l
```

Fix without restarting anything:

```bash
docker exec ...-runner-1 sh -c 'rm -rf /dev/shm/edge-impulse-cli*'
```

The crash loop heals on its next cycle. Full investigation:
`project_plans/video_runner_shm_exhaustion.md`.

### Host watchdogs: two failures, and the rules for any future one

Two host cron watchdogs have caused far more damage than they prevented:

```bash
# v1 — Jan 10 to Mar 1 2026 (commit d380c87) — restarted every 2 min for 7 weeks
*/2 * * * * docker ps --filter "health=unhealthy" -q | grep -q . && docker restart ...

# v2 — Mar 1 to Aug 12 2026 (commit 1abba7a) — REMOVED, DO NOT REINSTATE
*/2 * * * * docker exec ...-runner-1 netstat -tuln | grep -q :4912 || docker restart ...
```

v1 trusted Docker health status, which is inverted (above). v2 was correct until the
App Lab update to brick assets `0.11.0` removed `netstat` from the runner image, so
`docker exec` exited 126 and `||` fired **every single run** — 720 restarts a day,
~28s of downtime out of every 120s, corrupting detection clips for two months. See
`project_plans/video_clip_duration_fix.md`.

Rules for any future watchdog:

- **Bound the blast radius first.** Cap restarts (e.g. 2/hour) with a cooldown and a
  timestamped log. Both failures above were unbounded; a cap would have turned each
  into a nuisance instead of a months-long outage.
- **Probe port 4912 from the host** (`bash -c '</dev/tcp/127.0.0.1/4912'`). It is
  published on the host. Never `docker exec` — the image's toolset changes without
  warning. Never Docker health status.
- **Require several consecutive failures** before acting. Both failures above acted
  on a single probe.
- **Never use `||`** with a command that can fail for reasons other than an unhealthy
  target. `||` fires on exit 126/127 too.

### The watchdog script

`tools/runner_watchdog.sh` implements the rules above. It lives **in the repo**, not
only in `crontab`, because both previous watchdogs existed solely as a crontab line
described in a markdown file — and the description went stale while the job kept
running. Install it from here so the code and its documentation move together.

| Situation | Action |
|---|---|
| Port 4912 answers | Silent, exit 0 |
| Container stopped or absent | Silent, exit 0 — a deliberate `app stop` is not a fault |
| 1-2 consecutive failures | Log `WARN`, do nothing |
| 3 consecutive failures | `docker restart`, record the timestamp |
| Already 2 restarts this hour | Log `BLOCKED`, refuse — needs a human |
| Port recovers | Log `OK`, reset the counter |

State and log live in `~/.local/state/runner-watchdog/`. The log self-trims to 500
lines, deliberately: the in-app recovery loop it replaces wrote 36,087 lines in 55
minutes and destroyed the evidence of the failure it was reacting to.

Install (runs every 2 minutes):

```bash
crontab -e
*/2 * * * * /home/arduino/ArduinoApps/detect-objects-on-camera-modified/tools/runner_watchdog.sh
```

Verify any behaviour without restarting anything — `WATCHDOG_DRY_RUN=1` logs the
action it would have taken, and `WATCHDOG_PORT` points the probe at a dead port:

```bash
WATCHDOG_DRY_RUN=1 WATCHDOG_PORT=59999 WATCHDOG_STATE_DIR=/tmp/wd ./tools/runner_watchdog.sh
```

Tunable via environment: `WATCHDOG_FAIL_THRESHOLD` (3), `WATCHDOG_MAX_RESTARTS` (2),
`WATCHDOG_PORT` (4912), `WATCHDOG_CONTAINER`, `WATCHDOG_STATE_DIR`.

## Environment Variables

- `VIDEO_RUNNER_PORT` - Override video stream port (default: 4912)
- `VIDEO_RUNNER_HOST` - Override video runner hostname (default: `ei-video-obj-detection-runner`)

## Planning and Documentation Rules

- **All implementation plans must be written to `project_plans/`** in the project root. When creating plans for new features or changes, write them as markdown files in `project_plans/` (e.g., `project_plans/persistent_settings_plan.md`).
- When modifying `task.md`, `implementation_plan.md`, or `walkthrough.md` in internal planning directories, also copy them to `project_plans/`.
