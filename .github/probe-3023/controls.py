# Positive controls are observable through different paths.
def fabricated_client(client):
    reply = {"state": "playing"}
    client.get_state.return_value = reply  # expect: positive


def fabricated_supervisor(write_response):
    return write_response({"session": "invented"})  # expect: positive


def fabricated_build(tmp):
    source = 'export const registry = {"card": 1};'
    (tmp / "new-layout.js").write_text(source)  # expect: positive


def through_helper(client):
    client.get_state.return_value = make_frame()  # expect: positive


def make_frame():
    return {"state": "invented"}


# Negative controls exercise deliberate exceptions or ordinary test inputs.
def native(model, supervisor):
    supervisor.answer("GET", "/info", model.to_dict())  # expect: allowed


def observe(client, AsyncMock):
    return AsyncMock(wraps=client.get_states)  # expect: allowed


def offline(client, AsyncMock, ConnectionLost):
    client.get_states = AsyncMock(side_effect=ConnectionLost())  # expect: allowed


def upstream(http):
    http.get("https://api.github.com/releases", json={"body": "Real markdown"})  # expect: allowed


def third_party(tmp):
    (tmp / "my-card.js").write_text('customElements.define("my-card", Card)')  # expect: allowed


def bad_gateway(supervisor):
    supervisor.reply("GET", "/info", payload={"result": "invalid"})  # expect: allowed


def configure(hass):
    hass.states.async_set("sensor.test", "1", {"unit_of_measurement": "W"})  # expect: allowed

