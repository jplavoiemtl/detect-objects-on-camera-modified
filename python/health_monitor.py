import json
import os
import threading
import time

from mqtt_client import (
    CLIENT_ID,
    STATUS_TOPIC,
    get_client,
    is_connected,
    mqtt_connect_with_retry,
    safe_publish,
)

# Software health monitor config
HEALTH_CHECK_INTERVAL = 30          # seconds between health checks
REBOOT_GRACE_SECONDS = 5 * 60       # if no progress for this long AND MQTT down, reboot

# Video runner container name (used by Arduino App Lab)
VIDEO_RUNNER_CONTAINER = "detect-objects-on-camera-modified-ei-video-obj-detection-runner-1"

_health_thread_started = False
last_progress_time = time.time()
last_mqtt_ok = time.time()


def mark_progress(reason: str = ""):
    """Track activity for health decisions."""
    global last_progress_time
    last_progress_time = time.time()
    if reason:
        print(f"[HEALTH] progress: {reason}")


def restart_video_runner_container() -> bool:
    """Restart the video runner Docker container to recover from stuck state.

    Returns True if restart command succeeded, False otherwise.
    """
    print(f"[HEALTH] Attempting to restart video runner container: {VIDEO_RUNNER_CONTAINER}")
    try:
        result = os.system(f"docker restart {VIDEO_RUNNER_CONTAINER}")
        if result == 0:
            print(f"[HEALTH] ✓ Video runner container restarted successfully")
            return True
        else:
            print(f"[HEALTH] ✗ Container restart failed with code {result}")
            return False
    except Exception as e:
        print(f"[HEALTH] ✗ Container restart error: {e}")
        return False


def force_reboot(reason: str):
    """Force a board reboot to recover from a stuck or offline state."""
    print(f"[HEALTH] Rebooting device due to: {reason}")
    # Attempt a clean MQTT offline publish before rebooting
    try:
        safe_publish(
            STATUS_TOPIC,
            json.dumps({"device": CLIENT_ID, "status": "offline"}),
            retain=True,
        )
        get_client().loop_stop()
    except Exception as e:
        print(f"[HEALTH] Error during reboot prep: {e}")

    # Flush filesystem buffers if available
    try:
        os.sync()  # type: ignore[attr-defined]
    except Exception:
        pass

    # Trigger reboot; on failure, exit so system supervisor can restart us
    try:
        os.system("reboot")
        time.sleep(5)
    finally:
        os._exit(1)


def _health_monitor():
    """Periodic health check: try reconnects, and reboot if stuck offline."""
    global last_mqtt_ok
    while True:
        now = time.time()
        stale = now - last_progress_time

        # Try to heal MQTT connectivity if lost
        if is_connected():
            last_mqtt_ok = now
        else:
            print("[HEALTH] MQTT down; attempting reconnect...")
            mqtt_connect_with_retry(max_attempts=2, backoff=2)

        # If we've been stale for too long and still offline, reboot
        offline_duration = now - last_mqtt_ok
        if (stale >= REBOOT_GRACE_SECONDS or offline_duration >= REBOOT_GRACE_SECONDS) and not is_connected():
            reason = f"no-progress-for-{int(stale)}s-and-mqtt-down-{int(offline_duration)}s"
            force_reboot(reason)

        time.sleep(HEALTH_CHECK_INTERVAL)


def start_health_monitor():
    """Start the software health monitoring loop once."""
    global _health_thread_started
    if _health_thread_started:
        return
    _health_thread_started = True
    threading.Thread(target=_health_monitor, daemon=True).start()

