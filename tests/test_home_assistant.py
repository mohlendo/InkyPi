import pytest
import requests

from src.plugins.home_assistant.home_assistant import HomeAssistant


class FakeDeviceConfig:
    def get_resolution(self):
        return [800, 480]

    def get_config(self, key=None, default=None):
        return {"orientation": "horizontal"}.get(key, default)

    def load_env_key(self, key):
        return "dummy-token"


def http_error(status_code):
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} Error", response=response)


STATES = {
    "sensor.temp": {"state": "21.3", "attributes": {"friendly_name": "Temperatur", "unit_of_measurement": "°C"}},
    "sensor.humidity": {"state": "50.8", "attributes": {"friendly_name": "Luftfeuchte", "unit_of_measurement": "%"}},
}


@pytest.fixture
def plugin(monkeypatch):
    plugin = HomeAssistant({"id": "home_assistant"})
    rendered = {}

    def fake_render_image(dimensions, html_file, css_file, template_params):
        rendered["params"] = template_params
        return "image"

    monkeypatch.setattr(plugin, "render_image", fake_render_image)
    plugin.rendered = rendered
    return plugin


def settings(*entities):
    return {"baseUrl": "https://ha.example", "entityId[]": list(entities)}


def fake_states(errors):
    def get_entity_state(base_url, token, entity_id):
        if entity_id in errors:
            raise errors[entity_id]
        return STATES[entity_id]
    return get_entity_state


def test_missing_entity_is_skipped(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "get_entity_state", fake_states({"sensor.gone": http_error(404)}))

    image = plugin.generate_image(settings("sensor.gone", "sensor.temp", "sensor.humidity"), FakeDeviceConfig())

    assert image == "image"
    items = plugin.rendered["params"]["items"]
    assert [item["entity_id"] for item in items] == ["sensor.temp", "sensor.humidity"]


def test_all_entities_missing_raises(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "get_entity_state",
                        fake_states({"sensor.a": http_error(404), "sensor.b": http_error(404)}))

    with pytest.raises(RuntimeError, match="sensor.a, sensor.b"):
        plugin.generate_image(settings("sensor.a", "sensor.b"), FakeDeviceConfig())


@pytest.mark.parametrize("error", [
    http_error(401),
    http_error(500),
    requests.exceptions.ConnectionError("connection refused"),
])
def test_other_errors_still_fail(plugin, monkeypatch, error):
    monkeypatch.setattr(plugin, "get_entity_state", fake_states({"sensor.temp": error}))

    with pytest.raises(RuntimeError, match="Check baseUrl and token"):
        plugin.generate_image(settings("sensor.temp", "sensor.humidity"), FakeDeviceConfig())
