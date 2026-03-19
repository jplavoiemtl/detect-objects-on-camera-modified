# Video Recording PoC Plan (COMPLETED)

## Context

The app saves still JPEG images on detection. We tested whether the Arduino UNO Q hardware can handle recording 10-second video clips (5s before + 5s after detection) using a circular buffer approach.

## Status: PoC Complete ✅ (2026-03-19)

- Recording works reliably on the board
- MP4 format (mp4v codec) verified — plays in all browsers
- Bounding box overlays drawn on frames during write pass
- Files saved to `assets/videos/` (accessible via Samba share)
- Typical clip: 75 frames, ~8.5s, ~9fps, ~2.5 MB

**Next step**: UI integration — see `video_playback_ui_plan.md`

## Codec Test Results

| Codec | Format | Result |
|---|---|---|
| **mp4v** | .mp4 | ✅ Works — universal browser support |
| avc1/H264 | .mp4 | ❌ V4L2 encoder requires nv12 pixel format |
| VP80 | .webm | ✅ Works — no Safari support |
| MJPG | .avi | ✅ Works — no browser playback support |

## Approach: JPEG-bytes circular buffer + MP4 VideoWriter

Store incoming frames as compressed JPEG bytes in a `collections.deque`. On detection, snapshot the buffer (pre-detection frames), collect 5 more seconds of post-detection frames, then write everything to an MP4 file using `cv2.VideoWriter` with mp4v codec in a background thread. Detection bounding boxes are stored alongside frames in the buffer and drawn during the write pass.

**Why this approach:**
- **mp4v codec** works in the board's OpenCV build and plays in all browsers
- **JPEG bytes buffer uses ~5 MB RAM** (75 frames × 50-80KB), vs ~68 MB for raw numpy arrays
- **Minimal CPU overhead** — frames already arrive as JPEG, no encoding needed for the buffer
- **Bounding box overlay** — bbox data attached to buffer entries at capture time, drawn at write time with zero live-pipeline overhead
- **Output files ~2.5 MB** per 10s clip

## Memory estimate

| Item | Calculation | Size |
|---|---|---|
| Circular buffer (5s × 15fps) | 75 frames × ~60KB JPEG | ~4.5 MB |
| Post-detection collection (5s) | 75 frames × ~60KB JPEG | ~4.5 MB |
| Peak during write (decode 1 frame at a time) | 1 × 900KB numpy | ~1 MB |
| **Total peak** | | **~10 MB** |

## Implementation (completed)

### 1. New file: `python/video_recorder.py`

Self-contained module with:

- **`init()`** — creates `assets/videos/` directory
- **`update_overlay(bbox_xyxy, label, confidence)`** — stores latest detection bbox for frame annotation
- **`buffer_frame(jpeg_bytes)`** — appends `(timestamp, jpeg_bytes, overlay)` to `deque(maxlen=75)`
- **`trigger_recording(label, confidence)`** — snapshots pre-buffer, starts collecting post frames for 5s, then hands off to writer thread
- **`_draw_overlay(frame, overlay)`** — scales bbox and draws rectangle + label on frame
- **`_write_video(frames, filepath)`** — background thread decodes JPEGs one-by-one, draws overlays, writes via `cv2.VideoWriter` with mp4v fourcc
- **`_rotate_videos()`** — keeps max 5 video files, deletes oldest

### 2. Modified: `python/capture.py`

In `_process_frame_data()`, after `img_bytes = base64.b64decode(img_data)`:

```python
from video_recorder import buffer_frame
buffer_frame(img_bytes)
```

### 3. Modified: `python/inner_main.py`

- Import `video_recorder` and call `video_recorder.init()` at startup
- Call `video_recorder.update_overlay()` on every detection (for continuous bbox data)
- Call `video_recorder.trigger_recording()` after successful detection save

## Configuration constants

| Constant | Value | Notes |
|---|---|---|
| `BUFFER_SECONDS` | 5 | Pre-detection buffer |
| `POST_SECONDS` | 5 | Post-detection recording |
| `MAX_FPS_ESTIMATE` | 15 | Ceiling for deque maxlen calculation |
| `MAX_VIDEO_FILES` | 5 | Rotation limit (PoC only — UI plan changes this) |
| `VIDEOS_DIR` | `assets/videos` | Served by WebUI, visible in Samba share |
| `OVERLAY_STALE_SEC` | 1.0 | Bbox overlay expires after 1 second |

## Verification results

1. ✅ Deployed and triggered detection
2. ✅ `assets/videos/` contains `.mp4` file
3. ✅ File is 2.6 MB (well under estimates)
4. ✅ Playback verified in Windows — correct duration, smooth framerate, bounding boxes visible
5. ✅ Board handles recording without issues
6. ✅ Rotation works for `.mp4` files
7. ✅ Still image capture continues to work unchanged
