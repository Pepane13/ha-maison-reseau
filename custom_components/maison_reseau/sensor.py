"""Sensors (read-only)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfDataRate, UnitOfTime
from homeassistant.util import dt as dt_util

from .entity import MaisonReseauEntity


async def async_setup_entry(hass, entry, async_add_entities):
    d = entry.runtime_data
    async_add_entities([
        PublicIp(d.status, "public_ip"),
        FirewallLevel(d.security, "firewall_level", "firewall_level"),
        FirewallLevel(d.security, "firewall_ipv6_level", "firewall_ipv6_level"),
        RuleCount(d.security, "port_forwarding", "port_forwarding"),
        RuleCount(d.security, "ipv6_pinholes", "ipv6_pinholes"),
        DeviceCount(d.security, "devices_active"),
        Unavailable(d.security, "unavailable_checks"),
        SpeedValue(d.speedtest, "speedtest_download", "download"),
        SpeedValue(d.speedtest, "speedtest_upload", "upload"),
        SpeedLatency(d.speedtest, "speedtest_latency"),
        SpeedLastRun(d.speedtest, "speedtest_last_run"),
    ])


class PublicIp(MaisonReseauEntity, SensorEntity):
    _attr_icon = "mdi:ip-network"

    @property
    def native_value(self):
        return self.coordinator.data.get("public_ip")

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        return {k: d.get(k) for k in ("ipv6", "link_type", "gpon_state", "last_error", "firmware", "box_uptime", "wan_uptime")}


class FirewallLevel(MaisonReseauEntity, SensorEntity):
    _attr_icon = "mdi:wall-fire"

    def __init__(self, coordinator, key, field):
        super().__init__(coordinator, key)
        self._field = field

    @property
    def native_value(self):
        v = self.coordinator.data.get(self._field)
        return v if isinstance(v, str) else None


class RuleCount(MaisonReseauEntity, SensorEntity):
    _attr_icon = "mdi:router-network"

    def __init__(self, coordinator, key, field):
        super().__init__(coordinator, key)
        self._field = field

    @property
    def native_value(self):
        return len(self.coordinator.data.get(self._field) or [])

    @property
    def extra_state_attributes(self):
        return {"rules": self.coordinator.data.get(self._field) or []}


class DeviceCount(MaisonReseauEntity, SensorEntity):
    _attr_icon = "mdi:devices"

    @property
    def native_value(self):
        return sum(1 for d in self.coordinator.data.get("devices") or [] if d["active"])

    @property
    def extra_state_attributes(self):
        devices = self.coordinator.data.get("devices") or []
        return {"total_known_to_livebox": len(devices)}


class Unavailable(MaisonReseauEntity, SensorEntity):
    """Checks the Livebox did not answer (lets us verify the API names on this firmware)."""

    _attr_icon = "mdi:help-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return len(self.coordinator.data.get("unavailable") or [])

    @property
    def extra_state_attributes(self):
        return {"details": self.coordinator.data.get("unavailable") or []}


class _Speed(MaisonReseauEntity, SensorEntity):
    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)


class SpeedValue(_Speed):
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, key, field):
        super().__init__(coordinator, key)
        self._field = field

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._field)


class SpeedLatency(_Speed):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_icon = "mdi:timer-outline"

    @property
    def native_value(self):
        v = (self.coordinator.data or {}).get("latency")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class SpeedLastRun(_Speed):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    @property
    def native_value(self) -> datetime | None:
        v = (self.coordinator.data or {}).get("started")
        if not v:
            return None
        parsed = dt_util.parse_datetime(str(v))
        if parsed is None:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)

    @property
    def extra_state_attributes(self):
        return {"state": (self.coordinator.data or {}).get("state")}
