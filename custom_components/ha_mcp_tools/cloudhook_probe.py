"""THROWAWAY dev-env probe for #2696 -- dropped before the PR.

Replays HA core's Nabu Casa cloudhook relay in-process, without a cloud
account: ``homeassistant.components.cloud.client.CloudClient.async_webhook_message``
builds a ``MockRequest``, calls ``webhook.async_handle_webhook`` and returns
``serialize_response(response)``.
"""

from __future__ import annotations

import traceback

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util.aiohttp import MockRequest, serialize_response

from .const import DATA_WEBHOOK, DOMAIN


def async_register_probe(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "cloudhook_probe"):
        return

    async def relay(call: ServiceCall) -> dict:
        cfg = hass.data.get(DOMAIN, {}).get(DATA_WEBHOOK) or {}
        webhook_id = call.data.get("webhook_id") or cfg.get("webhook_id")
        if not webhook_id:
            return {"error": "no in-process server webhook registered"}
        body = call.data.get("body") or '{"jsonrpc":"2.0","id":1,"method":"ping"}'
        request = MockRequest(
            content=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
            query_string="",
            mock_source="cloud",
            remote=None,
        )
        try:
            response = await webhook.async_handle_webhook(hass, webhook_id, request)
            response_dict = serialize_response(response)
        except Exception as err:  # noqa: BLE001 -- probe reports, never raises
            return {"error": repr(err), "traceback": traceback.format_exc()[-1200:]}
        return {
            "status": response_dict["status"],
            "body": response_dict.get("body"),
            "content_type": response.content_type,
        }

    hass.services.async_register(
        DOMAIN, "cloudhook_probe", relay, supports_response=SupportsResponse.ONLY
    )
