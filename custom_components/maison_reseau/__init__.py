"""Maison Réseau: read-only monitoring of the Orange Livebox (connection, firewall, devices, speed test)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import MaisonReseauData, build_client, SecurityCoordinator, SpeedtestCoordinator, StatusCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

type MaisonReseauConfigEntry = ConfigEntry[MaisonReseauData]


async def async_setup_entry(hass: HomeAssistant, entry: MaisonReseauConfigEntry) -> bool:
    client = build_client(hass, entry.data)
    status = StatusCoordinator(hass, entry, client)
    security = SecurityCoordinator(hass, entry, client)
    await status.async_config_entry_first_refresh()
    await security.async_config_entry_first_refresh()
    speedtest = SpeedtestCoordinator(hass, entry, client)
    await speedtest.async_refresh()  # best-effort: older firmware may not expose the speed test
    entry.runtime_data = MaisonReseauData(client=client, status=status, security=security, speedtest=speedtest)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MaisonReseauConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
