import hashlib
import io
import logging
import socket
import ssl
import struct
import time
from typing import Any, Callable, Dict, Optional, Tuple

from rsm_protocol import (
    CommandType,
    HardwareItem,
    HardwareType,
    SensorItem,
    SensorType,
    TelemetrySnapshot,
)

logger = logging.getLogger("RSMClient")


class RSMClient:
    """
    Client for TRIGONE Remote System Monitor Server (v3.98, protocol 311).
    Connects to port 19150 over TLS, handles two-stage SHA-512 challenge-response,
    and streams live hardware and sensor telemetry.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 19150, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[ssl.SSLSocket] = None
        self.server_version: int = 0
        self.server_uid: str = ""
        self.salt: str = ""
        self.is_authenticated: bool = False

        self.hardware: Dict[str, HardwareItem] = {}
        self.sensors_by_id: Dict[int, SensorItem] = {}
        self.sensors_by_ident: Dict[str, SensorItem] = {}
        self._next_sensor_id: int = 1

    def connect(self) -> None:
        """Establishes TLS connection to the Remote System Monitor server."""
        raw_sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        self.sock = ctx.wrap_socket(raw_sock)

    def _read_exact(self, n: int) -> bytes:
        if not self.sock:
            raise ConnectionError("Socket is not connected")
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError("Connection closed by server while reading bytes")
            buf.extend(chunk)
        return bytes(buf)

    def _read_int32(self) -> int:
        data = self._read_exact(4)
        return struct.unpack("<i", data)[0]

    def _read_prefixed_string(self) -> str:
        length = self._read_int32()
        if length <= 0:
            return ""
        data = self._read_exact(length)
        return data.decode("utf-8", errors="replace")

    def _write_prefixed_string(self, text: str) -> None:
        if not self.sock:
            raise ConnectionError("Socket is not connected")
        data = text.encode("utf-8")
        header = struct.pack("<i", len(data))
        self.sock.sendall(header + data)

    def _send_command(self, cmd_type: CommandType, payload: bytes = b"") -> None:
        """
        Sends command to server.
        Format expected by RSM server reader loop:
        [Int32 total_length = 4 + len(payload)] [Int32 command_id] [payload bytes...]
        """
        if not self.sock:
            raise ConnectionError("Socket is not connected")
        total_len = 4 + len(payload)
        header = struct.pack("<ii", total_len, int(cmd_type))
        self.sock.sendall(header + payload)

    def perform_handshake(self) -> Tuple[int, str, str]:
        """
        Receives greeting from server:
        1. Protocol version (Int32, typically 311)
        2. Server UID (length-prefixed ASCII string)
        3. Challenge Salt (length-prefixed ASCII string)
        """
        self.server_version = self._read_int32()
        self.server_uid = self._read_prefixed_string()
        self.salt = self._read_prefixed_string()
        return self.server_version, self.server_uid, self.salt

    def authenticate(self, password: str) -> bool:
        """
        Authenticates against the server challenge using the 2-stage hash:
        1. intermediate = SHA512(server_uid + password).lower()
        2. auth_hash = SHA512(intermediate + salt).lower()
        """
        if not self.salt or not self.server_uid:
            self.perform_handshake()

        # Step 1: Bind server UID with password
        intermediate = hashlib.sha512((self.server_uid + password).encode("utf-8")).hexdigest().lower()

        # Step 2: Challenge-response with salt
        auth_hash = hashlib.sha512((intermediate + self.salt).encode("utf-8")).hexdigest().lower()
        self._write_prefixed_string(auth_hash)

        status = self._read_int32()
        if status == 2:
            self.is_authenticated = True
            return True
        elif status == 1:
            self.is_authenticated = False
            return False
        else:
            raise ValueError(f"Unexpected authentication response code: {status}")

    def initialize(self, interval_ms: int = 1000) -> None:
        """
        Completes the full RSM registration workflow:
        1. Sets server refresh interval.
        2. Requests initial update packet to receive full hardware & sensor inventory.
        3. Registers integer IDs for all sensors (Command 4).
        4. Activates all sensors for streaming (Command 11).
        """
        if not self.is_authenticated:
            raise PermissionError("Must authenticate before initializing session")

        # 1. Set refresh rate
        self.set_refresh_rate(interval_ms)

        # 2. Trigger initial update to receive dictionary definition
        self.request_update()
        self.read_telemetry_packet()

        # 3. Register sensor IDs and activate them
        self._register_and_activate_sensors()

    def set_refresh_rate(self, interval_ms: int = 1000) -> None:
        """Sets server update interval in milliseconds."""
        payload = struct.pack("<i", interval_ms)
        self._send_command(CommandType.SET_REFRESH_RATE, payload)

    def request_update(self) -> None:
        """Requests telemetry data snapshot (Command 3)."""
        self._send_command(CommandType.UPDATE, b"")

    def _register_and_activate_sensors(self) -> None:
        """Assigns numeric IDs to all sensors and activates them with the server."""
        new_sensors = [s for s in self.sensors_by_ident.values() if s.sensor_id == 0]
        if not new_sensors:
            return

        for s in new_sensors:
            s.sensor_id = self._next_sensor_id
            self.sensors_by_id[s.sensor_id] = s
            self._next_sensor_id += 1

        # Command 4: REGISTER_SENSORS
        cmd4_buf = bytearray(struct.pack("<i", len(new_sensors)))
        for s in new_sensors:
            ident_bytes = s.identifier.encode("utf-8")
            cmd4_buf.extend(struct.pack("<ii", s.sensor_id, len(ident_bytes)))
            cmd4_buf.extend(ident_bytes)
        self._send_command(CommandType.REGISTER_SENSORS, bytes(cmd4_buf))

        # Command 11: ACTIVATE_SENSORS
        all_ids = list(self.sensors_by_id.keys())
        cmd11_buf = bytearray(struct.pack("<i", len(all_ids)))
        for sid in all_ids:
            cmd11_buf.extend(struct.pack("<i", sid))
        self._send_command(CommandType.ACTIVATE_SENSORS, bytes(cmd11_buf))

    def read_telemetry_packet(self) -> TelemetrySnapshot:
        """
        Reads and parses a full telemetry update packet.
        Handles tagged markers:
        - Marker 8: Removed sensors
        - Marker 6: Removed hardware
        - Marker 5: Added hardware
        - Marker 7: Added sensors
        - Marker 3: Sensor metric values
        """
        packet_len = self._read_int32()
        if packet_len <= 0 or packet_len > 20 * 1024 * 1024:
            raise ValueError(f"Invalid packet length: {packet_len}")

        payload = self._read_exact(packet_len)
        stream = io.BytesIO(payload)

        def read_stream_int32() -> int:
            return struct.unpack("<i", stream.read(4))[0]

        def read_stream_string() -> str:
            slen = read_stream_int32()
            if slen <= 0:
                return ""
            return stream.read(slen).decode("utf-8", errors="replace")

        new_sensors_added = False

        while stream.tell() < len(payload):
            m_raw = stream.read(4)
            if len(m_raw) < 4:
                break
            marker = struct.unpack("<i", m_raw)[0]
            cnt = read_stream_int32()

            if marker == 8:  # Removed sensors
                for _ in range(cnt):
                    ident = read_stream_string()
                    s = self.sensors_by_ident.pop(ident, None)
                    if s:
                        self.sensors_by_id.pop(s.sensor_id, None)

            elif marker == 6:  # Removed hardware
                for _ in range(cnt):
                    ident = read_stream_string()
                    self.hardware.pop(ident, None)

            elif marker == 5:  # Added hardware
                for _ in range(cnt):
                    ident = read_stream_string()
                    name = read_stream_string()
                    parent = read_stream_string()
                    hw_type_val = read_stream_int32()
                    hw = HardwareItem(
                        identifier=ident,
                        name=name,
                        parent=parent,
                        hardware_type=HardwareType(hw_type_val)
                        if hw_type_val in HardwareType._value2member_map_
                        else HardwareType.MAINBOARD,
                    )
                    self.hardware[ident] = hw

            elif marker == 7:  # Added sensors
                for _ in range(cnt):
                    ident = read_stream_string()
                    name = read_stream_string()
                    parent = read_stream_string()
                    sensor_type_val = read_stream_int32()
                    stype = (
                        SensorType(sensor_type_val)
                        if sensor_type_val in SensorType._value2member_map_
                        else SensorType.NUMBER
                    )

                    sensor = SensorItem(
                        identifier=ident,
                        name=name,
                        parent=parent,
                        sensor_type=stype,
                    )
                    self.sensors_by_ident[ident] = sensor
                    new_sensors_added = True

            elif marker == 3:  # Sensor readings
                for _ in range(cnt):
                    sensor_id = read_stream_int32()
                    sensor = self.sensors_by_id.get(sensor_id)
                    stype = sensor.sensor_type if sensor else SensorType.LOAD
                    val = self._parse_sensor_value(stream, stype)
                    if sensor:
                        sensor.value = val
            else:
                logger.debug(f"Unknown section marker: {marker}, skipping.")
                break

        # If server introduced dynamic sensors on this update, register & activate them
        if new_sensors_added and self.is_authenticated:
            self._register_and_activate_sensors()

        return TelemetrySnapshot(
            server_uid=self.server_uid,
            timestamp=time.time(),
            hardware=dict(self.hardware),
            sensors=dict(self.sensors_by_id),
            sensors_by_identifier=dict(self.sensors_by_ident),
        )

    def _parse_sensor_value(self, stream: io.BytesIO, sensor_type: SensorType) -> Any:
        try:
            # 64-bit integers
            if sensor_type in (
                SensorType.DATA,
                SensorType.SMALLDATA,
                SensorType.THROUGHPUT,
                SensorType.TIMESPAN,
                SensorType.BATTERY_LIFE_TIME,
                SensorType.BATTERY_LIFE_REMAINING,
                SensorType.NUMBER,
                SensorType.PROCESS_RAM,
            ):
                raw_val = struct.unpack("<q", stream.read(8))[0]
                if sensor_type == SensorType.DATA:
                    return round(raw_val / 1073741824.0, 2)  # Convert bytes to GB
                elif sensor_type == SensorType.SMALLDATA:
                    return round(raw_val / 1048576.0, 2)  # Convert bytes to MB
                elif sensor_type == SensorType.THROUGHPUT:
                    return round(raw_val / 1024.0, 2)  # Convert B/s to KB/s
                elif sensor_type == SensorType.PROCESS_RAM:
                    return round(raw_val / 1048576.0, 2)  # Convert bytes to MB
                return raw_val

            # 32-bit integers
            elif sensor_type in (
                SensorType.FRAME_RATE,
                SensorType.BATTERY_STATUS,
                SensorType.BATTERY_POWER_LINE_STATUS,
                SensorType.HDD_SMART_VALUE,
                SensorType.HDD_SMART_WORST,
                SensorType.HDD_SMART_DATA,
                SensorType.HDD_SMART_THRESHOLD,
                SensorType.NETWORK_DOWNLOAD,
                SensorType.NETWORK_UPLOAD,
            ):
                raw_int = struct.unpack("<i", stream.read(4))[0]
                if sensor_type in (SensorType.NETWORK_DOWNLOAD, SensorType.NETWORK_UPLOAD):
                    return round(raw_int / 1024.0, 2)  # Convert B/s to KB/s
                return raw_int

            # Strings
            elif sensor_type in (
                SensorType.STRING,
                SensorType.NAMED_STRING,
                SensorType.HDD_SMART_STATUS,
                SensorType.PROCESS_NAME,
            ):
                slen = struct.unpack("<i", stream.read(4))[0]
                if slen <= 0:
                    return ""
                raw_str = stream.read(slen).decode("utf-8", errors="replace").strip()
                # Sanitize embedded newlines into single-line representation
                return " - ".join(part.strip() for part in raw_str.splitlines() if part.strip())

            # Floats (temperature, clock, voltage, fan speed, load %, power, flow, control, etc.)
            else:
                raw_float = struct.unpack("<f", stream.read(4))[0]
                if sensor_type == SensorType.CONTROL:
                    # In TRIGONE RSM, values >= 999 represent software/manual control mode (encoded as value + 1000)
                    if raw_float >= 999.0:
                        return round(max(0.0, raw_float - 1000.0), 2)
                return round(raw_float, 2)
        except Exception as e:
            logger.debug(f"Error parsing sensor value for {sensor_type}: {e}")
            return None

    def close(self) -> None:
        """Closes socket connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.is_authenticated = False
