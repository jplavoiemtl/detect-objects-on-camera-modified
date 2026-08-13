# Bounding Box Misalignment — Investigation and Fix

**Date:** 2026-08-13
**Symptom:** The green bounding box in saved images and recorded clips sat to the
right of the actual bottle and stretched to the bottom edge of the frame — but
only for objects in the upper-left region. Objects on the right rendered fine.
**Root cause:** `scale_bbox_to_frame()` guessed the detector's coordinate space
from coordinate magnitude. The App Lab SDK update changed that space.
**Status:** Fixed; the guess is removed.

---

## 1. Evidence

Measured on detection `20260812_220447_755` (bottle on the left):

| | x | y |
|---|---|---|
| Real bottle (colour-segmented from the saved JPEG) | 198–248 | 215–358 |
| Raw bbox from the brick (recovered by inverting the transform) | **197–257** | **162–363** |
| Green box actually drawn | 303–395 | 169–479 |

The raw bbox matched the physical bottle to within a few pixels (its top at 162
is the white sprayer cap above the green liquid). The drawn box was 105 px to
the right and stretched to the frame bottom.

The same box appeared in both the saved JPEG and the video clip, and it barely
moved across all 98 frames — ruling out overlay staleness or a difference
between the `capture.py` and `video_recorder.py` drawing paths.

## 2. Root cause

`scale_bbox_to_frame()` inferred the coordinate space from magnitude:

```python
elif max_coord <= model_input_size + epsilon:   # 416
    # assume model space, undo the letterbox
```

For a 640x480 frame that computes `scale = 0.65`, `pad_y = 52`, and applies
`x/0.65`, `(y-52)/0.65`. Feeding it the true bbox `[197,162,257,363]` yields
exactly `[303,169,395,479]` — the observed box, to the pixel.

**The guess is unsound in principle.** Model space (0–416) and frame space
(0–640 x 0–480) overlap, so a genuine frame-space box in the upper-left is
indistinguishable from a model-space box. It happened to work only while the
brick actually sent model-space coordinates.

## 3. Why it was position-dependent

The branch triggers only when *every* coordinate is <= 416:

- Bottle left / high in frame -> all coords < 416 -> letterbox applied -> **wrong**
- Bottle right / low in frame -> some coord > 416 -> passthrough -> **correct**

Confirmed live: with the bottle moved to the right, the brick reported
`[428, 62, 536, 410]` and the box rendered correctly around it.

## 4. Confirmed as a regression

Detection `20260603_181237_824` (June 3, before the update): box drawn at
x 403–538, bottle measured at x 468–528 — correctly enclosed, verified visually.
Before the update the brick sent model-space coordinates and the letterbox math
was right. The SDK update switched it to frame pixels.

This is the second payload change from the same update; the first (detection
values wrapped in a list of dicts) was already handled in `inner_main.py`.

## 5. Fix

`capture.py` — removed the model-space branch entirely. Coordinates are now
either normalized `[0,1]` (scaled by frame size) or frame pixels (used as-is).
`model_input_size` is retained in the signature for compatibility but unused.

Do **not** reintroduce magnitude-based detection of the coordinate space.

## 6. Verification

| Case | Input | Output | Expected |
|---|---|---|---|
| Left bottle (raw) | `[197,162,257,363]` | `[197,162,257,363]` | matches bottle at 198–248 / 215–358 |
| Right bottle (raw) | `[428,62,536,410]` | `[428,62,536,410]` | unchanged, was already correct |
| Normalized payload | `[0.3,0.2,0.5,0.7]` | `[192,96,320,336]` | scaled by frame size |
| Out-of-frame coords | `[-20,-5,900,700]` | `[0,0,639,479]` | clamped |
| Degenerate box | `[300,200,300,200]` | `None` | rejected |

Old code for the left-side case produced `[303,169,395,478]` — the bug.

## 7. Side effect worth knowing

`inner_main.py` publishes the **raw** bbox to MQTT (`unoq/detection`), not the
scaled one. Those coordinates silently changed from model space to frame pixels
at the SDK update. Any downstream consumer is now reading different units — as
it happens, the more useful ones.
