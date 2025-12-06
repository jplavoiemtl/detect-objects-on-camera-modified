---
name: watchdog_and_graceful_shutdown
overview: Implement a watchdog timer tied to the detection loop to ensure system health and add graceful shutdown handling for signal interruption.
todos:
  - id: watchdog_state
    content: Implement global last_loop_time and update it in on_detections
    status: completed
  - id: watchdog_check
    content: Update heartbeat function to check last_loop_time before publishing online status
    status: completed
  - id: shutdown_handler
    content: Implement shutdown_handler with LED off and MQTT offline logic
    status: completed
  - id: signal_reg
    content: Register signal handlers for SIGINT and SIGTERM
    status: completed
---

# Watchdog Timer & Graceful Shutdown Plan

I will update `python/main.py` to implement a robust watchdog timer and graceful shutdown mechanism.

## 1. Watchdog Timer Implementation

Currently, the `heartbeat` thread reports "online" regardless of whether the object detection loop is functioning. I will link the heartbeat to the detection callback.

- **Global State**: Add `last_loop_time` global variable initialized to `time.time()`.
- **Update Loop**: In `on_detections(detections)`, update `last_loop_time = time.time()` at the start of the function. This assumes `on_detections` is called for every processed frame (or frequently enough).
- **Heartbeat Logic**:
- Define a `WATCHDOG_THRESHOLD` (e.g., 30 seconds).
- Inside the `heartbeat` loop, check if `time.time() - last_loop_time < WATCHDOG_THRESHOLD`.
- Only publish the "online" status if the loop is active.
- Optionally, publish a "stalled" status if the timeout is exceeded.

## 2. Graceful Shutdown

Ensure the hardware and network state are clean upon termination (Ctrl+C).

- **Signal Handler**:
- Import `signal` and `sys`.
- Create a `shutdown_handler(signum, frame)` function.
- Actions within handler:

1. Turn off LED (`set_led(False)`).
2. Publish MQTT "offline" status.
3. Disconnect MQTT client gracefully.
4. Exit process (`sys.exit(0)`).

- **Registration**: Register the handler for `SIGINT` (Ctrl+C) and `SIGTERM`.

## Verification

- The heartbeat should stop or report error if the video feed freezes.
- Ctrl+C should immediately turn off the LED and update the MQTT status.

**Affected File**: [`python/main.py`](python/main.py)