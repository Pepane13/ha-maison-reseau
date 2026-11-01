"""Tests for the read-only Livebox client (no Home Assistant needed)."""

import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "livebox", pathlib.Path(__file__).parents[1] / "custom_components/maison_reseau/livebox.py"
)
livebox = importlib.util.module_from_spec(_SPEC)
sys.modules['livebox'] = livebox
_SPEC.loader.exec_module(livebox)


class FakeBox:
    """Answers like a Livebox 5 and records every call."""

    def __init__(self, replies, password="secret"):
        self.replies = replies
        self.password = password
        self.calls = []
        self.logins = 0
        self.expire_once = False

    async def __call__(self, url, headers, body):
        req = json.loads(body)
        if req["service"] == "sah.Device.Information":
            self.logins += 1
            if req["parameters"]["password"] != self.password:
                return 200, {}, json.dumps({"status": 0, "errors": [{"error": 13, "description": "Permission denied"}]})
            return 200, {"Set-Cookie": "abc/sessid=XYZ; path=/"}, json.dumps({"status": 0, "data": {"contextID": "CTX"}})
        service, method = req["service"].removeprefix("sysbus."), req["method"]
        self.calls.append((service, method, req["parameters"], headers.get("X-Context")))
        if self.expire_once:
            self.expire_once = False
            return 200, {}, json.dumps({"result": {"errors": [{"error": 13, "description": "Permission denied"}]}})
        reply = self.replies.get((service, method))
        if reply is None:
            return 200, {}, json.dumps({"result": {"errors": [{"error": 196618, "description": "Object or function not found"}]}})
        return 200, {}, json.dumps({"result": reply})


def run(coro):
    return asyncio.run(coro)


REPLIES = {
    ("NMC", "getWANStatus"): {"status": True, "data": {"LinkState": "up", "ConnectionState": "Bound", "IPAddress": "86.198.172.93", "LinkType": "gpon", "GponState": "O5_Operation"}},
    ("DeviceInfo", "get"): {"status": {"ModelName": "Livebox 5", "SoftwareVersion": "SG_LB5_1.2.3", "UpTime": 3600}},
    ("Firewall", "getFirewallLevel"): {"status": "Medium"},
    ("Firewall", "getPortForwarding"): {"status": {"webui_Example": {"Id": "webui_Example", "Origin": "webui", "Enable": True, "Protocol": "6", "ExternalPort": "8443", "InternalPort": "443", "DestinationIPAddress": "192.168.1.200", "Description": "Example"}}},
    ("Firewall", "getDMZ"): {"status": {}},
    ("UPnP-IGD", "get"): {"status": {"Enable": False}},
    ("RemoteAccess", "get"): {"status": {"Enable": False}},
    ("Devices", "get"): {"status": [{"PhysAddress": "aa:bb:cc:dd:ee:ff", "Name": "Example camera", "IPAddress": "192.168.1.201", "Active": True}]},
}


def test_forbidden_call_is_blocked_before_any_request():
    box = FakeBox(REPLIES)
    client = livebox.LiveboxClient("192.168.1.1", "secret", box)
    with pytest.raises(livebox.LiveboxForbiddenCall):
        run(client.call("Firewall", "setFirewallLevel"))
    with pytest.raises(livebox.LiveboxForbiddenCall):
        run(client.call("NMC", "reboot"))
    assert box.calls == [] and box.logins == 0


def test_allow_list_contains_only_reads():
    for service, method in livebox.ALLOWED_CALLS:
        assert method.startswith("get"), f"{service}:{method} is not a read call"


def test_status_and_relogin_on_expired_context():
    box = FakeBox(REPLIES)
    client = livebox.LiveboxClient("http://192.168.1.1/", "secret", box)
    status = run(client.get_status())
    assert status["online"] is True and status["public_ip"] == "86.198.172.93"
    assert status["model"] == "Livebox 5"
    box.expire_once = True
    run(client.call("NMC", "getWANStatus"))
    assert box.logins == 2
    assert all(c[3] == "CTX" for c in box.calls)


def test_bad_password():
    client = livebox.LiveboxClient("192.168.1.1", "wrong", FakeBox(REPLIES))
    with pytest.raises(livebox.LiveboxAuthError):
        run(client.get_status())


def test_security_snapshot_reports_unavailable_parts():
    sec = run(livebox.LiveboxClient("192.168.1.1", "secret", FakeBox(REPLIES)).get_security())
    assert sec["firewall_level"] == "Medium"
    assert sec["port_forwarding"][0]["external_port"] == "8443"
    assert sec["dmz"] == [] and sec["upnp_enabled"] is False and sec["remote_access_enabled"] is False
    assert any(u.startswith("firewall_ipv6_level") for u in sec["unavailable"])


def test_devices():
    devices = run(livebox.LiveboxClient("192.168.1.1", "secret", FakeBox(REPLIES)).get_devices())
    assert devices[0]["mac"] == "AA:BB:CC:DD:EE:FF" and devices[0]["active"] is True
