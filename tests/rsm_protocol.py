import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Any


class HardwareType(IntEnum):
    MAINBOARD = 0
    SUPERIO = 1
    CPU = 2
    RAM = 3
    GPUNVIDIA = 4
    GPUATI = 5
    HDD = 6
    NETWORK_NOT_USED = 7
    COOLER = 8
    EMBEDDED_CONTROLLER = 9
    PSU = 10
    GPUINTEL = 11
    DIMM = 12
    PROCESS = 100
    PROCESS_ITEM = 101
    NETWORK = 102
    HDD_IO = 103
    COMPUTER = 1024
    COMPUTER_WIN_XP = 1025
    COMPUTER_WIN_VISTA = 1026
    COMPUTER_WIN_SEVEN = 1027
    COMPUTER_WIN_10 = 1028

    @classmethod
    def get_name(cls, value: int) -> str:
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN_{value}"


class SensorType(IntEnum):
    VOLTAGE = 0
    CLOCK = 1
    TEMPERATURE = 2
    LOAD = 3
    FREQUENCY = 4
    FAN = 5
    FLOW = 6
    CONTROL = 7
    LEVEL = 8
    FACTOR = 9
    POWER = 10
    DATA = 11
    SMALLDATA = 12
    THROUGHPUT = 13
    CURRENT = 14
    TIMESPAN = 15
    ENERGY = 16
    NOISE = 17
    CONDUCTIVITY = 18
    HUMIDITY = 19
    TIMING = 20
    STRING = 100
    NAMED_STRING = 101
    HDD_IO = 103
    HDD_IO_PERCENT = 104
    HDD_SMART_VALUE = 105
    HDD_SMART_WORST = 106
    HDD_SMART_DATA = 107
    HDD_SMART_STATUS = 108
    HDD_SMART_THRESHOLD = 109
    BATTERY_STATUS = 200
    BATTERY_LIFE_TIME = 201
    BATTERY_LIFE_PERCENT = 202
    BATTERY_LIFE_REMAINING = 203
    BATTERY_POWER_LINE_STATUS = 204
    PROCESS_NAME = 300
    PROCESS_RAM = 301
    PROCESS_CPU = 302
    NUMBER = 400
    FRAME_RATE = 500
    NETWORK_DOWNLOAD = 2001
    NETWORK_UPLOAD = 2002

    @classmethod
    def get_name(cls, value: int) -> str:
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN_{value}"

    @property
    def unit(self) -> str:
        units = {
            SensorType.VOLTAGE: "V",
            SensorType.CLOCK: "MHz",
            SensorType.TEMPERATURE: "°C",
            SensorType.LOAD: "%",
            SensorType.FREQUENCY: "Hz",
            SensorType.FAN: "RPM",
            SensorType.FLOW: "L/h",
            SensorType.CONTROL: "%",
            SensorType.LEVEL: "%",
            SensorType.POWER: "W",
            SensorType.DATA: "GB",
            SensorType.SMALLDATA: "MB",
            SensorType.THROUGHPUT: "KiB/s",
            SensorType.CURRENT: "A",
            SensorType.ENERGY: "Wh",
            SensorType.HUMIDITY: "%",
            SensorType.BATTERY_LIFE_PERCENT: "%",
            SensorType.PROCESS_RAM: "MB",
            SensorType.PROCESS_CPU: "%",
            SensorType.FRAME_RATE: "FPS",
            SensorType.NETWORK_DOWNLOAD: "KiB/s",
            SensorType.NETWORK_UPLOAD: "KiB/s",
        }
        return units.get(self, "")


PHYSICAL_HARDWARE_TYPES = frozenset(
    {
        HardwareType.HDD,
        HardwareType.CPU,
        HardwareType.GPUNVIDIA,
        HardwareType.GPUATI,
        HardwareType.GPUINTEL,
        HardwareType.RAM,
        HardwareType.DIMM,
        HardwareType.SUPERIO,
        HardwareType.COOLER,
        HardwareType.PSU,
    }
)


class CommandType(IntEnum):
    UPDATE = 3
    REGISTER_SENSORS = 4
    SET_CONTROLLER = 9
    ACTIVATE_SENSORS = 11
    SET_REFRESH_RATE = 12
    PROCESS_COUNT = 13
    PROCESS_ORDER = 14


@dataclass
class HardwareItem:
    identifier: str
    name: str
    parent: str
    hardware_type: HardwareType


@dataclass
class SensorItem:
    sensor_id: int = 0
    identifier: str = ""
    name: str = ""
    parent: str = ""
    sensor_type: SensorType = SensorType.NUMBER
    value: Any = None


@dataclass
class TelemetrySnapshot:
    server_uid: str
    timestamp: float
    hardware: Dict[str, HardwareItem] = field(default_factory=dict)
    sensors: Dict[int, SensorItem] = field(default_factory=dict)
    sensors_by_identifier: Dict[str, SensorItem] = field(default_factory=dict)
    _root_cache: Dict[str, Optional[HardwareItem]] = field(default_factory=dict, init=False, repr=False)

    def get_root_hardware(self, parent_id: str) -> Optional[HardwareItem]:
        """
        Traverses upward from sub-hardware containers (such as SMART data, disk I/O, etc.)
        to resolve the parent physical device (e.g. Hard Drive, CPU, GPU).
        Results are cached to avoid repeated tree traversals.
        """
        if not parent_id:
            return None

        if parent_id in self._root_cache:
            return self._root_cache[parent_id]

        current = parent_id
        visited = set()
        candidate = self.hardware.get(parent_id)

        while current and current != "/computer" and current not in visited:
            visited.add(current)
            hw = self.hardware.get(current)
            if not hw:
                break
            if hw.hardware_type in PHYSICAL_HARDWARE_TYPES:
                candidate = hw
                break
            if hw.parent in ("/computer", "", None):
                candidate = hw
                break
            current = hw.parent

        self._root_cache[parent_id] = candidate
        return candidate

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "server_uid": self.server_uid,
            "timestamp": self.timestamp,
            "hardware": {
                h.identifier: {
                    "name": h.name,
                    "parent": h.parent,
                    "type": h.hardware_type.name,
                }
                for h in self.hardware.values()
            },
            "sensors": {},
        }
        for s in self.sensors_by_identifier.values():
            root_hw = self.get_root_hardware(s.parent)
            parent_hw = self.hardware.get(s.parent)

            # Determine best hardware device name
            hw_name = root_hw.name if root_hw else (parent_hw.name if parent_hw else s.parent)
            if not hw_name or hw_name == "/mainboard":
                mb = self.hardware.get("/motherboard")
                hw_name = mb.name if mb else "Motherboard"

            # Clean sensor name if empty (e.g. BIOS version or OS version)
            s_name = s.name
            if not s_name:
                if "/osversion" in s.identifier:
                    s_name = "OS Version"
                elif "/mainboard" in s.identifier or "/motherboard" in s.identifier:
                    s_name = "BIOS Version"
                else:
                    s_name = s.identifier.strip("/").split("/")[-1].replace("_", " ").title()

            result["sensors"][s.identifier] = {
                "name": s_name,
                "hardware": hw_name,
                "hardware_id": root_hw.identifier if root_hw else s.parent,
                "sub_hardware": parent_hw.name if parent_hw and parent_hw != root_hw else None,
                "type": s.sensor_type.name,
                "unit": s.sensor_type.unit,
                "value": s.value,
            }
        return result
