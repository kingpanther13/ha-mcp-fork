"""Merging of ``ha_manage_app`` config-mode options into an app's current ones."""


def merge_options(base: dict, override: dict) -> dict:
    """Merge caller options into current options with one-level deep merge.

    Top-level scalar values are replaced. Top-level dict values are merged
    one level deep so callers can update a single nested field (e.g.
    ``{"ssh": {"sftp": True}}``) without losing sibling fields. A ``None``
    at either level removes the key, unsetting that option.
    """
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict):
            current = merged.get(key)
            nested = {**current, **value} if isinstance(current, dict) else value
            merged[key] = {k: v for k, v in nested.items() if v is not None}
        else:
            merged[key] = value
    return merged
