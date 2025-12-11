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


def _ensure_client() -> mqtt.Client:
    """Create the MQTT client singleton if needed."""
    global _client
    if _client is None:
        _client = mqtt.Client(client_id=CLIENT_ID)
        _client.username_pw_set(USERNAME, KEY)
        _client.will_set(
            STATUS_TOPIC,
            json.dumps({"device": CLIENT_ID, "status": "offline"}),
            retain=True,
        )
        _client.on_disconnect = _on_disconnect
    return _client


def _on_disconnect(_client, _userdata, rc):
    """MQTT disconnect handler to track connectivity."""
    global _connected
    _connected = False
    print(f"[MQTT] disconnected (rc={rc})")


def is_connected() -> bool:
    return _connected


def safe_publish(topic: str, payload: str, retain: bool = False) -> bool:
    """Publish with error handling to avoid crashing the main loop."""
    client = _ensure_client()
    try:
        client.publish(topic, payload, retain=retain)
        return True
    except Exception as e:
        print(f"[MQTT] publish failed topic={topic}: {e}")
        return False


def mqtt_connect_with_retry(max_attempts: int = 3, backoff: int = 2) -> bool:
    """Connect to MQTT broker with basic retry/backoff."""
    global _connected
    client = _ensure_client()
    for attempt in range(1, max_attempts + 1):
        try:
            client.connect(SERVERMQTT, SERVERPORT, 60)
            client.loop_start()
            print(f"✅ MQTT connected to {SERVERMQTT}:{SERVERPORT} as {CLIENT_ID}")
            safe_publish(
                STATUS_TOPIC,
                json.dumps({"device": CLIENT_ID, "status": "online"}),
                retain=True,
            )
            _connected = True
            return True
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

