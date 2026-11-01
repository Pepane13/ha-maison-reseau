"""Diagnostics (password and public IP redacted)."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_PASSWORD

REDACT = {CONF_PASSWORD, "public_ip", "ipv6", "mac"}


async def async_get_config_entry_diagnostics(hass, entry):
    d = entry.runtime_data
    return async_redact_data({
        "entry": dict(entry.data),
        "status": d.status.data,
        "security": {k: v for k, v in (d.security.data or {}).items() if k != "devices"},
        "devices": len((d.security.data or {}).get("devices") or []),
    }, REDACT)
