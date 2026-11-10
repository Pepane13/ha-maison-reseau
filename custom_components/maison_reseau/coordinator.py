"""Data coordinators (polling only, read-only)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_PASSWORD, DOMAIN, SECURITY_INTERVAL, SPEEDTEST_INTERVAL, STATUS_INTERVAL
from .livebox import LiveboxAuthError, LiveboxClient, LiveboxError

_LOGGER = logging.getLogger(__name__)


def build_client(hass: HomeAssistant, data: dict[str, Any]) -> LiveboxClient:
    # Own session: the Livebox session cookie must not leak into HA's shared session.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())

    async def transport(url: str, headers: dict[str, str], body: str):
        async with asyncio.timeout(15):
            async with session.post(url, data=body.encode(), headers=headers) as resp:
                return resp.status, dict(resp.headers), await resp.text()

    return LiveboxClient(host=data[CONF_HOST], password=data[CONF_PASSWORD], transport=transport)


class _Base(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass, entry, client, name, interval):
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {name}", update_interval=interval)
        self.client = client

    async def _fetch(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch()
        except LiveboxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (LiveboxError, TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(str(err)) from err


class StatusCoordinator(_Base):
    def __init__(self, hass, entry, client):
        super().__init__(hass, entry, client, "status", STATUS_INTERVAL)

    async def _fetch(self):
        return await self.client.get_status()


class SecurityCoordinator(_Base):
    def __init__(self, hass, entry, client):
        super().__init__(hass, entry, client, "security", SECURITY_INTERVAL)

    async def _fetch(self):
        data = await self.client.get_security()
        data["devices"] = await self.client.get_devices()
        return data


class SpeedtestCoordinator(_Base):
    """Reads the last Livebox speed test (read-only); a test is started by the button."""

    def __init__(self, hass, entry, client):
        super().__init__(hass, entry, client, "speedtest", SPEEDTEST_INTERVAL)

    async def _fetch(self):
        return await self.client.get_speedtest_results()


@dataclass
class MaisonReseauData:
    client: LiveboxClient
    status: StatusCoordinator
    security: SecurityCoordinator
    speedtest: SpeedtestCoordinator
