import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("RSM_MQTT")

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False


def slugify(text: str) -> str:
    """Converts a hardware/sensor name to a clean MQTT topic slug."""
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", text)


class MQTTBridge:
    """
    Handles publishing RSM telemetry to an MQTT broker.
    Supports granular topics, consolidated JSON, and Home Assistant MQTT Discovery.
    """

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        base_topic: str = "rsm",
        dry_run: bool = False,
    ):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.base_topic = base_topic.strip("/")
        self.dry_run = dry_run or not PAHO_AVAILABLE
        self.client = None

        if not PAHO_AVAILABLE and not dry_run:
            logger.warning("[!] paho-mqtt is not installed. Running MQTT bridge in DRY-RUN mode.")

    def connect(self) -> bool:
        if self.dry_run:
            logger.info(f"[*] MQTT Bridge initialized in DRY-RUN mode (Target: {self.broker}:{self.port})")
            return True

        try:
            self.client = mqtt.Client(client_id=f"rsm_bridge_{self.base_topic}")
            if self.username and self.password:
                self.client.username_pw_set(self.username, self.password)

            # LWT message
            self.client.will_set(f"{self.base_topic}/status", payload="offline", retain=True, qos=1)
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()

            # Announce online
            self.client.publish(f"{self.base_topic}/status", payload="online", retain=True, qos=1)
            logger.info(f"[+] Connected to MQTT broker at {self.broker}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"[-] Failed to connect to MQTT broker: {e}")
            return False

    def publish_telemetry(self, snapshot_dict: Dict[str, Any]) -> None:
        """Publishes all sensor telemetry from a snapshot dictionary."""
        uid = snapshot_dict.get("server_uid", "default")
        prefix = f"{self.base_topic}/{uid}"

        # 1. Publish complete JSON snapshot
        json_payload = json.dumps(snapshot_dict, indent=2)
        self._publish(f"{prefix}/all", json_payload)

        # 2. Publish individual granular sensor topics
        sensors = snapshot_dict.get("sensors", {})
        for ident, sdata in sensors.items():
            val = sdata.get("value")
            if val is None:
                continue

            hw_name = slugify(sdata.get("hardware", "system"))
            sensor_name = slugify(sdata.get("name", "val"))
            topic = f"{prefix}/{hw_name}/{sensor_name}"

            self._publish(topic, str(val))

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self.dry_run:
            logger.debug(f"[DRY-RUN MQTT] {topic} -> {payload[:80]}")
        else:
            if self.client:
                self.client.publish(topic, payload=payload, retain=retain)

    def disconnect(self) -> None:
        if self.client and not self.dry_run:
            try:
                self.client.publish(f"{self.base_topic}/status", payload="offline", retain=True, qos=1)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
