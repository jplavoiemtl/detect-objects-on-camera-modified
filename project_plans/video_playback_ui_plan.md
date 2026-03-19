# Video Playback UI Plan (COMPLETED)

## Context

The video recording PoC is complete and verified on the Arduino UNO Q board. Detection clips are saved in dual format (MP4 + WebM) with bounding box overlays in `assets/videos/`. This plan covers the UI integration for video playback and download.

## Status: Complete ✅ (2026-03-19)

All features implemented and tested on both iPhone (Safari) and desktop (Chrome):
- Play button on saved images with associated videos
- Dual-format video (MP4 for Safari/iOS, WebM for Chrome/Firefox)
- Video download button with native share sheet on iOS
- Unified image + video rotation
- Anti-aliased bounding box overlays with improved text rendering

## Codec Solution

The board's OpenCV cannot encode H.264. Dual-format encoding provides universal browser support:

| Format | Codec | Browser support | File size |
|---|---|---|---|
| MP4 | mp4v (MPEG-4 Part 2) | Safari/iOS | ~2.5 MB |
| WebM | VP80 (VP8) | Chrome/Firefox | ~1.5 MB |

The frontend uses `<source>` elements so each browser picks the format it supports.

## Implementation (completed)

### Phase 1: Backend — Associate videos with detection entries

#### `python/video_recorder.py`
- `trigger_recording()` accepts `video_filename` parameter (matches detection image filename)
- Removed independent `MAX_VIDEO_FILES` rotation and `_rotate_videos()` — lifecycle managed by `persistence.py`
- Dual-format writer: writes both `.mp4` (mp4v) and `.webm` (VP80) in a single pass
- Improved overlay: anti-aliased text (`cv2.LINE_AA`), larger font (0.65 scale, thickness 2)

#### `python/persistence.py`
- Added `VIDEOS_DIR` constant
- `delete_oldest_detection()` deletes both `.mp4` and `.webm` files alongside the image

#### `python/capture.py`
- `capture_and_save_detection()` generates `video_filename` (same stem as image, `.mp4` extension) and includes it in the detection log entry

#### `python/inner_main.py`
- Passes `entry["video_filename"]` to `video_recorder.trigger_recording()`

### Phase 2: Frontend — Video playback and download

#### `assets/index.html`
- Added `<video id="detectionVideo">` element (hidden by default, `playsinline` for iOS)
- Added play button (`#btnPlayVideo`) — top-left, triangle icon, shown only when video exists
- Added video download button (`#btnDownloadVideo`) — film strip icon, in `#imageActions` top-right
- Removed unused share button (`#btnShare`)

#### `assets/app.js`
- `playDetectionVideo()`: uses `<source>` elements for dual-format (WebM first, MP4 fallback)
- `stopDetectionVideo()`: stops video, restores still image view with play/download buttons
- `downloadCurrentVideo()`: fetches blob, uses `navigator.share({ files })` on iOS for native share sheet, falls back to `<a>` download on desktop
- `showHistoryImage()`: toggles play and video download button visibility based on `video_filename`
- Video stops automatically when navigating (prev/next/live) or when playback ends
- Removed `shareCurrentDetection()` and all `btnShare` references

## Detection log entry format

```json
{
    "id": 832,
    "filename": "detection_20260319_142341_832.jpg",
    "video_filename": "detection_20260319_142341_832.mp4",
    "label": "bottle",
    "confidence": 0.79,
    "timestamp": 1742398986.123,
    "time_formatted": "19 Mar 2026, 14:23:41",
    "bbox_xyxy": [120, 80, 280, 310]
}
```

## UI Button Layout (on saved detection images)

| Position | Button | Condition |
|---|---|---|
| Top-left | Play video (triangle) | Shown when `video_filename` exists |
| Top-right | Download image (arrow-down) | Always shown |
| Top-right | Download video (film strip) | Shown when `video_filename` exists |
| Bottom-left | Wake lock (eye icon) | Mobile only |
| Bottom-right | Fullscreen | Always shown |

## Edge cases handled

| Case | Handling |
|---|---|
| Video still being written when user clicks play | `<video>` error event → toast "Video not available", return to image |
| Detection has no video (recording skipped/failed) | No `video_filename` in entry → play and video download buttons hidden |
| Old detections from before video feature | No `video_filename` field → buttons hidden (backward compatible) |
| User navigates away while video is playing | Stop and hide video, show new image |
| User switches to live mode while video plays | Stop and hide video, show live feed |
| Video file deleted by rotation but entry still references it | `<video>` error event → toast + return to image |
| iOS video download | Uses `navigator.share({ files })` for native share sheet with close button |
| Desktop video download | Uses blob URL + `<a>` click for direct file download |

## Storage estimate

| Items | Calculation | Total |
|---|---|---|
| 40 images | 40 × ~80 KB | ~3 MB |
| 40 videos (MP4) | 40 × ~2.5 MB | ~100 MB |
| 40 videos (WebM) | 40 × ~1.5 MB | ~60 MB |
| **Total** | | **~163 MB** |

With 2.4 GB free, this uses ~7% of available space.
