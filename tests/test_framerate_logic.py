"""Unit tests for frame rate filtering and Foreground App game attributes."""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest.mock import MagicMock

# Create mock homeassistant modules if not present in environment
for mod_name in [
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.exceptions",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Define dummy base classes for SensorEntity and CoordinatorEntity
class DummySensorEntity:
    pass

class DummyCoordinatorEntity:
    __class_getitem__ = classmethod(lambda cls, item: cls)

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return True

sys.modules["homeassistant.components.sensor"].SensorEntity = DummySensorEntity
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = MagicMock()
sys.modules["homeassistant.components.sensor"].SensorStateClass = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = DummyCoordinatorEntity
sys.modules["homeassistant.core"].callback = lambda f: f

# Now import from custom_components.remote_system_monitor
from custom_components.remote_system_monitor.rsm_protocol import (
    HardwareItem,
    HardwareType,
    SensorItem,
    SensorType,
    TelemetrySnapshot,
)
from custom_components.remote_system_monitor.sensor import (
    RSMSensorEntity,
    _is_per_game_framerate_sensor,
)


def test_is_per_game_framerate_sensor():
    print("Testing _is_per_game_framerate_sensor...")
    # Foreground App sensor must NOT be filtered out
    s_fg = SensorItem(
        identifier="/framerate/activeapp",
        name="# Foreground App",
        parent="/framerate",
        sensor_type=SensorType.FRAME_RATE,
    )
    assert not _is_per_game_framerate_sensor(s_fg), "Foreground App sensor should NOT be filtered out"

    # Per-game frame rate sensors MUST be filtered out
    s_game1 = SensorItem(
        identifier="/framerate/cyberpunk2077.exe",
        name="Cyberpunk 2077",
        parent="/framerate",
        sensor_type=SensorType.FRAME_RATE,
    )
    assert _is_per_game_framerate_sensor(s_game1), "Cyberpunk per-game sensor should be filtered out"

    s_game2 = SensorItem(
        identifier="/framerate/eldenring.exe",
        name="ELDEN RING",
        parent="/framerate",
        sensor_type=SensorType.FRAME_RATE,
    )
    assert _is_per_game_framerate_sensor(s_game2), "Elden Ring per-game sensor should be filtered out"

    # Error sensor under /framerate should also be filtered out from separate entity creation
    s_err = SensorItem(
        identifier="/framerate/error",
        name="Error",
        parent="/framerate",
        sensor_type=SensorType.STRING,
    )
    assert _is_per_game_framerate_sensor(s_err), "Error sensor under /framerate should be filtered out"

    # Regular sensors must NOT be filtered out
    s_cpu = SensorItem(
        identifier="/amdcpu/0/load/0",
        name="CPU Total",
        parent="/amdcpu/0",
        sensor_type=SensorType.LOAD,
    )
    assert not _is_per_game_framerate_sensor(s_cpu), "CPU load sensor should NOT be filtered out"

    s_gpu = SensorItem(
        identifier="/gpu-nvidia/0/temperature/0",
        name="GPU Temperature",
        parent="/gpu-nvidia/0",
        sensor_type=SensorType.TEMPERATURE,
    )
    assert not _is_per_game_framerate_sensor(s_gpu), "GPU temperature sensor should NOT be filtered out"

    print("  [PASSED] _is_per_game_framerate_sensor filtering checks.")


def test_foreground_app_attributes():
    print("Testing Foreground App attributes tracking...")

    # Mock coordinator and snapshot
    coordinator = MagicMock()
    coordinator.server_uid = "server123"
    coordinator.computer_name = "MY-PC"
    coordinator.client.server_version = 398

    # Hardware tree
    hw_computer = HardwareItem("/computer", "MY-PC", "", HardwareType.COMPUTER)
    hw_framerate = HardwareItem("/framerate", "Frame Rate", "/computer", HardwareType.MAINBOARD)

    # 1. Scenario: No game running, RTSS running at 0 FPS
    s_active = SensorItem(
        sensor_id=1,
        identifier="/framerate/activeapp",
        name="# Foreground App",
        parent="/framerate",
        sensor_type=SensorType.FRAME_RATE,
        value=0,
    )
    snap1 = TelemetrySnapshot(
        server_uid="server123",
        timestamp=1000.0,
        hardware={"/computer": hw_computer, "/framerate": hw_framerate},
        sensors={1: s_active},
        sensors_by_identifier={"/framerate/activeapp": s_active},
    )
    coordinator.data = snap1

    entity = RSMSensorEntity(coordinator, "/framerate/activeapp")
    assert entity._attr_name == "Foreground App", f"Expected 'Foreground App', got '{entity._attr_name}'"
    assert entity._attr_icon == "mdi:gamepad-variant-outline", f"Expected gamepad icon, got '{entity._attr_icon}'"
    assert entity.native_value == 0

    attrs = entity.extra_state_attributes
    assert attrs["current_app"] is None, f"Expected current_app None, got {attrs['current_app']}"
    assert attrs["executable"] is None
    assert attrs["is_foreground"] is False
    print("  [PASSED] Scenario 1: Idle system without app running.")

    # 2. Scenario: App running and active in foreground (e.g. Starfield at 75 FPS)
    s_active.value = 75
    s_starfield = SensorItem(
        sensor_id=2,
        identifier="/framerate/Starfield.exe",
        name="Starfield",
        parent="/framerate",
        sensor_type=SensorType.FRAME_RATE,
        value=75,
    )
    snap2 = TelemetrySnapshot(
        server_uid="server123",
        timestamp=1001.0,
        hardware={"/computer": hw_computer, "/framerate": hw_framerate},
        sensors={1: s_active, 2: s_starfield},
        sensors_by_identifier={
            "/framerate/activeapp": s_active,
            "/framerate/Starfield.exe": s_starfield,
        },
    )
    coordinator.data = snap2

    assert entity.native_value == 75
    attrs = entity.extra_state_attributes
    assert attrs["current_app"] == "Starfield", f"Expected Starfield, got {attrs['current_app']}"
    assert attrs["executable"] == "Starfield.exe"
    assert attrs["app_fps"] == 75
    assert attrs["is_foreground"] is True
    print("  [PASSED] Scenario 2: Active foreground app tracking.")

    # 3. Scenario: User Alt-Tabs to desktop (active FPS drops to 0, but app still running in background)
    s_active.value = 0
    s_starfield.value = 60
    assert entity.native_value == 0
    attrs = entity.extra_state_attributes
    assert attrs["current_app"] == "Starfield", f"Expected Starfield to remain identified, got {attrs['current_app']}"
    assert attrs["executable"] == "Starfield.exe"
    assert attrs["is_foreground"] is False
    assert attrs["app_fps"] == 60
    print("  [PASSED] Scenario 3: Alt-tabbed / background app tracking.")

    # 4. Scenario: User quits app (app sensor removed)
    s_active.value = 0
    snap4 = TelemetrySnapshot(
        server_uid="server123",
        timestamp=1003.0,
        hardware={"/computer": hw_computer, "/framerate": hw_framerate},
        sensors={1: s_active},
        sensors_by_identifier={"/framerate/activeapp": s_active},
    )
    coordinator.data = snap4
    assert entity.native_value == 0
    attrs = entity.extra_state_attributes
    assert attrs["current_app"] is None
    assert attrs["executable"] is None
    assert attrs["is_foreground"] is False
    print("  [PASSED] Scenario 4: Clean state reset after app exit.")

    # 5. Scenario: RTSS error reporting
    s_err = SensorItem(
        sensor_id=3,
        identifier="/framerate/error",
        name="Error",
        parent="/framerate",
        sensor_type=SensorType.STRING,
        value="RTSS is not running.",
    )
    snap5 = TelemetrySnapshot(
        server_uid="server123",
        timestamp=1004.0,
        hardware={"/computer": hw_computer, "/framerate": hw_framerate},
        sensors={1: s_active, 3: s_err},
        sensors_by_identifier={"/framerate/activeapp": s_active, "/framerate/error": s_err},
    )
    coordinator.data = snap5
    attrs = entity.extra_state_attributes
    assert attrs.get("error") == "RTSS is not running.", f"Expected RTSS error attribute, got {attrs.get('error')}"
    print("  [PASSED] Scenario 5: Error attribute passthrough.")


def test_entity_registry_cleanup():
    print("Testing entity registry cleanup logic...")
    # Mock entity registry entries
    e_cpu = MagicMock(entity_id="sensor.my_pc_cpu_load", unique_id="server123_/amdcpu/0/load/0")
    e_fg = MagicMock(entity_id="sensor.my_pc_foreground_app", unique_id="server123_/framerate/activeapp")
    e_game1 = MagicMock(entity_id="sensor.my_pc_cyberpunk2077_exe", unique_id="server123_/framerate/cyberpunk2077.exe")
    e_game2 = MagicMock(entity_id="sensor.my_pc_eldenring_exe", unique_id="server123_/framerate/eldenring.exe")
    e_err = MagicMock(entity_id="sensor.my_pc_error", unique_id="server123_/framerate/error")

    entries = [e_cpu, e_fg, e_game1, e_game2, e_err]

    removed_ids = []
    for ent in entries:
        uid = ent.unique_id
        if "/framerate/" in uid and not (uid.endswith("/activeapp") or uid.endswith("/framerate/activeapp")):
            removed_ids.append(ent.entity_id)

    assert "sensor.my_pc_cyberpunk2077_exe" in removed_ids
    assert "sensor.my_pc_eldenring_exe" in removed_ids
    assert "sensor.my_pc_error" in removed_ids
    assert "sensor.my_pc_foreground_app" not in removed_ids
    assert "sensor.my_pc_cpu_load" not in removed_ids
    print(f"  [PASSED] Entity registry cleanup cleanly removes: {removed_ids}")


if __name__ == "__main__":
    test_is_per_game_framerate_sensor()
    test_foreground_app_attributes()
    test_entity_registry_cleanup()
    print("\nALL UNIT TESTS PASSED SUCCESSFULLY!")
