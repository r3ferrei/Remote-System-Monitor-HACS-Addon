"""DataUpdateCoordinator for Remote System Monitor."""

from datetime import timedelta
import logging
from typing import Callable, List, Optional, Set

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL, DEFAULT_NAME, DEFAULT_SCAN_INTERVAL, DOMAIN
from .rsm_client import RSMAuthError, RSMClient, RSMConnectionError, RSMError
from .rsm_protocol import HardwareItem, HardwareType, TelemetrySnapshot

_LOGGER = logging.getLogger(__name__)


class RSMDataUpdateCoordinator(DataUpdateCoordinator[TelemetrySnapshot]):
    """Coordinator to manage polling of Remote System Monitor server over TLS."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data[CONF_PORT]
        self.password: str = entry.data[CONF_PASSWORD]

        # Scan interval can be updated via options
        scan_interval_sec: int = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.host}_{self.port}",
            update_interval=timedelta(seconds=scan_interval_sec),
        )

        self.client = RSMClient(host=self.host, port=self.port, timeout=10.0)
        self.known_sensor_identifiers: Set[str] = set()
        self._new_sensors_callbacks: List[Callable[[List[str]], None]] = []
        self._computer_name: Optional[str] = None

    @property
    def server_uid(self) -> str:
        """Return server UID if available, else host:port fallback."""
        return self.client.server_uid or f"{self.host}_{self.port}"

    @property
    def computer_name(self) -> str:
        """Return hostname/computer name reported by RSM server."""
        if self._computer_name:
            return self._computer_name
        # Search for computer or mainboard hardware item
        if self.data and self.data.hardware:
            for hw in self.data.hardware.values():
                if hw.hardware_type in (
                    HardwareType.COMPUTER,
                    HardwareType.COMPUTER_WIN_10,
                    HardwareType.COMPUTER_WIN_SEVEN,
                    HardwareType.COMPUTER_WIN_VISTA,
                    HardwareType.COMPUTER_WIN_XP,
                ):
                    self._computer_name = hw.name
                    return self._computer_name
        return self.host

    def register_new_sensors_callback(self, callback: Callable[[List[str]], None]) -> Callable[[], None]:
        """Register callback invoked when new sensors are discovered dynamically."""
        self._new_sensors_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._new_sensors_callbacks:
                self._new_sensors_callbacks.remove(callback)

        return unsubscribe

    def _update_data_sync(self) -> TelemetrySnapshot:
        """Synchronously poll the RSM server (executed in worker thread)."""
        try:
            if not self.client.is_connected:
                _LOGGER.debug("Connecting to Remote System Monitor server at %s:%s...", self.host, self.port)
                self.client.connect()
                self.client.perform_handshake()
                if not self.client.authenticate(self.password):
                    raise RSMAuthError("Invalid Remote System Monitor server password")

                interval_ms = max(500, int(self.update_interval.total_seconds() * 1000))
                self.client.initialize(interval_ms=interval_ms)
                _LOGGER.info("Connected and authenticated to Remote System Monitor (%s)", self.client.server_uid)

            self.client.request_update()
            snapshot = self.client.read_telemetry_packet()

            # Detect dynamically discovered sensors
            current_idents = set(snapshot.sensors_by_identifier.keys())
            new_idents = list(current_idents - self.known_sensor_identifiers)
            if new_idents:
                self.known_sensor_identifiers.update(new_idents)
                # Dispatch notification to any registered callbacks
                self.hass.loop.call_soon_threadsafe(self._dispatch_new_sensors, new_idents)

            return snapshot

        except RSMAuthError as err:
            self.client.close()
            raise ConfigEntryAuthFailed(str(err)) from err
        except (RSMConnectionError, RSMError, Exception) as err:
            _LOGGER.debug("Error updating from Remote System Monitor server: %s", err)
            self.client.close()
            raise UpdateFailed(f"Connection failed: {err}") from err

    def _dispatch_new_sensors(self, new_idents: List[str]) -> None:
        """Call registered callbacks when new sensors appear."""
        for cb in self._new_sensors_callbacks:
            try:
                cb(new_idents)
            except Exception as err:
                _LOGGER.exception("Error in new sensors callback: %s", err)

    async def _async_update_data(self) -> TelemetrySnapshot:
        """Asynchronously fetch data by running the sync routine in executor."""
        return await self.hass.async_add_executor_job(self._update_data_sync)

    async def async_shutdown(self) -> None:
        """Close connection cleanly on coordinator shutdown / integration unload."""
        await self.hass.async_add_executor_job(self.client.close)
