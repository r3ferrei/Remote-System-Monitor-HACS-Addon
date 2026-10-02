"""Sensor platform for Remote System Monitor."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import RSMDataUpdateCoordinator
from .rsm_protocol import HardwareItem, HardwareType, SensorItem, SensorType

_LOGGER = logging.getLogger(__name__)

# Map SensorType to (device_class, state_class, unit)
SENSOR_TYPE_MAPPINGS: Dict[SensorType, tuple[Optional[SensorDeviceClass], Optional[SensorStateClass], Optional[str]]] = {
    SensorType.TEMPERATURE: (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, UnitOfTemperature.CELSIUS),
    SensorType.VOLTAGE: (SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, UnitOfElectricPotential.VOLT),
    SensorType.CURRENT: (SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, UnitOfElectricCurrent.AMPERE),
    SensorType.POWER: (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, UnitOfPower.WATT),
    SensorType.ENERGY: (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL, UnitOfEnergy.WATT_HOUR),
    SensorType.CLOCK: (SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT, UnitOfFrequency.MEGAHERTZ),
    SensorType.FREQUENCY: (SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT, UnitOfFrequency.HERTZ),
    SensorType.FAN: (None, SensorStateClass.MEASUREMENT, "RPM"),
    SensorType.FLOW: (None, SensorStateClass.MEASUREMENT, "L/h"),
    SensorType.LOAD: (None, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.CONTROL: (None, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.LEVEL: (None, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.HUMIDITY: (SensorDeviceClass.HUMIDITY, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.DATA: (SensorDeviceClass.DATA_SIZE, SensorStateClass.MEASUREMENT, UnitOfInformation.GIGABYTES),
    SensorType.SMALLDATA: (SensorDeviceClass.DATA_SIZE, SensorStateClass.MEASUREMENT, UnitOfInformation.MEGABYTES),
    SensorType.PROCESS_RAM: (SensorDeviceClass.DATA_SIZE, SensorStateClass.MEASUREMENT, UnitOfInformation.MEGABYTES),
    SensorType.THROUGHPUT: (SensorDeviceClass.DATA_RATE, SensorStateClass.MEASUREMENT, UnitOfDataRate.KIBIBYTES_PER_SECOND),
    SensorType.NETWORK_DOWNLOAD: (SensorDeviceClass.DATA_RATE, SensorStateClass.MEASUREMENT, UnitOfDataRate.KIBIBYTES_PER_SECOND),
    SensorType.NETWORK_UPLOAD: (SensorDeviceClass.DATA_RATE, SensorStateClass.MEASUREMENT, UnitOfDataRate.KIBIBYTES_PER_SECOND),
    SensorType.BATTERY_LIFE_PERCENT: (SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.PROCESS_CPU: (None, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.HDD_IO_PERCENT: (None, SensorStateClass.MEASUREMENT, PERCENTAGE),
    SensorType.FRAME_RATE: (None, SensorStateClass.MEASUREMENT, "FPS"),
    SensorType.STRING: (None, None, None),
    SensorType.NAMED_STRING: (None, None, None),
    SensorType.HDD_SMART_STATUS: (None, None, None),
    SensorType.PROCESS_NAME: (None, None, None),
}


def _is_per_game_framerate_sensor(sensor_item: SensorItem) -> bool:
    """Check if sensor is an individual game frame rate sensor.

    Remote System Monitor dynamically creates a sensor item per running 3D game
    under the '/framerate' parent. We ignore these individual game sensors to prevent
    a pileup of dead entities in Home Assistant, tracking only the unified
    '# Foreground App' (/framerate/activeapp) sensor instead.
    """
    ident = sensor_item.identifier
    # Foreground App sensor is the single entity we keep
    if ident == "/framerate/activeapp" or ident.endswith("/activeapp"):
        return False

    # Any other sensor under /framerate (game executables, error items) is filtered out
    if sensor_item.parent == "/framerate" or ident.startswith("/framerate/"):
        return True

    # Any other FRAME_RATE sensor type that isn't the activeapp
    if sensor_item.sensor_type == SensorType.FRAME_RATE:
        return True

    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Remote System Monitor sensors based on a config entry."""
    coordinator: RSMDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Clean up obsolete per-game frame rate entities previously saved in entity registry
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    for ent in entries:
        uid = ent.unique_id
        if "/framerate/" in uid and not (uid.endswith("/activeapp") or uid.endswith("/framerate/activeapp")):
            _LOGGER.info(
                "Removing obsolete per-game frame rate entity from registry: %s (unique_id: %s)",
                ent.entity_id,
                uid,
            )
            ent_reg.async_remove(ent.entity_id)

    created_sensors: set[str] = set()

    @callback
    def add_sensors_for_identifiers(identifiers: List[str]) -> None:
        """Helper to create and register sensor entities."""
        entities: List[RSMSensorEntity] = []
        snapshot = coordinator.data
        if not snapshot:
            return

        for ident in identifiers:
            if ident in created_sensors:
                continue
            sensor_item = snapshot.sensors_by_identifier.get(ident)
            if not sensor_item:
                continue

            # Ignore individual game frame rate sensors
            if _is_per_game_framerate_sensor(sensor_item):
                continue

            created_sensors.add(ident)
            entities.append(RSMSensorEntity(coordinator, ident))

        if entities:
            async_add_entities(entities)

    # Initial batch of sensors
    if coordinator.data and coordinator.data.sensors_by_identifier:
        add_sensors_for_identifiers(list(coordinator.data.sensors_by_identifier.keys()))

    # Listen for new sensors dynamically discovered during runtime
    entry.async_on_unload(coordinator.register_new_sensors_callback(add_sensors_for_identifiers))


class RSMSensorEntity(CoordinatorEntity[RSMDataUpdateCoordinator], SensorEntity):
    """Representation of a Remote System Monitor sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RSMDataUpdateCoordinator, sensor_identifier: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.sensor_identifier = sensor_identifier
        self._attr_unique_id = f"{coordinator.server_uid}_{sensor_identifier}"
        self._metadata_initialized: bool = False
        self._base_attributes: Dict[str, Any] = {}

        # Initial metadata setup
        self._update_metadata()

    @property
    def _is_foreground_app(self) -> bool:
        """Return True if this sensor represents the Foreground App frame rate."""
        return (
            self.sensor_identifier == "/framerate/activeapp"
            or self.sensor_identifier.endswith("/activeapp")
        )

    def _update_metadata(self) -> None:
        """Update sensor attributes and device registry information once."""
        if self._metadata_initialized:
            return

        snapshot = self.coordinator.data
        if not snapshot:
            return

        sensor_item: Optional[SensorItem] = snapshot.sensors_by_identifier.get(self.sensor_identifier)
        if not sensor_item:
            return

        # Resolve sensor name
        s_name = sensor_item.name
        if self._is_foreground_app or s_name == "# Foreground App":
            s_name = "Foreground App"
        elif not s_name:
            if "/osversion" in sensor_item.identifier:
                s_name = "OS Version"
            elif "/mainboard" in sensor_item.identifier or "/motherboard" in sensor_item.identifier:
                s_name = "BIOS Version"
            else:
                s_name = sensor_item.identifier.strip("/").split("/")[-1].replace("_", " ").title()
        else:
            s_name = s_name.lstrip("#").strip()

        self._attr_name = s_name

        if self._is_foreground_app:
            self._attr_icon = "mdi:gamepad-variant-outline"

        # Mapping classes and units
        mapping = SENSOR_TYPE_MAPPINGS.get(sensor_item.sensor_type)
        if mapping:
            device_class, state_class, unit = mapping
            self._attr_device_class = device_class
            self._attr_state_class = state_class
            self._attr_native_unit_of_measurement = unit
        else:
            self._attr_device_class = None
            self._attr_state_class = None
            self._attr_native_unit_of_measurement = sensor_item.sensor_type.unit or None

        # Build hierarchical device info
        root_hw: Optional[HardwareItem] = snapshot.get_root_hardware(sensor_item.parent)
        parent_hw: Optional[HardwareItem] = snapshot.hardware.get(sensor_item.parent)

        if root_hw and root_hw.identifier not in ("/computer", "", None):
            # Sub-device (CPU, GPU, RAM, Motherboard, Disk, etc.)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{self.coordinator.server_uid}_{root_hw.identifier}")},
                name=root_hw.name,
                manufacturer=MANUFACTURER,
                model=root_hw.hardware_type.name.replace("_", " ").title(),
                via_device=(DOMAIN, self.coordinator.server_uid),
            )
        else:
            # Root Computer Device
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, self.coordinator.server_uid)},
                name=self.coordinator.computer_name,
                manufacturer=MANUFACTURER,
                model=DEFAULT_MODEL,
                sw_version=f"Protocol {self.coordinator.client.server_version}",
            )

        # Pre-compute static diagnostic attributes once
        attrs: Dict[str, Any] = {
            "sensor_identifier": self.sensor_identifier,
            "sensor_type": sensor_item.sensor_type.name,
        }
        if parent_hw:
            attrs["hardware_name"] = parent_hw.name
            attrs["hardware_type"] = parent_hw.hardware_type.name
        self._base_attributes = attrs

        self._metadata_initialized = True

    def _get_foreground_app_attributes(self) -> Dict[str, Any]:
        """Resolve current app and active process details from coordinator data."""
        attrs: Dict[str, Any] = {
            "current_app": None,
            "executable": None,
            "is_foreground": False,
        }
        if not self.coordinator.data:
            return attrs

        snapshot = self.coordinator.data
        active_fps = self.native_value

        # Collect all individual app sensors and error sensors under /framerate
        app_sensors: List[SensorItem] = []
        error_val: Optional[str] = None

        for ident, item in snapshot.sensors_by_identifier.items():
            if ident == "/framerate/error":
                if item.value:
                    error_val = str(item.value)
            elif (
                ident.startswith("/framerate/")
                and ident != "/framerate/activeapp"
                and item.sensor_type == SensorType.FRAME_RATE
            ):
                app_sensors.append(item)

        if error_val:
            attrs["error"] = error_val

        if not app_sensors:
            return attrs

        # Determine which app is currently active
        current_app: Optional[SensorItem] = None
        is_fg = False

        # 1. Match app whose FPS matches active_fps (when active_fps > 0)
        if isinstance(active_fps, (int, float)) and active_fps > 0:
            matching = [a for a in app_sensors if a.value == active_fps]
            if matching:
                current_app = matching[0]
                is_fg = True

        # 2. If no exact match, check for any app actively rendering (> 0 FPS)
        if not current_app:
            active_apps = [
                a for a in app_sensors
                if a.value is not None and isinstance(a.value, (int, float)) and a.value > 0
            ]
            if active_apps:
                current_app = active_apps[0]
                is_fg = bool(isinstance(active_fps, (int, float)) and active_fps > 0)

        # 3. Fallback: single reported app
        if not current_app and len(app_sensors) == 1:
            current_app = app_sensors[0]
            is_fg = bool(isinstance(active_fps, (int, float)) and active_fps > 0)

        if current_app:
            raw_name = (current_app.name or "").strip()
            exe_name = current_app.identifier.split("/")[-1]
            app_title = raw_name if raw_name else exe_name

            attrs["current_app"] = app_title
            attrs["executable"] = exe_name
            attrs["is_foreground"] = is_fg
            if current_app.value is not None:
                attrs["app_fps"] = current_app.value

        if len(app_sensors) > 1:
            attrs["running_apps"] = [
                (a.name or a.identifier.split("/")[-1]).strip()
                for a in app_sensors
            ]

        return attrs

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return dynamic extra state attributes."""
        attrs = dict(self._base_attributes)
        if self._is_foreground_app:
            attrs.update(self._get_foreground_app_attributes())
        return attrs

    @property
    def native_value(self) -> Any:
        """Return the current sensor value."""
        if not self.coordinator.data:
            return None
        sensor_item = self.coordinator.data.sensors_by_identifier.get(self.sensor_identifier)
        if not sensor_item:
            return None
        if sensor_item.value is None and self._is_foreground_app:
            return 0
        return sensor_item.value

    @property
    def available(self) -> bool:
        """Return True if coordinator is successful and sensor is in current snapshot."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.sensor_identifier in self.coordinator.data.sensors_by_identifier
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self._metadata_initialized:
            self._update_metadata()
        self.async_write_ha_state()
