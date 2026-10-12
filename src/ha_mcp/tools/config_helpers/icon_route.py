"""Experimental icon routing using the running Core collection's field names."""

from typing import Any

from ..component_helper_collections import fetch_helper_schemas


async def collection_icon(
    client: Any, helper_type: str, action: str, icon: str | None
) -> str | None:
    """Use a declared collection field, otherwise the native registry API.

    Missing discovery says nothing about collection support. The explicit
    fallback writes entity metadata to the registry, as entity settings do.
    Clearing retains the existing stored-item merge behavior: remove the key
    for replacing collections and retain the zone safeguard for merging ones.
    """
    # The existing zone policy must see the requested icon to preserve/update
    # an already stored icon and refuse a clear that Core cannot apply (#2643).
    if icon is None or icon == "" or helper_type == "zone":
        return icon
    schemas = await fetch_helper_schemas(client)
    fields = schemas.get(helper_type, {}).get(action) if schemas else None
    if isinstance(fields, list) and any(f.get("name") == "icon" for f in fields):
        return icon
    return None
