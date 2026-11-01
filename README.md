# ha-maison-reseau

Read-only home network security monitoring (Home Assistant custom integration, HACS).
Kept separate from any automation or strategy layer on purpose: this component holds the Livebox
administrator credentials and must never be able to change anything.

Also contains `tools/Install-HassAgent.ps1`, which installs HASS.Agent on Windows PCs with read-only
security sensors (antivirus, firewall, BitLocker, Windows Update) for a security dashboard.

## `maison_reseau` — Livebox security monitoring (read-only)

Custom integration that polls the Orange Livebox local API (sysbus) and exposes:

| Entity | Content |
|---|---|
| `binary_sensor.livebox_internet` | WAN connectivity |
| `sensor.livebox_public_ip` | Public IP (+ link details as attributes) |
| `sensor.livebox_firewall_level_ipv4` / `_ipv6` | Firewall level |
| `sensor.livebox_port_forwarding_rules` | Number of port-forwarding rules (full list in attributes) |
| `sensor.livebox_ipv6_pinholes` | IPv6 pinholes |
| `binary_sensor.livebox_dmz_enabled` / `_upnp_enabled` / `_remote_access_enabled` | Exposure settings (ON = problem) |
| `binary_sensor.livebox_guest_wi_fi` | Guest Wi-Fi |
| `sensor.livebox_active_devices` | Devices seen by the Livebox (DHCP server) |
| `sensor.livebox_unavailable_checks` | Checks the firmware did not answer (diagnostic) |

### Security model

* The Livebox has a single admin account. The client enforces an **allow-list of read-only
  calls** (`livebox.ALLOWED_CALLS`, all `get…`); any other call is rejected before a request
  is sent (covered by tests).
* The password lives only in the config entry; diagnostics redact it, the public IP and MACs.
* Local polling only: connection every minute, security settings every 15 minutes.

### Roadmap

1. Read-only sensors (this version).
2. Validated baseline + drift alerts (new port forward, UPnP/DMZ/remote access switched on, firewall level lowered).
3. Known devices + new-device alerts; critical devices (cameras, outdoor PoE switches) must stay online.

### Install

HACS → custom repository `Pepane13/ha-maison-reseau` (Integration), then *Settings → Devices & services → Add integration → Maison Réseau*.

### Tests

```bash
python -m pytest tests
```
