"""Read-only client for the Orange Livebox (Sagemcom "sysbus" JSON API on http://<host>/ws).

Security model
--------------
The Livebox has a single administrator account, so the credentials technically allow
configuration changes. This client therefore enforces a strict allow-list: only the
(service, method) pairs listed in ``ALLOWED_CALLS`` can be sent, and every one of them is a
read ("get...") call. Any other call raises ``LiveboxForbiddenCall`` before a request is made.

Single exception (10/11/2026, requested by the owner): the Livebox's own speed test. It is a
diagnostic, not a configuration change. It is listed apart in ``DIAGNOSTIC_CALLS`` with fixed
parameters that callers cannot override, and is only reachable through ``start_speedtest``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

APP_NAME = "so_sdkut"
LOGIN_CALL = ("sah.Device.Information", "createContext")

# Read-only calls. Keys are (sysbus service, method); values are default parameters.
ALLOWED_CALLS: dict[tuple[str, str], dict[str, Any]] = {
    ("NMC", "getWANStatus"): {},
    ("DeviceInfo", "get"): {},
    ("NeMo.Intf.data", "getMIBs"): {"mibs": "dhcp"},
    ("Firewall", "getFirewallLevel"): {},
    ("Firewall", "getFirewallIPv6Level"): {},
    ("Firewall", "getPortForwarding"): {},
    ("Firewall", "getPinhole"): {},
    ("Firewall", "getDMZ"): {},
    ("Firewall", "getRespondToPing"): {"sourceInterface": "data"},
    ("UPnP-IGD", "get"): {},
    ("RemoteAccess", "get"): {},
    ("NMC.Wifi", "get"): {},
    ("NMC.Guest", "get"): {},
    ("Devices", "get"): {"expression": "physical and !self and !voice"},
    ("SpeedTest", "getWANResults"): {},
    ("AutoDiag", "getDiagnosticsState"): {},
}

# Diagnostics that act on the box without changing its configuration. Fixed parameters.
DIAGNOSTIC_CALLS: dict[tuple[str, str], dict[str, Any]] = {
    ("AutoDiag", "executeDiagnostics"): {"id": "speedService", "usr": True},
}


class LiveboxError(Exception):
    """Generic Livebox error."""


class LiveboxAuthError(LiveboxError):
    """Login refused."""


class LiveboxForbiddenCall(LiveboxError):
    """A call outside the read-only allow-list was attempted."""


# transport(url, headers, body) -> (status, headers, text)
Transport = Callable[[str, dict[str, str], str], Awaitable[tuple[int, dict[str, str], str]]]


@dataclass
class LiveboxClient:
    """Minimal async sysbus client."""

    host: str
    password: str
    transport: Transport
    username: str = "admin"
    _context: str | None = field(default=None, init=False, repr=False)
    _cookie: str = field(default="", init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @property
    def url(self) -> str:
        host = self.host.strip().rstrip("/")
        return f"{host if host.startswith(('http://', 'https://')) else 'http://' + host}/ws"

    async def _login(self) -> None:
        body = json.dumps({
            "service": LOGIN_CALL[0],
            "method": LOGIN_CALL[1],
            "parameters": {"applicationName": APP_NAME, "username": self.username, "password": self.password},
        })
        status, headers, text = await self.transport(self.url, {
            "Accept": "*/*",
            "Authorization": "X-Sah-Login",
            "Content-Type": "application/x-sah-ws-4-call+json",
        }, body)
        try:
            data = json.loads(text)
        except ValueError as err:
            raise LiveboxError(f"Unexpected login response (HTTP {status})") from err
        context = (data.get("data") or {}).get("contextID")
        if not context:
            raise LiveboxAuthError("Login refused by the Livebox")
        self._context = context
        cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
        self._cookie = "; ".join(p.split(";", 1)[0].strip() for p in cookie.split(",") if "=" in p.split(";", 1)[0])

    async def _post(self, service: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        status, _, text = await self.transport(self.url, {
            "Accept": "*/*",
            "Authorization": f"X-Sah {self._context}",
            "X-Context": self._context or "",
            "Content-Type": "application/x-sah-ws-4-call+json; charset=UTF-8",
            "Cookie": self._cookie,
        }, json.dumps({"service": f"sysbus.{service}", "method": method, "parameters": params}))
        try:
            data = json.loads(text)
        except ValueError as err:
            raise LiveboxError(f"{service}:{method} invalid JSON (HTTP {status})") from err
        return data.get("result", data) if isinstance(data, dict) else {}

    @staticmethod
    def _auth_error(reply: dict[str, Any]) -> bool:
        errors = reply.get("errors") or []
        return any(e.get("error") == 13 or "permission" in str(e.get("description", "")).lower() for e in errors)

    async def call(self, service: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send an allow-listed read call, logging in when needed."""
        key = (service, method)
        if key not in ALLOWED_CALLS:
            raise LiveboxForbiddenCall(f"{service}:{method} is not an allowed read-only call")
        payload = dict(ALLOWED_CALLS[key]) if params is None else params
        return await self._send(service, method, payload)

    async def _send(self, service: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if not self._context:
                await self._login()
            reply = await self._post(service, method, payload)
            if self._auth_error(reply):
                await self._login()
                reply = await self._post(service, method, payload)
        errors = reply.get("errors") or []
        if errors:
            raise LiveboxError(f"{service}:{method} {errors[0].get('description', 'error')}")
        return reply

    async def try_call(self, service: str, method: str) -> tuple[bool, Any]:
        """Best-effort call: (ok, status-or-error-text)."""
        try:
            reply = await self.call(service, method)
        except LiveboxForbiddenCall:
            raise
        except (LiveboxError, asyncio.TimeoutError, OSError) as err:
            return False, str(err)
        return True, reply.get("status", reply.get("data"))

    # ------------------------------------------------------------------ speed test

    async def get_speedtest_results(self) -> dict[str, Any]:
        """Last speed test run by the Livebox (read-only). Rates in Mbit/s."""
        r = (await self.call("SpeedTest", "getWANResults")).get("status") or {}
        down, up = r.get("Downstream") or {}, r.get("Upstream") or {}

        def mbit(x: Any) -> float | None:
            try:
                return round(float(x) / 1000, 1)
            except (TypeError, ValueError):
                return None

        return {
            "state": r.get("status"),
            "download": mbit(down.get("rate")),
            "upload": mbit(up.get("rate")),
            "latency": down.get("latency"),
            "started": down.get("start"),
            "upload_started": up.get("start"),
        }

    async def start_speedtest(self) -> None:
        """Ask the Livebox to run its speed test (fixed diagnostic, no configuration change)."""
        state = (await self.call("AutoDiag", "getDiagnosticsState")).get("status") or {}
        if isinstance(state, dict) and state.get("DiagnosticsState") == "Requested":
            raise LiveboxError("A Livebox diagnostic is already running")
        key = ("AutoDiag", "executeDiagnostics")
        await self._send(*key, dict(DIAGNOSTIC_CALLS[key]))

    async def run_speedtest(self, timeout_s: float = 150, poll_s: float = 5) -> dict[str, Any]:
        """Run the Livebox speed test and wait for new results."""
        before = await self.get_speedtest_results()
        await self.start_speedtest()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        idle = 0
        while loop.time() < deadline:
            await asyncio.sleep(poll_s)
            res = await self.get_speedtest_results()
            if res["state"] == "Completed" and res["started"] and res["started"] != before["started"]:
                return res
            st = (await self.call("AutoDiag", "getDiagnosticsState")).get("status") or {}
            if isinstance(st, dict) and st.get("DiagnosticsState") != "Requested":
                idle += 1
                if idle >= 3:
                    raise LiveboxError(f"Livebox speed test ended without results ({st.get('DiagnosticsState')})")
        raise LiveboxError("Livebox speed test timed out")

    # ------------------------------------------------------------------ snapshots

    async def get_status(self) -> dict[str, Any]:
        """Connection status (WAN must answer; the rest is best-effort)."""
        wan = (await self.call("NMC", "getWANStatus")).get("data") or {}
        _, info = await self.try_call("DeviceInfo", "get")
        _, dhcp = await self.try_call("NeMo.Intf.data", "getMIBs")
        info = info if isinstance(info, dict) else {}
        dh = (((dhcp or {}).get("dhcp") or {}).get("dhcp_data") or {}) if isinstance(dhcp, dict) else {}
        return {
            "online": str(wan.get("LinkState", "")).lower() == "up"
            and str(wan.get("ConnectionState", "")).lower() in ("bound", "connected", "up"),
            "public_ip": wan.get("IPAddress") or None,
            "ipv6": wan.get("IPv6Address") or None,
            "link_type": wan.get("LinkType"),
            "gpon_state": wan.get("GponState"),
            "last_error": wan.get("LastConnectionError") or None,
            "model": info.get("ModelName") or info.get("ProductClass"),
            "firmware": info.get("SoftwareVersion"),
            "box_uptime": info.get("UpTime"),
            "wan_uptime": dh.get("Uptime"),
        }

    async def get_security(self) -> dict[str, Any]:
        """Security-relevant configuration, normalised. Unavailable parts are reported, not hidden."""
        out: dict[str, Any] = {"unavailable": []}

        async def grab(name: str, service: str, method: str) -> Any:
            ok, value = await self.try_call(service, method)
            if not ok:
                out["unavailable"].append(f"{name}: {value}")
                return None
            return value

        out["firewall_level"] = await grab("firewall_level", "Firewall", "getFirewallLevel")
        out["firewall_ipv6_level"] = await grab("firewall_ipv6_level", "Firewall", "getFirewallIPv6Level")
        out["respond_to_ping"] = await grab("respond_to_ping", "Firewall", "getRespondToPing")
        out["port_forwarding"] = normalise_rules(await grab("port_forwarding", "Firewall", "getPortForwarding"))
        out["ipv6_pinholes"] = normalise_rules(await grab("ipv6_pinholes", "Firewall", "getPinhole"))
        dmz = await grab("dmz", "Firewall", "getDMZ")
        out["dmz"] = normalise_dmz(dmz)
        upnp = await grab("upnp", "UPnP-IGD", "get")
        out["upnp_enabled"] = bool(upnp.get("Enable")) if isinstance(upnp, dict) and "Enable" in upnp else None
        remote = await grab("remote_access", "RemoteAccess", "get")
        out["remote_access_enabled"] = bool(remote.get("Enable")) if isinstance(remote, dict) and "Enable" in remote else None
        guest = await grab("guest_wifi", "NMC.Guest", "get")
        out["guest_wifi_enabled"] = bool(guest.get("Enable")) if isinstance(guest, dict) and "Enable" in guest else None
        return out

    async def get_devices(self) -> list[dict[str, Any]]:
        """Devices known to the Livebox (DHCP server: the Deco run as access points)."""
        ok, value = await self.try_call("Devices", "get")
        if not ok or not isinstance(value, list):
            return []
        devices = []
        for d in value:
            mac = (d.get("PhysAddress") or d.get("Key") or "").upper()
            if not mac:
                continue
            devices.append({
                "mac": mac,
                "name": d.get("Name") or d.get("HostName") or mac,
                "ip": d.get("IPAddress") or None,
                "active": bool(d.get("Active")),
                "interface": d.get("InterfaceName") or d.get("Layer2Interface") or None,
                "type": d.get("DeviceType") or None,
                "first_seen": d.get("FirstSeen") or None,
            })
        return devices


def normalise_rules(raw: Any) -> list[dict[str, Any]]:
    """Port-forwarding / pinhole rules as a stable, comparable list."""
    if not isinstance(raw, dict):
        return []
    rules = []
    for rid, r in raw.items():
        if not isinstance(r, dict):
            continue
        rules.append({
            "id": str(r.get("Id") or rid),
            "origin": r.get("Origin"),
            "enabled": bool(r.get("Enable", True)),
            "protocol": str(r.get("Protocol", "")),
            "external_port": str(r.get("ExternalPort") or r.get("DestinationPort") or ""),
            "internal_port": str(r.get("InternalPort") or ""),
            "destination": r.get("DestinationIPAddress") or r.get("DestinationIP") or "",
            "source": r.get("SourcePrefix") or r.get("SourceIPAddress") or "",
            "description": r.get("Description") or "",
        })
    return sorted(rules, key=lambda x: x["id"])


def normalise_dmz(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    return sorted(
        ({"id": str(k), "destination": v.get("DestinationIPAddress", ""), "enabled": bool(v.get("Enable", True))}
         for k, v in raw.items() if isinstance(v, dict)),
        key=lambda x: x["id"],
    )
