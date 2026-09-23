import argparse
import getpass
import json
import logging
import os
import sys
import time

from mqtt_bridge import MQTTBridge
from rsm_client import RSMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RSM_Test")


def load_config() -> dict:
    cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load config.json: {e}")
    return {}


def format_table(title: str, rows: list) -> str:
    if not rows:
        return ""
    col_widths = [max(len(str(item)) for item in col) for col in zip(*rows)]
    lines = [f"\n=== {title} ==="]
    header = "  ".join(f"{str(item):<{col_widths[i]}}" for i, item in enumerate(rows[0]))
    separator = "  ".join("-" * col_widths[i] for i in range(len(col_widths)))
    lines.append(header)
    lines.append(separator)
    for row in rows[1:]:
        lines.append("  ".join(f"{str(item):<{col_widths[i]}}" for i, item in enumerate(row)))
    return "\n".join(lines)


def main():
    config = load_config()

    parser = argparse.ArgumentParser(description="Test client for Remote System Monitor v3.98 server")
    parser.add_argument("--host", default=config.get("host", "127.0.0.1"), help="Server IP address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=config.get("port", 19150), help="Server port (default: 19150)")
    parser.add_argument("--password", default=config.get("password") or os.environ.get("RSM_PASSWORD"), help="Server password configured in RSM Control Panel")
    parser.add_argument("--interval", type=int, default=config.get("interval_ms", 1000), help="Refresh interval in ms (default: 1000)")
    parser.add_argument("--iterations", type=int, default=1, help="Number of telemetry snapshots to read (default: 1, 0 for infinite stream)")
    parser.add_argument("--mqtt-broker", default=config.get("mqtt_broker"), help="Optional MQTT broker host (e.g. 192.168.1.10)")
    parser.add_argument("--mqtt-port", type=int, default=config.get("mqtt_port", 1883), help="MQTT broker port (default: 1883)")
    parser.add_argument("--mqtt-topic", default=config.get("mqtt_topic", "rsm"), help="Base MQTT topic (default: rsm)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON snapshot instead of formatted table")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    password = args.password
    if not password:
        logger.info(f"Connecting to {args.host}:{args.port} to check server challenge...")
        temp_client = RSMClient(host=args.host, port=args.port)
        try:
            temp_client.connect()
            ver, uid, salt = temp_client.perform_handshake()
            logger.info(f"[+] Server detected! Protocol v{ver}, UID: {uid}")
            temp_client.close()
        except Exception as e:
            logger.error(f"[-] Could not connect to {args.host}:{args.port}: {e}")
            sys.exit(1)

        password = getpass.getpass("Enter Remote System Monitor server password: ")

    client = RSMClient(host=args.host, port=args.port, timeout=10.0)

    # Setup MQTT bridge if requested
    mqtt_bridge = None
    if args.mqtt_broker:
        mqtt_bridge = MQTTBridge(broker=args.mqtt_broker, port=args.mqtt_port, base_topic=args.mqtt_topic)
        mqtt_bridge.connect()

    try:
        logger.info(f"Connecting to {args.host}:{args.port} over TLS...")
        client.connect()

        version, uid, salt = client.perform_handshake()
        logger.info(f"[+] Connected! Protocol Version: {version} | Server UID: {uid}")
        logger.debug(f"Challenge Salt: {salt}")

        logger.info("Authenticating via SHA-512 challenge-response...")
        if not client.authenticate(password):
            logger.error("[-] Authentication failed: Invalid password.")
            sys.exit(1)

        logger.info("[+] Authentication succeeded!")

        # Initialize sensor registry and activate streaming
        logger.info("Discovering hardware components and activating sensor telemetry...")
        client.initialize(interval_ms=args.interval)
        logger.info(f"[+] Initialized! Active sensors: {len(client.sensors_by_id)} across {len(client.hardware)} components.")

        count = 0
        while True:
            # Request next update
            client.request_update()
            snapshot = client.read_telemetry_packet()
            count += 1
            snap_dict = snapshot.to_dict()

            if mqtt_bridge:
                mqtt_bridge.publish_telemetry(snap_dict)
                logger.info(f"[+] Published telemetry snapshot #{count} to MQTT")

            if args.json:
                print(json.dumps(snap_dict, indent=2))
            else:
                rows = [["Hardware Component", "Sensor Name", "Type", "Live Value", "Unit"]]
                # Filter to sensors with non-null values and display them cleanly
                sorted_sensors = sorted(
                    snap_dict.get("sensors", {}).items(),
                    key=lambda x: (x[1].get("hardware", ""), x[1].get("type", ""), x[1].get("name", ""))
                )
                for ident, s in sorted_sensors:
                    val = s.get("value")
                    if val is not None and str(val).strip() != "":
                        rows.append([
                            s.get("hardware", ""),
                            s.get("name", ""),
                            s.get("type", ""),
                            str(val),
                            s.get("unit", "")
                        ])
                print(format_table(f"RSM Live Telemetry Snapshot #{count} ({time.strftime('%X')}) - {len(rows)-1} Active Readings", rows))

            if 0 < args.iterations <= count:
                break

            time.sleep(args.interval / 1000.0)

    except KeyboardInterrupt:
        logger.info("\nStopped by user.")
    except Exception as e:
        logger.error(f"[-] Error: {e}", exc_info=args.debug)
    finally:
        client.close()
        if mqtt_bridge:
            mqtt_bridge.disconnect()


if __name__ == "__main__":
    main()
