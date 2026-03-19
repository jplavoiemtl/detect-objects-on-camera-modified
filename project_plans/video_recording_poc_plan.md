# Video Recording PoC Plan

## Context

The app currently saves still JPEG images on detection. We want to test whether the Arduino UNO Q hardware can handle recording 10-second video clips (5s before + 5s after detection) using a circular buffer approach. This is a proof of concept — no UI changes, just backend recording to verify feasibility.

## Approach: JPEG-bytes circular buffer + MJPEG VideoWriter

Store incoming frames as compressed JPEG bytes in a `collections.deque`. On detection, snapshot the buffer (pre-detection frames), collect 5 more seconds of post-detection frames, then write everything to an AVI file using `cv2.VideoWriter` with MJPEG codec in a background thread.

**Why this approach:**
- **MJPEG codec is guaranteed** to work in any OpenCV build — no risk of silent failure on the board
- **JPEG bytes buffer uses ~5 MB RAM** (75 frames × 50-80KB), vs ~68 MB for raw numpy arrays
- **Minimal CPU overhead** — frames already arrive as JPEG, no encoding needed for the buffer
- **Output files ~5-8 MB** per 10s clip — acceptable for PoC (H.264 can be tested later as a one-line fourcc change)

## Memory estimate

| Item | Calculation | Size |
|---|---|---|
| Circular buffer (5s × 15fps) | 75 frames × ~60KB JPEG | ~4.5 MB |
| Post-detection collection (5s) | 75 frames × ~60KB JPEG | ~4.5 MB |
| Peak during write (decode 1 frame at a time) | 1 × 900KB numpy | ~1 MB |
| **Total peak** | | **~10 MB** |

## Implementation

### 1. New file: `python/video_recorder.py` (~130 lines)

Self-contained module with:

- **`init()`** — creates `data/videos/` directory
- **`buffer_frame(jpeg_bytes)`** — appends `(timestamp, jpeg_bytes)` to `deque(maxlen=75)`
- **`trigger_recording(label, confidence)`** — snapshots pre-buffer, starts collecting post frames for 5s, then hands off to writer thread
- **`_write_video(frames, filename)`** — background thread decodes JPEGs one-by-one, writes via `cv2.VideoWriter` with MJPG fourcc, rotates old files
- **`_rotate_videos()`** — keeps max 5 video files, deletes oldest

Key design:
- Recording debounce: ignores new triggers while a recording is in progress
- FPS calculated from actual frame timestamps (handles variable frame rate)
- Writer thread is `daemon=True` so it won't block app shutdown
- Frames decoded one at a time during write to avoid holding all numpy arrays in memory

### 2. Modify: `python/capture.py` (~3 lines)

At line 204 in `_process_frame_data()`, after `img_bytes = base64.b64decode(img_data)`:

```python
from video_recorder import buffer_frame
buffer_frame(img_bytes)
```

This hooks into the existing frame pipeline with zero overhead — the JPEG bytes are already in memory at that point.

### 3. Modify: `python/inner_main.py` (~8 lines)

- Import `video_recorder` and call `video_recorder.init()` at startup
- After successful `capture_and_save_detection()` (line ~280), call `video_recorder.trigger_recording(label, confidence)`

### Files unchanged

- `persistence.py`, `ui_handlers.py`, `health_monitor.py` — no changes
- All frontend files — no UI changes per requirements

## Configuration constants

| Constant | Value | Notes |
|---|---|---|
| `BUFFER_SECONDS` | 5 | Pre-detection buffer |
| `POST_SECONDS` | 5 | Post-detection recording |
| `MAX_FPS_ESTIMATE` | 15 | Ceiling for deque maxlen calculation |
| `MAX_VIDEO_FILES` | 5 | Rotation limit (~25-40 MB max disk) |
| `VIDEOS_DIR` | `data/videos` | Not served by WebUI (internal only) |

## Risks

| Risk | Mitigation |
|---|---|
| Board runs out of RAM | JPEG buffer (~10 MB peak) is safe; decode one frame at a time during write |
| Frame drops during video write | Writer runs in background daemon thread; detection pipeline only reads `_latest_frame` |
| Disk fills up | 5-file rotation limits to ~40 MB max |
| MJPG files too large for storage | Follow-up: change fourcc to `avc1` for H.264 (one-line change) |
| Variable FPS causes playback issues | FPS calculated from actual timestamps of captured frames |

## Verification

1. Deploy and trigger a detection
2. Check `data/videos/` for an `.avi` file
3. Verify file is non-zero and ~5-8 MB
4. Copy off board, verify playback: correct duration (~10s), smooth framerate, frames in order
5. Monitor `docker stats` during recording for memory usage
6. Trigger 6+ detections to verify rotation deletes oldest clips
7. Confirm existing still image capture still works unchanged
