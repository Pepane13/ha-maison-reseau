"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class MaisonReseauEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Livebox", manufacturer="Orange / Sagemcom",
            model=(entry.runtime_data.status.data or {}).get("model") if hasattr(entry, "runtime_data") else None,
        )
