"""Binary sensors (read-only). Phase 1: state only, no baseline yet."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import MaisonReseauEntity


async def async_setup_entry(hass, entry, async_add_entities):
    d = entry.runtime_data
    async_add_entities([
        Internet(d.status, "internet"),
        Flag(d.security, "dmz", lambda s: bool([r for r in s.get("dmz") or [] if r["enabled"]]) if s.get("dmz") is not None else None),
        Flag(d.security, "upnp", lambda s: s.get("upnp_enabled")),
        Flag(d.security, "remote_access", lambda s: s.get("remote_access_enabled")),
        Flag(d.security, "guest_wifi", lambda s: s.get("guest_wifi_enabled"), BinarySensorDeviceClass.RUNNING),
    ])


class Internet(MaisonReseauEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self):
        return self.coordinator.data.get("online")


class Flag(MaisonReseauEntity, BinarySensorEntity):
    """A security setting that should normally be OFF: ON is shown as a problem."""

    def __init__(self, coordinator, key, getter, device_class=BinarySensorDeviceClass.PROBLEM):
        super().__init__(coordinator, key)
        self._getter = getter
        self._attr_device_class = device_class

    @property
    def is_on(self):
        return self._getter(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        return {"dmz": self.coordinator.data.get("dmz")} if self._attr_translation_key == "dmz" else None
