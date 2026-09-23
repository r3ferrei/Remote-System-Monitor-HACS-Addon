"""Config flow for Remote System Monitor integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .rsm_client import RSMAuthError, RSMClient, RSMConnectionError

_LOGGER = logging.getLogger(__name__)


def _test_connection_sync(host: str, port: int, password: str) -> tuple[str, str]:
    """Test connection and return server UID and initial computer name."""
    client = RSMClient(host=host, port=port, timeout=8.0)
    try:
        client.connect()
        client.perform_handshake()
        if not client.authenticate(password):
            raise RSMAuthError("Invalid server password")
        
        # Pull initial packet to read hardware name if possible
        client.request_update()
        snapshot = client.read_telemetry_packet()
        comp_name = host
        if snapshot and snapshot.hardware:
            for hw in snapshot.hardware.values():
                if "computer" in hw.identifier.lower() or hw.hardware_type.name.startswith("COMPUTER"):
                    comp_name = hw.name
                    break
        return client.server_uid or f"{host}_{port}", comp_name
    finally:
        client.close()


class RSMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Remote System Monitor."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            password = user_input[CONF_PASSWORD]
            scan_interval = user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

            try:
                server_uid, comp_name = await self.hass.async_add_executor_job(
                    _test_connection_sync, host, port, password
                )

                await self.async_set_unique_id(server_uid)
                self._abort_if_unique_id_configured()

                title = f"{comp_name} ({host})" if comp_name and comp_name != host else f"Remote System Monitor ({host})"

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_PASSWORD: password,
                        CONF_SCAN_INTERVAL: scan_interval,
                    },
                )
            except RSMAuthError:
                errors["base"] = "invalid_auth"
            except RSMConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected exception during RSM setup: %s", err)
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="127.0.0.1"): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return RSMOptionsFlowHandler(config_entry)


class RSMOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow to update polling interval and connection options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=current_interval
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
