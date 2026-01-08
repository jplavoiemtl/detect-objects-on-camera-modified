# Walkthrough - Connection Stability & Self-Healing Fix

## Problem
The application was experiencing "Max retries exceeded" and "Connection Reset" errors after running for several days.
- **Symptoms**: Logs flooded with connection attempts to 8 different URLs every few seconds.
- **Root Cause**: The external video runner service (Docker container) became unreachable/crashed. The client application exhausted system resources by aggressively retrying connections without waiting.

## Changes Implemented

### 1. Robust Connection Logic ([capture.py](file:///b:/detect-objects-on-camera-modified/python/capture.py))
We moved from a "spray and pray" connection approach to a smart, patient one.
- **Exponential Backoff**: Instead of retrying every 5 seconds forever, the app now waits longer after each failure: `5s -> 10s -> 20s -> 40s -> 60s`.
- **Smart Prioritization**: The app remembers the last working URL (`_last_successful_url`) and tries it first, reducing unnecessary network checks.

### 2. Self-Healing Watchdog
To handle the case where the app gets permanently stuck or the video service needs the app to re-initialize:
- **Mechanism**: A watchdog timer tracks how long the video connection has been offline.
- **Trigger**: If offline for > **5 minutes**.
- **Action**: The app voluntarily "suicides" (`os._exit(1)`).
- **Result**: The process supervisor (Docker/CLI) detects the simple exit and restarts the application fresh.

### 3. Resource Management
- Added explicit cleanup (`disconnect()`) for socket clients to prevent file descriptor leaks during valid restart cycles.

## Verification
- **Backoff**: Verified code paths ensure `current_wait` doubles on failure.
- **Watchdog**: Verified `WATCHDOG_MAX_OFFLINE` is set to 300.0s (5 mins).
- **Commit**: Changes saved to git with message "Fix connection handling with backoff and self-healing watchdog".

The application is now resilient to temporary outages and self-correcting for permanent ones.
