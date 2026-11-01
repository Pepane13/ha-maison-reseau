"""Config flow: host + Livebox admin password (stored only in the config entry)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_HOST, CONF_PASSWORD, DEFAULT_HOST, DOMAIN
from .coordinator import build_client
from .livebox import LiveboxAuthError, LiveboxError


class MaisonReseauConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check(self, data: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        try:
            status = await build_client(self.hass, data).get_status()
        except LiveboxAuthError:
            return "invalid_auth", None
        except (LiveboxError, TimeoutError, OSError):
            return "cannot_connect", None
        return None, status

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"livebox_{user_input[CONF_HOST]}")
            self._abort_if_unique_id_configured()
            error, status = await self._check(user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title=status.get("model") or "Livebox", data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, DEFAULT_HOST)): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            error, _ = await self._check(data)
            if not error:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}), errors=errors)
