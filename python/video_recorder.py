"""Video recorder — circular buffer + dual-format video writer for detection clips.

Continuously buffers incoming JPEG frames. When a detection triggers
recording, the pre-buffer is snapshotted and post-detection frames are
collected for a few more seconds. The combined clip is written in two
formats (MP4 for Safari/iOS, WebM for Chrome/Firefox) in a background thread.
"""

import base64
import collections
import os
import tempfile
import threading
import time

import cv2
import numpy as np

# --------------- Configuration ---------------
BUFFER_SECONDS = 5          # seconds of video before detection.
POST_SECONDS = 5            # seconds of video after detection
MAX_FPS_ESTIMATE = 15       # ceiling for deque maxlen calculation
VIDEOS_DIR = os.path.join("assets", "videos")

# --------------- Overlay ---------------
OVERLAY_STALE_SEC = 1.0     # bbox overlay expires after this many seconds

# --------------- State ---------------
_buffer = collections.deque(maxlen=BUFFER_SECONDS * MAX_FPS_ESTIMATE)
_recording_active = False
_pre_frames = []            # snapshot of buffer at trigger time
_post_frames = []           # frames collected after trigger
_post_deadline = 0.0        # timestamp when post-collection ends
_recording_filepath = None  # filepath for the current recording
_recording_callback = None  # optional callback for clip capture
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


def trigger_recording(label, confidence, video_filename, callback=None):
    """Start recording a clip. Called from inner_main.py on detection.

    If callback is provided, it's called with (mp4_b64, filename) when the
    clip is ready. Used by capture_clip() for in-memory delivery.
    """
    global _recording_active, _pre_frames, _post_frames, _post_deadline, _recording_filepath, _recording_callback

    filepath = os.path.join(VIDEOS_DIR, video_filename) if not callback else os.path.join(tempfile.gettempdir(), video_filename)

    with _lock:
        if _recording_active:
            # Already recording — skip this trigger
            if callback:
                callback(None, "Recording already in progress")
            return

        # Snapshot the pre-buffer now — before post-collection pushes out old frames
        _pre_frames = list(_buffer)

        # Start post-detection collection
        _post_frames = []
        _post_deadline = time.time() + POST_SECONDS
        _recording_active = True
        _recording_filepath = filepath
        _recording_callback = callback

    print(f"[VIDEO] Recording triggered: {label or 'manual'} ({confidence:.2f}) — {len(_pre_frames)} pre-frames buffered")


def capture_clip(callback):
    """Record 10s of live video starting now and deliver via callback.

    The callback receives (mp4_b64, error). On success mp4_b64 is a base64
    string and error is None. On failure mp4_b64 is None and error is a message.
    """
    global _recording_active, _pre_frames, _post_frames, _post_deadline, _recording_filepath, _recording_callback

    filename = f"clip_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    filepath = os.path.join(tempfile.gettempdir(), filename)

    with _lock:
        if _recording_active:
            callback(None, "Recording already in progress")
            return

        # No pre-buffer — start fresh from now
        _pre_frames = []
        _post_frames = []
        _post_deadline = time.time() + 10  # 10 seconds of new footage
        _recording_active = True
        _recording_filepath = filepath
        _recording_callback = callback

        # DEBUG: buffer state at clip start
        if _buffer:
            newest_age = time.time() - _buffer[-1][0]
            oldest_age = time.time() - _buffer[0][0]
            print(f"[VIDEO DEBUG] Clip start: buffer has {len(_buffer)} frames, newest={newest_age:.3f}s ago, oldest={oldest_age:.3f}s ago")
        else:
            print(f"[VIDEO DEBUG] Clip start: buffer is empty")

    print(f"[VIDEO] Manual clip started — recording 10s from now")


def _finalize_recording():
    """Called with _lock held when post-collection is complete."""
    global _recording_active, _pre_frames, _post_frames, _recording_filepath, _recording_callback

    # Use the pre-buffer snapshot from trigger time (not the current buffer,
    # which has lost old frames during post-collection)
    n_pre = len(_pre_frames)
    n_post = len(_post_frames)
    combined = list(_pre_frames) + list(_post_frames)

    # DEBUG: frame timing analysis
    clip_start_time = _post_deadline - 10 if n_pre == 0 else _post_deadline - POST_SECONDS
    print(f"[VIDEO DEBUG] Finalize: {n_pre} pre + {n_post} post = {len(combined)} total")
    if combined:
        first_ts = combined[0][0]
        last_ts = combined[-1][0]
        print(f"[VIDEO DEBUG] clip_start={clip_start_time:.3f}, first_frame={first_ts:.3f}, last_frame={last_ts:.3f}")
        print(f"[VIDEO DEBUG] first_frame arrived {first_ts - clip_start_time:.3f}s after clip request, span={last_ts - first_ts:.3f}s")

    filepath = _recording_filepath
    callback = _recording_callback
    _pre_frames = []
    _post_frames = []
    _recording_active = False
    _recording_filepath = None
    _recording_callback = None

    if len(combined) < 5:
        print("[VIDEO] Too few frames captured — skipping write")
        if callback:
            callback(None, "Too few frames captured")
        return

    # Hand off to background writer
    threading.Thread(
        target=_write_video,
        args=(combined, filepath, callback),
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
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2, cv2.LINE_AA)
    text = f"{label} {confidence:.0%}"
    cv2.putText(frame, text, (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2, cv2.LINE_AA)


def _write_video(frames, filepath, callback=None):
    """Decode JPEG frames, draw overlays, and write video files.

    For detection recordings (no callback): writes both MP4 and WebM to assets/videos/.
    For manual clips (with callback): writes MP4 to temp, reads bytes, deletes file,
    and delivers base64 via callback.
    Runs in a background thread.
    """
    try:
        # Calculate actual FPS from timestamps
        duration = frames[-1][0] - frames[0][0]
        if duration <= 0:
            print("[VIDEO] Invalid frame timestamps — skipping")
            if callback:
                callback(None, "Invalid frame timestamps")
            return
        fps = len(frames) / duration

        # Decode first frame to get dimensions
        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            print("[VIDEO] Failed to decode first frame — skipping")
            if callback:
                callback(None, "Failed to decode frames")
            return
        h, w = first.shape[:2]

        # For manual clips: MP4 only (temp file, served via WebSocket)
        # For detection recordings: MP4 + WebM (persistent files)
        mp4_path = filepath
        mp4_writer = cv2.VideoWriter(mp4_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))

        writers = []
        if mp4_writer.isOpened():
            writers.append(("mp4", mp4_writer, mp4_path))
        else:
            print("[VIDEO] Failed to open MP4 writer")

        if not callback:
            # Detection recording — also write WebM for Chrome/Firefox
            webm_path = filepath.replace(".mp4", ".webm")
            webm_writer = cv2.VideoWriter(webm_path, cv2.VideoWriter_fourcc(*'VP80'), fps, (w, h))
            if webm_writer.isOpened():
                writers.append(("webm", webm_writer, webm_path))
            else:
                print("[VIDEO] Failed to open WebM writer")

        if not writers:
            print("[VIDEO] No writers available — skipping")
            if callback:
                callback(None, "Failed to create video")
            return

        # Write first frame (with overlay if present)
        _draw_overlay(first, frames[0][2] if len(frames[0]) > 2 else None)
        for _, writer, _ in writers:
            writer.write(first)

        # Decode and write remaining frames one at a time
        for entry in frames[1:]:
            overlay = entry[2] if len(entry) > 2 else None
            frame = cv2.imdecode(np.frombuffer(entry[1], np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                _draw_overlay(frame, overlay)
                for _, writer, _ in writers:
                    writer.write(frame)

        # Release and report
        for fmt, writer, path in writers:
            writer.release()
            file_size = os.path.getsize(path)
            print(f"✅ Video saved: {os.path.basename(path)} ({len(frames)} frames, {duration:.1f}s, {fps:.1f}fps, {file_size / 1024 / 1024:.1f}MB)")

        # For manual clips: read MP4 bytes, delete temp file, deliver via callback
        if callback:
            with open(mp4_path, "rb") as f:
                mp4_b64 = base64.b64encode(f.read()).decode("ascii")
            os.remove(mp4_path)
            callback(mp4_b64, None)

    except Exception as e:
        print(f"[VIDEO] Write error: {e}")
        if callback:
            callback(None, str(e))
