# Implementation Plan - Fix Connection Failures & Add Backoff

## Problem Description
The application loses connection to the video stream after a few days of operation. The logs show `Max retries exceeded` and `ConnectionResetError` when attempting to connect to the video runner service on port 4912. The current implementation retries connection to ~8 different URLs every few seconds, potentially overwhelming the struggling service or exhausting client-side resources (sockets/file descriptors).

## Proposed Changes

### Component: `python/capture.py`

#### [MODIFY] [capture.py](file:///b:/detect-objects-on-camera-modified/python/capture.py)
1.  **Implement Exponential Backoff**:
    - Change `_reconnect_loop` to increase the wait time between retry attempts if they fail.
    - Start at `_reconnect_interval` (5s), multiply by 1.5x or 2x on failure, cap at 60s.
    - Reset to default interval on successful connection.

2.  **Smart URL Prioritization**:
    - Add a global variable `_last_successful_url`.
    - In `_connect_socketio`, if `_last_successful_url` is set, try it **first** before iterating through the full list.
    - Update `_last_successful_url` upon successful connection.

3.  **Enhance Resource Cleanup**:
    - In `_connect_socketio`, when destroying the client, ensure we aggressively clean up.
    - Add logic to explicitly close the `requests.Session` if accessible (via `_sio_client.eio.http`) to prevent socket leaks, although `disconnect()` should handle this.

## Verification Plan

### Manual Verification
1.  **Simulate Failure**: Stop the video runner (if possible) or change the port in code to a wrong one to simulate connection failure.
2.  **Verify Backoff**: Observe terminal output to ensure the "Connection failed" messages appear with increasing time gaps (5s, 10s, 20s...).
3.  **Verify Recovery**: Restore the port/service and verify it reconnects.
4.  **Verify Priority**: Check logs to see if it connects directly to the working URL without trying the others first after a reconnection.

### Automated Tests
- None accessible for this environment (hardware dependent).
