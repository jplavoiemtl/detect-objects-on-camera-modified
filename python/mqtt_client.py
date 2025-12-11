import json
import time
from typing import Optional

import paho.mqtt.client as mqtt  # type: ignore

from mqtt_secrets import SERVERMQTT, SERVERPORT, USERNAME, KEY, CLIENT_ID

# MQTT topics
STATUS_TOPIC = "unoq/status"
MQTT_DETECTION_TOPIC = "unoq/detection"

# Internal state
_connected = False
_client: Optional[mqtt.Client] = None
_loop_started = False


def _ensure_client() -> mqtt.Client:
    """Create the MQTT client singleton if needed."""
    global _client
    if _client is None:
        _client = mqtt.Client(client_id=CLIENT_ID)
        _client.username_pw_set(USERNAME, KEY)
        _client.reconnect_delay_set(min_delay=2, max_delay=30)
        _client.will_set(
            STATUS_TOPIC,
            json.dumps({"device": CLIENT_ID, "status": "offline"}),
            retain=True,
        )
        _client.on_connect = _on_connect
        _client.on_disconnect = _on_disconnect
    return _client


def _on_disconnect(_client, _userdata, rc):
    """MQTT disconnect handler to track connectivity."""
    global _connected
    _connected = False
    print(f"[MQTT] disconnected (rc={rc})")


def _on_connect(_client, _userdata, _flags, rc):
    """MQTT connect handler to mark connectivity and republish status."""
    global _connected
    if rc == 0:
        _connected = True
        print(f"✅ MQTT connected to {SERVERMQTT}:{SERVERPORT} as {CLIENT_ID}")
        # Re-announce online status on (re)connect
        safe_publish(
            STATUS_TOPIC,
            json.dumps({"device": CLIENT_ID, "status": "online"}),
            retain=True,
        )
    else:
        _connected = False
        print(f"[MQTT] connection refused (rc={rc})")


def is_connected() -> bool:
    return _connected


def safe_publish(topic: str, payload: str, retain: bool = False) -> bool:
    """Publish with error handling to avoid crashing the main loop."""
    client = _ensure_client()
    try:
        info = client.publish(topic, payload, retain=retain)
        rc = getattr(info, "rc", None)
        if rc is None and isinstance(info, tuple):
            rc = info[0]
        if rc not in (None, mqtt.MQTT_ERR_SUCCESS):
            print(f"[MQTT] publish failed topic={topic}: rc={rc}")
            return False
        return True
    except Exception as e:
        print(f"[MQTT] publish failed topic={topic}: {e}")
        return False


def mqtt_connect_with_retry(max_attempts: int = 3, backoff: int = 2) -> bool:
    """Connect to MQTT broker with basic retry/backoff."""
    global _connected, _loop_started
    client = _ensure_client()

    if not _loop_started:
        try:
            client.loop_start()
            _loop_started = True
        except Exception as e:
            print(f"[MQTT] failed to start loop: {e}")
            return False

    if _connected:
        return True

    for attempt in range(1, max_attempts + 1):
        try:
            if attempt == 1:
                client.connect(SERVERMQTT, SERVERPORT, 60)
            else:
                client.reconnect()

            # Give the network loop a moment to invoke on_connect
            for _ in range(10):
                if _connected:
                    return True
                time.sleep(0.2)
            if _connected:
                return True
            print(f"[MQTT] connect attempt {attempt}/{max_attempts} did not confirm connection")
        except Exception as e:
            print(f"[MQTT] connect attempt {attempt}/{max_attempts} failed: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
    print("[MQTT] giving up after retries; MQTT features will be degraded")
    _connected = False
    return False


def get_client() -> mqtt.Client:
    """Expose the MQTT client instance."""
    return _ensure_client()

