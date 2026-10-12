"""Experimental icon routing using the running Core collection's field names."""

from typing import Any

from ..component_helper_collections import fetch_helper_schemas


async def collection_icon(
    client: Any, helper_type: str, action: str, icon: str | None
) -> str | None:
    """Omit an icon only when Core's actual action schema has no icon field.

    Missing discovery retains native forwarding; it is not evidence that Core
    rejects a field. This deliberately exposes the no-component limitation of
    the proposed schema-based approach for the live experiment.
    """
    if icon is None:
        return None
    schemas = await fetch_helper_schemas(client)
    fields = schemas.get(helper_type, {}).get(action) if schemas else None
    if isinstance(fields, list) and not any(f.get("name") == "icon" for f in fields):
        return None
    return icon
