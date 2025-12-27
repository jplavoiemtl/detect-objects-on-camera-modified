# Investigation Report: Stale Connection and Skipped Saves

## Issue Summary
The software enters a state where it repeatedly detects a "stale connection or frame", forces a reconnect, but then immediately reports "Socket.IO already connected" without actually resuming the video stream. This results in no fresh frames being available for saving when detections occur throughout the day.

## Reviewed Root Cause Analysis & Strategy Update
The user noted that simply restarting the stream might not be enough, as this issue occurs after long periods of operation. The "Socket.IO already connected" message indicates that the internal state of the `socketio.Client` instance has desynchronized from the actual network reality (or our application state).

Trying to "resume" or "fix" this zombie instance is risky. The user correctly suggested a more aggressive approach: **"kill more aggressively ... and then reopen a new one like we do when we start the software"**.

When the software starts, `_sio_client` is `None`, and we create a brand new instance. To solve this persistently, we should enforce this same "fresh start" whenever we detect staleness or connection issues.

## Proposed Remediation Plan

We will implement a **Hard Reset** strategy in two places:

1.  **In the Stale Watchdog (`_stale_watchdog_loop`)**: 
    When staleness is detected (`age > STALE_RECONNECT_AGE`), instead of just calling `disconnect()`, we will also set `_sio_client = None`.
    -   This guarantees that the next reconnect attempt will call `_setup_socketio()`, creating a completely fresh client instance, identical to a cold boot.
    -   *Requires adding `_sio_client` to the global declaration in this function.*

2.  **In the Connection Logic (`_connect_socketio`)**:
    If we *do* encounter the "Already connected" error (e.g., from a race condition or partial recovery), we will treat it as a fatal error.
    -   Instead of setting `_sio_connected = True`, we will destroy the client (`_sio_client = None`) and return `False`.
    -   This forces the retry loop to create a new client on the next pass.

### Changes to `python/capture.py`

#### Modify `_stale_watchdog_loop`
```python
def _stale_watchdog_loop():
    global _latest_frame, _latest_frame_time, _last_connect_attempt, _sio_connected, _sio_client # Added _sio_client
    # ...
    if needs_reconnect:
        print(f"[CAPTURE] Stale connection... forcing reconnect and destroying client")
        try:
            _sio_connected = False
            if _sio_client is not None:
                _sio_client.disconnect()
        except Exception:
            pass
        
        # AGGRESSIVE FIX: Destroy the instance to force full re-creation
        _sio_client = None 
```

#### Modify `_connect_socketio`
```python
        except Exception as e:
            err_str = str(e)
            if "Already connected" in err_str:
                # AGGRESSIVE FIX: Do not trust "Already connected". It's a zombie state.
                print(f"[CAPTURE] Socket.IO reported 'Already connected' in stale state. Destroying client to reset.")
                try:
                    _sio_client.disconnect()
                except:
                    pass
                _sio_client = None # Force new instance
                _sio_connected = False
                return False
            
            # ... other errors
```

## Verification
1.  **Reproduction**: Hard to reproduce deterministically without waiting 24 hours, but can be simulated by manually stopping the runner stream or mocking the "Already connected" state.
2.  **Fix Verification**:
    -   Apply the fix.
    -   Monitor logs. If "Already connected" appears, it should be followed by successful frame reception (clearing the stale state).
    -   The infinite loop of "Stale -> Reconnect -> Already connected" should be broken because the stream will resume.
