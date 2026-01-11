import http.client
import json
import os
import socket
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


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    """HTTP connection over Unix socket for Docker API."""

    def __init__(self, socket_path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def _restart_via_unix_socket() -> bool:
    """Try to restart container via Docker Unix socket."""
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        print(f"[HEALTH] Docker socket not found at {socket_path}")
        return False

    try:
        conn = UnixSocketHTTPConnection(socket_path, timeout=30)
        conn.request("POST", f"/containers/{VIDEO_RUNNER_CONTAINER}/restart?t=10")
        response = conn.getresponse()
        conn.close()

        if response.status == 204:
            print(f"[HEALTH] ✓ Container restarted via Unix socket (status 204)")
            return True
        elif response.status == 404:
            print(f"[HEALTH] ✗ Container not found: {VIDEO_RUNNER_CONTAINER}")
            return False
        else:
            body = response.read().decode('utf-8', errors='replace')
            print(f"[HEALTH] ✗ Unix socket restart failed: HTTP {response.status} - {body[:200]}")
            return False
    except Exception as e:
        print(f"[HEALTH] ✗ Unix socket error: {e}")
        return False


def _restart_via_docker_host_api() -> bool:
    """Try to restart container via Docker API on host network."""
    docker_hosts = [
        ("172.17.0.1", 2375),      # Default Docker bridge gateway
        ("host.docker.internal", 2375),  # Docker Desktop special DNS
        ("192.168.30.223", 2375),  # Known board IP
    ]

    for host, port in docker_hosts:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("POST", f"/containers/{VIDEO_RUNNER_CONTAINER}/restart?t=10")
            response = conn.getresponse()
            conn.close()

            if response.status == 204:
                print(f"[HEALTH] ✓ Container restarted via {host}:{port} (status 204)")
                return True
            elif response.status == 404:
                print(f"[HEALTH] ✗ Container not found via {host}:{port}")
                continue
        except Exception as e:
            print(f"[HEALTH] Docker API at {host}:{port} failed: {e}")
            continue

    return False


def restart_video_runner_container() -> bool:
    """Restart the video runner Docker container to recover from stuck state.

    Tries multiple methods:
    1. Docker Unix socket (most reliable if mounted)
    2. Docker API via host network (if API exposed)
    3. docker CLI as fallback

    Returns True if restart succeeded, False otherwise.
    """
    print(f"[HEALTH] Attempting to restart video runner container: {VIDEO_RUNNER_CONTAINER}")

    # Method 1: Try Unix socket first (most common)
    if _restart_via_unix_socket():
        return True

    # Method 2: Try Docker API on host
    if _restart_via_docker_host_api():
        return True

    # Method 3: Fall back to CLI (might work if docker is in PATH elsewhere)
    print("[HEALTH] Trying docker CLI as last resort...")
    try:
        result = os.system(f"docker restart {VIDEO_RUNNER_CONTAINER}")
        if result == 0:
            print(f"[HEALTH] ✓ Container restarted via docker CLI")
            return True
        else:
            print(f"[HEALTH] ✗ docker CLI failed with code {result}")
    except Exception as e:
        print(f"[HEALTH] ✗ docker CLI error: {e}")

    print("[HEALTH] ✗ All container restart methods failed")
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

