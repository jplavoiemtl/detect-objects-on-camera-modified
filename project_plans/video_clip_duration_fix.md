# Video Clip Duration Regression — Investigation and Fix

**Date:** 2026-08-12 / 2026-08-13
**Symptom:** After the Arduino App Lab / UNO Q update, detection clips became
34–42 seconds long instead of ~11s, choppy, and visibly lower quality.
**Root cause:** A host cron job restarting the video runner every 2 minutes.
**Status:** Root cause removed; app hardened so a future outage degrades gracefully.

---

## 1. Evidence

All 40 clips in `assets/videos/` were probed with ffprobe and OpenCV.

| Date | Frames | Written FPS | Duration | pre-roll fps | post-roll fps |
|---|---|---|---|---|---|
| Apr 1 – Jun 3 (34 clips) | 95–111 | 8.0–10.0 | **11.0–11.5s** | 7.2–10.1 | 8.1–10.1 |
| Aug 10 21:10 | 110 | 2.58 | **42.6s** | **0.87** | 10.0 |
| Aug 10 21:14 | 39 | 2.64 | **14.8s** | 4.4 | **1.1** |
| Aug 10 21:35 | 110 | 9.98 | 11.0s | 9.95 | 10.0 |
| Aug 11 07:36 | 109 | 2.58 | **42.2s** | **0.88** | 9.9 |
| Aug 11 09:00 | 107 | 9.55 | 11.2s | 9.4 | 9.6 |
| Aug 12 21:16 | 41 | **1.20** | **34.2s** | 1.14 | 1.4 |

Resolution was unchanged (640x480) throughout. Frame sharpness (Laplacian
variance) fell from 1114–1489 in June to 149–471 in August, and mean luminance
from 113–138 to 39–71.

## 2. Root cause

`docker logs -t` on the runner showed `🚀 Starting EI inference runner...` on
**every even minute at :15–:17 seconds**, without a gap across 4 hours — ~119
consecutive restarts. That periodicity can only come from a scheduler.

The host crontab held:

```bash
*/2 * * * * docker exec ...-runner-1 netstat -tuln | grep -q :4912 || docker restart ...-runner-1
```

Run manually, the check gives:

```
OCI runtime exec failed: exec: "netstat": executable file not found in $PATH
exit=126
```

The App Lab update replaced the runner image (`assets/0.11.0`), and the new image
has no `netstat`. `||` fires on *any* non-zero exit, so the check restarted a
healthy container every 2 minutes. `/tmp/video_restart.log` held **1461** lines
against a 2-day uptime — exactly 30/hour, continuous.

**Per-cycle timeline:** SIGTERM at `:00` → container up `:15` → GStreamer
listening `:22` → camera `:24` → frames resume `:26–:28`. That is **~28s dead out
of every 120s (23%)**, matching the observed `was connected 89.3s`.

## 3. Why that produced 34-second clips

Three amplifiers inside `video_recorder.py`:

1. **Frame-count pre-buffer.** `deque(maxlen=BUFFER_SECONDS * MAX_FPS_ESTIMATE)`
   always held 30 frames regardless of how long they took to arrive. At 9.5 fps
   that is ~3.2s; across a 28s outage the same 30 frames span ~34s. The 8s post
   window is real wall-clock time, so all overshoot came from the pre-roll.
2. **Single averaged fps.** `fps = len(frames) / duration` gives one constant rate
   for a clip whose two halves were captured at wildly different rates. For the
   Aug 10 clip: 110/42.6 = 2.58 fps, so pre-roll frames play ~3x too fast and
   post-detection frames play ~4x slow-motion. This is the "less fluid" symptom.
3. **Splicing.** Frames from before and after an outage were concatenated with no
   gap detection, producing a visible discontinuity (measured frame-to-frame diff
   of 27–56 in bad clips vs 3–7 in good ones).

### Hypotheses tested and rejected

- **Encoder bitrate scaling with fps** — rejected. Re-encoding identical frames at
  9.5 / 2.6 / 1.2 fps produced byte-identical output and identical PSNR
  (40.18 dB). The quality loss is in the source frames (dark, motion-blurred),
  not the writer.
- **Camera auto-exposure lowering frame rate in low light** — rejected. The
  Aug 10 21:10 clip has flat luminance (38→40) across all 110 frames while the
  rate jumps 0.87 → 10.0 fps at the detection. The Aug 10 21:35 clip is equally
  dark (lum 38) and runs a clean 9.95 fps.
- **The confidence slider triggering a model reload** — rejected. Threshold
  changes at 01:16:06 and 01:35:44 were *not* followed by restarts; the next
  restart each time was the scheduled one at `:15`.
- **`capture.py`'s second Socket.IO connection being starved by the new runner** —
  rejected. The brick's own WebSocket and camera TCP socket died in the same 76ms
  window, so all clients were dropped together.

## 4. Fixes applied

**Host (root cause):** crontab removed (`crontab -r`); backup at
`~/crontab.backup` on the board. Verified: no restarts afterwards, heartbeats
report `stream=connected frame_age=0.0–0.1s` continuously.

**`video_recorder.py`**
- Pre-buffer evicted by **age** (`now - BUFFER_SECONDS`), not frame count.
- `_trim_to_contiguous()` drops everything before the last gap exceeding
  `MAX_STREAM_GAP`, so a clip is never encoded across an outage.
- `_finalize_watchdog()` thread forces the write `FINALIZE_GRACE` seconds after
  the post deadline. Previously `_finalize_recording()` ran only from
  `buffer_frame()`, so a stalled stream left `_recording_active = True` forever
  and silently blocked every future recording.

**`capture.py`**
- `disconnect()` sets `_last_connect_attempt = now`, so the retry loop no longer
  burns attempt #1 against a server that is still down (which doubled the backoff
  before recovery was even possible).
- The post-connect wait polls up to 3s instead of assuming a fixed 0.5s, so a
  working connection is not torn down merely for reporting itself late.
- Backoff capped at 20s (was 60s). The runner recovers in ~15s; the old cap left
  the capture path dark long after detections had resumed — the window in which
  a detection produces a corrupted clip.

**`inner_main.py`**
- `[STREAM]` logging now fires on any degraded stream (`fps < LOW_FPS_WARN` or
  any disconnect), plus a routine line every ~5 minutes. The old condition
  required `fps == 0` exactly, which is why a stream delivering frames in bursts
  between outages logged nothing for two months.

**`CLAUDE.md`** — cron-job instructions replaced with an explicit warning, plus a
section on why the container's built-in healthcheck must not be trusted.

> **Corrected 2026-08-13.** An earlier revision of this document claimed the
> built-in healthcheck was fine because port 5050 is a real GStreamer listener.
> That is wrong. GStreamer listens on 5050 only until the camera connects, then
> accepts and stops listening, so the healthcheck's `grep ' 0A '` (LISTEN) never
> matches again. Measured on a healthy runner: port 5050 in state `01`
> (ESTABLISHED) only, failing streak 15533. The container reads `unhealthy` for
> its entire life while working perfectly. The March 2026 diagnosis in commit
> `1abba7a` was right.

## 5. Verification

`scratchpad/test_fixes.py` replays the measured failure shape:

| Test | Result |
|---|---|
| Pre-buffer at 0.87 fps | span 1.15s (was 34.5s) |
| Pre-buffer at 9.5 fps | 20 frames / 2.00s — full pre-roll retained |
| Gap trim on Aug 10 shape | 95 frames/37.4s → 80 frames/7.9s; written fps 2.54 → 10.13 |
| Clean June-3-shaped clip | 107 frames in, 107 out — untouched |

## 6. If a watchdog is ever wanted again

Do **not** use `docker exec`. The runner image has `curl`, `wget`, `python3` but
no `netstat`, `ss`, or `nc`. Port 4912 is published on the host, so test it there:

```bash
*/2 * * * * timeout 5 bash -c '</dev/tcp/127.0.0.1/4912' || docker restart ...-runner-1 >> /tmp/video_restart.log 2>&1
```

Better still, rely on the in-app path: `capture.py` calls
`restart_video_runner_container()` after `WATCHDOG_MAX_OFFLINE` (300s) of genuine
outage, which cannot fire against a healthy container.
