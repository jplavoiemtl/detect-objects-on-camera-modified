# Video Playback UI Plan

## Context

The video recording PoC is complete and verified on the Arduino UNO Q board. Detection clips are saved as MP4 files (mp4v codec, ~2.5MB per 10s clip) with bounding box overlays in `assets/videos/`. The next step is to integrate video playback into the web UI so users can watch detection clips while browsing past detections.

## PoC Results (verified 2026-03-19)

- **Codec**: mp4v (MPEG-4 Part 2) — plays in all browsers including Safari/iOS
- **File size**: ~2.5 MB per 10s clip (vs ~5 MB with MJPEG)
- **Performance**: 75 frames, 8.8s, 8.6fps — well within board capabilities
- **Disk space**: 2.4 GB free on `/home/arduino`, plenty of room
- **Bounding boxes**: Drawn at write time, visible in recorded clips

## Approach: Paired image + video with playback button

Each detection saves both a JPEG image and an MP4 video. The video filename is stored in the detection log entry alongside the image filename. When browsing past detections, a play button appears on images that have an associated video. Clicking it plays the video in the same frame area; when the video ends, it returns to the still image.

## Implementation

### Phase 1: Backend — Associate videos with detection entries

#### 1a. Modify `python/video_recorder.py`

- Change `trigger_recording()` to accept and return a video filename (based on the detection image filename for easy pairing)
- Replace `clip_{timestamp}.mp4` naming with `detection_{timestamp}_{id}.mp4` to match the image naming convention (e.g., image `detection_20260319_104626_826.jpg` pairs with `detection_20260319_104626_826.mp4`)
- Remove the independent `MAX_VIDEO_FILES` rotation — video lifecycle is now managed by `persistence.py` alongside images
- `trigger_recording()` returns the expected video filename immediately so it can be stored in the detection entry before the video finishes writing

#### 1b. Modify `python/persistence.py`

- Add `VIDEOS_DIR` constant (or import from `video_recorder`)
- Update `delete_oldest_detection()` to also delete the associated video file when rotating out old detections
- No changes to `MAX_DETECTION_IMAGES` (stays at 40) — this now governs both images and videos

#### 1c. Modify `python/inner_main.py` and `python/capture.py`

- Pass the detection filename stem to `trigger_recording()` so the video filename matches
- Store `video_filename` in the detection log entry (in `capture_and_save_detection()` or after it returns)
- The video filename is stored in the entry even though the video is still being written — the frontend handles the case where the file isn't ready yet

### Phase 2: Frontend — Video playback in history view

#### 2a. Modify `assets/index.html`

- Add a `<video>` element inside `#videoFeedContainer` (hidden by default), positioned to overlay the saved image area:
  ```html
  <video id="detectionVideo" style="display:none; width:100%; height:100%; object-fit:contain;" playsinline></video>
  ```
- Add a play button overlay on the saved image (similar to the snapshot button):
  ```html
  <button id="btnPlayVideo" title="Play detection video" style="display:none; position:absolute; ...">
      ▶ play icon SVG
  </button>
  ```

#### 2b. Modify `assets/app.js`

- In `showHistoryImage()`: check if the current detection entry has a `video_filename` field. If yes, show the play button; if no, hide it
- `playDetectionVideo()` function:
  1. Set `detectionVideo.src` to `/videos/{video_filename}`
  2. Hide the saved image wrapper, show the video element
  3. Call `detectionVideo.play()`
- On `detectionVideo.ended` event: hide the video element, show the saved image wrapper (return to still image)
- On `detectionVideo.error` event: hide video, show image, show toast "Video not available"
- When navigating to a different detection (prev/next/live): stop and hide any playing video
- Play button visibility follows the same zoom-proof positioning as other overlay buttons

### Phase 3: Cleanup

- Remove `_rotate_videos()` from `video_recorder.py` (rotation handled by `persistence.py`)
- Update `video_recorder.py` configuration comments

## File changes summary

| File | Change |
|---|---|
| `python/video_recorder.py` | Accept filename from caller, remove independent rotation |
| `python/persistence.py` | Delete video alongside image in `delete_oldest_detection()` |
| `python/inner_main.py` | Pass filename to `trigger_recording()`, store `video_filename` in entry |
| `python/capture.py` | Minor: return filename stem for video naming |
| `assets/index.html` | Add `<video>` element and play button |
| `assets/app.js` | Play/stop video logic, button visibility |
| `assets/style.css` | Minimal: video element sizing if needed |

## Detection log entry format (updated)

```json
{
    "id": 826,
    "filename": "detection_20260319_104626_826.jpg",
    "video_filename": "detection_20260319_104626_826.mp4",
    "label": "bottle",
    "confidence": 0.77,
    "timestamp": 1742398986.123,
    "time_formatted": "19 Mar 2026, 10:46:26",
    "bbox_xyxy": [120, 80, 280, 310]
}
```

## Edge cases

| Case | Handling |
|---|---|
| Video still being written when user clicks play | `<video>` error event fires → show toast "Video not ready yet", return to image |
| Detection has no video (recording was skipped/failed) | No `video_filename` in entry → play button hidden |
| Old detections from before video feature | No `video_filename` field → play button hidden (backward compatible) |
| User navigates away while video is playing | Stop and hide video, show new image |
| User switches to live mode while video plays | Stop and hide video, show live feed |
| Video file deleted by rotation but entry still references it | `<video>` error event → toast + return to image |

## Storage estimate

| Items | Calculation | Total |
|---|---|---|
| 40 images | 40 × ~80 KB | ~3 MB |
| 40 videos | 40 × ~2.5 MB | ~100 MB |
| **Total** | | **~103 MB** |

With 2.4 GB free, this uses ~4% of available space.

## Risks

| Risk | Mitigation |
|---|---|
| MP4 files not served correctly by WebUI | Files are in `assets/videos/` which WebUI serves statically — same as images. Verify MIME type is correct |
| Simultaneous video write + playback | Writer runs in background thread; HTTP serving is independent. File may be incomplete if played too early — handled by error event |
| 40 videos too much for storage | ~100 MB is well within 2.4 GB free. Can reduce `MAX_DETECTION_IMAGES` if needed |
| Mobile browser video autoplay restrictions | Using `playsinline` attribute and user-initiated play (button click) — no autoplay issues |
