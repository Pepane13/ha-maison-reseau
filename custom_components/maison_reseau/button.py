"""Button: run the Livebox's own speed test (diagnostic, no configuration change)."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import MaisonReseauEntity
from .livebox import LiveboxError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SpeedtestButton(entry.runtime_data.speedtest, "run_speedtest")])


class SpeedtestButton(MaisonReseauEntity, ButtonEntity):
    _attr_icon = "mdi:speedometer"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.run_speedtest()
        except (LiveboxError, TimeoutError) as err:
            raise HomeAssistantError(f"Livebox speed test failed: {err}") from err
        finally:
            await self.coordinator.async_request_refresh()
