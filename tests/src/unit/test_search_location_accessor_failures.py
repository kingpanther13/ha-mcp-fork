"""Location-filter diagnostics for registry accessors that fail during search."""

from typing import Any

import pytest

from .test_component_ws_search import (
    FakeArea,
    FakeDevice,
    FakeFloor,
    FakeHass,
    FakeRegEntry,
    FakeState,
    make_view,
    wsapi,
)


def _search(monkeypatch: pytest.MonkeyPatch, view: Any) -> dict[str, Any]:
    monkeypatch.setattr(wsapi, "_resolve_registries", lambda hass: view)
    return wsapi._do_search(
        FakeHass(states=[FakeState("light.kitchen", "on")]),
        {"search_types": ["entity"], "area_filter": "Kitchen"},
    )


@pytest.mark.parametrize(
    ("registry_name", "method_name"),
    [
        ("entity", "async_get"),
        ("area", "async_get_area"),
        ("floor", "async_get_floor"),
    ],
)
def test_location_filter_reports_failed_registry_lookup(
    monkeypatch: pytest.MonkeyPatch, registry_name: str, method_name: str
) -> None:
    view = make_view(
        entity={"light.kitchen": FakeRegEntry("light.kitchen", area_id="kitchen")},
        areas=[FakeArea("kitchen", "Kitchen", "ground")],
        floors=[FakeFloor("ground", "Ground floor")],
    )

    def broken(_key: str) -> None:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(getattr(view, registry_name), method_name, broken)
    result = _search(monkeypatch, view)

    assert result["partial"] is True
    assert registry_name in result["partial_reason"]
    assert result["diagnostics"]["location_registries_unavailable"] == 1


def test_location_filter_reports_failed_device_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = make_view(
        entity={"light.kitchen": FakeRegEntry("light.kitchen", device_id="kitchen")},
        areas=[FakeArea("kitchen", "Kitchen", "ground")],
        floors=[FakeFloor("ground", "Ground floor")],
        devices=[FakeDevice("kitchen", area_id="kitchen")],
    )

    class BrokenCollection:
        def __iter__(self) -> Any:
            raise RuntimeError("device collection unavailable")

    view.device.devices = BrokenCollection()
    result = _search(monkeypatch, view)

    assert result["entities"] == []
    assert result["partial"] is True
    assert "device" in result["partial_reason"]
    assert result["diagnostics"]["location_registries_unavailable"] == 1
