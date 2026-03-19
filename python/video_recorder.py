"""Video recorder — circular buffer + MJPEG writer for detection clips.

Continuously buffers incoming JPEG frames. When a detection triggers
recording, the pre-buffer is snapshotted and post-detection frames are
collected for a few more seconds. The combined clip is written to an
AVI file in a background thread.

This is a proof-of-concept module to test feasibility on Arduino UNO Q.
"""

import collections
import os
import threading
import time

import cv2
import numpy as np

# --------------- Configuration ---------------
BUFFER_SECONDS = 5          # seconds of video before detection
POST_SECONDS = 5            # seconds of video after detection
MAX_FPS_ESTIMATE = 15       # ceiling for deque maxlen calculation
MAX_VIDEO_FILES = 5         # rotation limit
VIDEOS_DIR = os.path.join("assets", "videos")

# --------------- Overlay ---------------
OVERLAY_STALE_SEC = 1.0     # bbox overlay expires after this many seconds

# --------------- State ---------------
_buffer = collections.deque(maxlen=BUFFER_SECONDS * MAX_FPS_ESTIMATE)
_recording_active = False
_post_frames = []           # frames collected after trigger
_post_deadline = 0.0        # timestamp when post-collection ends
_lock = threading.Lock()
_current_overlay = None     # (bbox_xyxy, label, confidence, timestamp)


def init():
    """Create videos directory if needed."""
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    print(f"[VIDEO] Recorder initialized — buffer={BUFFER_SECONDS}s, post={POST_SECONDS}s, dir={VIDEOS_DIR}")


def update_overlay(bbox_xyxy, label, confidence):
    """Update the current detection overlay. Called from inner_main.py on every detection."""
    global _current_overlay
    if bbox_xyxy and len(bbox_xyxy) == 4:
        _current_overlay = (list(bbox_xyxy), label, confidence, time.time())


def buffer_frame(jpeg_bytes):
    """Append a JPEG frame to the circular buffer. Called from capture.py on every frame."""
    global _recording_active, _post_deadline

    now = time.time()

    # Attach current overlay if fresh
    overlay = None
    if _current_overlay and (now - _current_overlay[3]) < OVERLAY_STALE_SEC:
        overlay = _current_overlay[:3]  # (bbox_xyxy, label, confidence)

    entry = (now, jpeg_bytes, overlay)

    with _lock:
        _buffer.append(entry)

        # If we're in post-recording phase, also collect into _post_frames
        if _recording_active:
            _post_frames.append(entry)
            if now >= _post_deadline:
                # Post-collection complete — hand off to writer
                _finalize_recording()


def trigger_recording(label, confidence):
    """Start recording a clip. Called from inner_main.py on detection."""
    global _recording_active, _post_frames, _post_deadline

    with _lock:
        if _recording_active:
            # Already recording — skip this trigger
            return

        # Snapshot the pre-buffer
        pre_frames = list(_buffer)

        # Start post-detection collection
        _post_frames = []
        _post_deadline = time.time() + POST_SECONDS
        _recording_active = True

    print(f"[VIDEO] Recording triggered: {label} ({confidence:.2f}) — {len(pre_frames)} pre-frames buffered")


def _finalize_recording():
    """Called with _lock held when post-collection is complete."""
    global _recording_active, _post_frames

    pre_frames = list(_buffer)
    # pre_frames from the deque may overlap with _post_frames since the deque
    # kept appending. Build the full list: everything in pre_frames that has a
    # timestamp before the first post frame, then all post frames.
    if _post_frames:
        post_start_time = _post_frames[0][0]
        combined = [f for f in pre_frames if f[0] < post_start_time] + _post_frames
    else:
        combined = pre_frames

    _post_frames = []
    _recording_active = False

    if len(combined) < 5:
        print("[VIDEO] Too few frames captured — skipping write")
        return

    # Generate filename
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"clip_{ts}.mp4"
    filepath = os.path.join(VIDEOS_DIR, filename)

    # Hand off to background writer
    threading.Thread(
        target=_write_video,
        args=(combined, filepath),
        daemon=True,
        name="video-writer",
    ).start()


def _draw_overlay(frame, overlay):
    """Draw detection bounding box and label on a frame."""
    if not overlay:
        return
    from capture import scale_bbox_to_frame
    bbox_xyxy, label, confidence = overlay
    scaled = scale_bbox_to_frame(bbox_xyxy, frame.shape)
    if not scaled:
        return
    x1, y1, x2, y2 = [int(c) for c in scaled]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    text = f"{label} {confidence:.0%}"
    cv2.putText(frame, text, (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)


def _write_video(frames, filepath):
    """Decode JPEG frames, draw overlays, and write to AVI file. Runs in background thread."""
    try:
        # Calculate actual FPS from timestamps
        duration = frames[-1][0] - frames[0][0]
        if duration <= 0:
            print("[VIDEO] Invalid frame timestamps — skipping")
            return
        fps = len(frames) / duration

        # Decode first frame to get dimensions
        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            print("[VIDEO] Failed to decode first frame — skipping")
            return
        h, w = first.shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(filepath, fourcc, fps, (w, h))

        if not writer.isOpened():
            print(f"[VIDEO] Failed to open VideoWriter for {filepath}")
            return

        # Write first frame (with overlay if present)
        _draw_overlay(first, frames[0][2] if len(frames[0]) > 2 else None)
        writer.write(first)

        # Decode and write remaining frames one at a time
        for entry in frames[1:]:
            _, jpeg_bytes = entry[0], entry[1]
            overlay = entry[2] if len(entry) > 2 else None
            frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                _draw_overlay(frame, overlay)
                writer.write(frame)

        writer.release()

        file_size = os.path.getsize(filepath)
        print(f"✅ Video saved: {os.path.basename(filepath)} ({len(frames)} frames, {duration:.1f}s, {fps:.1f}fps, {file_size / 1024 / 1024:.1f}MB)")

        # Rotate old files
        _rotate_videos()

    except Exception as e:
        print(f"[VIDEO] Write error: {e}")


def _rotate_videos():
    """Delete oldest video files if over the limit."""
    try:
        files = sorted(
            [os.path.join(VIDEOS_DIR, f) for f in os.listdir(VIDEOS_DIR) if f.endswith('.mp4')],
            key=os.path.getmtime,
        )
        while len(files) > MAX_VIDEO_FILES:
            oldest = files.pop(0)
            os.remove(oldest)
            print(f"[VIDEO] Rotated old clip: {os.path.basename(oldest)}")
    except Exception as e:
        print(f"[VIDEO] Rotation error: {e}")
