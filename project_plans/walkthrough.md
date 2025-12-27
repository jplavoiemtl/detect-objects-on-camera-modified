# Verification: Aggressive Stale Connection Fix

## Changes Applied
I have modified `python/capture.py` to implement a "Nuclear Option" for connection recovery.

### 1. Watchdog "Hard Reset"
In `_stale_watchdog_loop`, when the frame age exceeds 30 seconds:
- **Old Behavior**: Disconnected the client but kept the instance.
- **New Behavior**: **Destroys** the `_sio_client` instance (`_sio_client = None`). This forces `_setup_socketio()` to run again on the next attempt, creating a fresh environment.
- **Added Logs**: 
  ```text
  [CAPTURE] WATCHDOG: Stale state detected (Frame Age=...s, Connected=..., ClientConnected=...)
  [CAPTURE] WATCHDOG: Forcing aggressive reconnect and destroying client instance.
  ```

### 2. Connection "Zombie" Prevention
In `_connect_socketio`, when `_sio_client.connect()` raises "Already connected":
- **Old Behavior**: Assumed success and set `_sio_connected = True`.
- **New Behavior**: Identifies this as a state mismatch, **destroys** the client instance, and returns failure. This ensures we never use a "zombie" connection that thinks it's connected but isn't receiving data.
- **Added Logs**:
  ```text
  [CAPTURE] ! Socket.IO reported 'Already connected' ... State mismatched.
  [CAPTURE] ! Destroying client instance to force fresh start.
  ```

### 3. Connection Debugging & Configuration
- **Disabled Auto-Reconnection**: Set `reconnection=False` in `socketio.Client` to avoid race conditions.
- **Removed Output Suppression**: Removed `_SuppressOutput` context manager. It was modifying `sys.stdout` globally, causing **all** detection logs from the main thread to be swallowed whenever the background thread was connecting.
- **Added Visibility**: 
  - Logs `[CAPTURE] ! Socket.IO disconnect event received` to trace disconnects.
  - Logs `[CAPTURE] ✓ Socket.IO connected to video stream` (from event handler) should now be visible during connection.

## How to Verify
1.  **Detection Logs**: You should now definitively see `✅ Detection saved: ...` in the terminal when a detection occurs, even if the connection is unstable.
2.  **Connection Output**: You will see more "Trying Socket.IO" or similar logs from the library itself, which were previously suppressed. This is expected and helpful for now.
3.  **Stability**: Monitor if the "loop" persists or if seeing the full logs reveals an underlying error (like a handshake failure).


## Rollback Plan
If this causes instability (e.g., memory leaks from creating too many clients — though Python's GC should handle it), revert the changes to `capture.py`.
