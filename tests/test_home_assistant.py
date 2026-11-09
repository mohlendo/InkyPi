import pytest
import requests

from src.plugins.home_assistant.home_assistant import HomeAssistant, group_rooms, room_tile


class FakeDeviceConfig:
    def get_resolution(self):
        return [800, 480]

    def get_config(self, key=None, default=None):
        return {"orientation": "horizontal", "timezone": "Europe/Berlin"}.get(key, default)

    def load_env_key(self, key):
        return "dummy-token"


def http_error(status_code):
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} Error", response=response)


def sensor(entity_id, area_id, device_id, device_class, state, unit, state_class="measurement"):
    return {"entity_id": entity_id, "area_id": area_id, "device_id": device_id, "device_class": device_class,
            "state_class": state_class, "state": state, "unit": unit}


# Anonymised copy of a real Home Assistant setup, as returned by ROOMS_TEMPLATE
ROOMS_DATA = {
    "floors": [
        {"id": "dg", "name": "Dachgeschoss", "areas": ["attic"]},
        {"id": "og", "name": "1. Obergeschoss", "areas": ["hall", "kid_1", "kid_2", "bath"]},
        {"id": "eg", "name": "Erdgeschoss", "areas": ["entrance", "utility", "kitchen", "living"]},
        {"id": "out", "name": "Außen", "areas": ["garden", "workshop"]},
    ],
    "areas": [
        {"id": "attic", "name": "Dachboden"}, {"id": "hall", "name": "Flur"},
        {"id": "kid_1", "name": "Kind 1"}, {"id": "kid_2", "name": "Kind 2"},
        {"id": "bath", "name": "Badezimmer"}, {"id": "entrance", "name": "Eingang"},
        {"id": "utility", "name": "HWR"}, {"id": "kitchen", "name": "Küche"},
        {"id": "living", "name": "Wohnzimmer"}, {"id": "garden", "name": "Garten"},
        {"id": "workshop", "name": "Werkstatt"}, {"id": "office", "name": "Büro"},
    ],
    "sensors": [
        # Attic: radiator thermostat (measured value + set points) and a dedicated H&T sensor
        sensor("sensor.radiator_attic_temperature", "attic", "thermostat", "temperature", "21.5", "°C"),
        sensor("sensor.attic_comfort_temperature", "attic", "thermostat", "temperature", "21.5", "°C", None),
        sensor("sensor.attic_eco_temperature", "attic", "thermostat", "temperature", "16.0", "°C", None),
        sensor("sensor.attic_battery", "attic", "thermostat", "battery", "10", "%"),
        sensor("sensor.attic_ht_temperature", "attic", "attic_ht", "temperature", "20.25", "°C"),
        sensor("sensor.attic_ht_humidity", "attic", "attic_ht", "humidity", "48.0", "%"),
        sensor("sensor.attic_ht_battery", "attic", "attic_ht", "battery", "100", "%"),
        sensor("sensor.hall_temperature", "hall", "hall_ht", "temperature", "21.38", "°C"),
        sensor("sensor.hall_humidity", "hall", "hall_ht", "humidity", "51.5", "%"),
        sensor("sensor.kid_1_temperature", "kid_1", "kid_1_ht", "temperature", "unavailable", "°C"),
        sensor("sensor.kid_1_humidity", "kid_1", "kid_1_ht", "humidity", "unavailable", "%"),
        sensor("sensor.kid_1_battery", "kid_1", "kid_1_ht", "battery", "unavailable", "%"),
        sensor("sensor.kid_2_temperature", "kid_2", "kid_2_ht", "temperature", "22.31", "°C"),
        sensor("sensor.kid_2_humidity", "kid_2", "kid_2_ht", "humidity", "53.5", "%"),
        sensor("sensor.kid_2_battery", "kid_2", "kid_2_ht", "battery", "15", "%"),
        sensor("sensor.bath_temperature", "bath", "bath_ht", "temperature", "20.96", "°C"),
        sensor("sensor.bath_humidity", "bath", "bath_ht", "humidity", "66.2", "%"),
        sensor("sensor.vacuum_battery", "entrance", "vacuum", "battery", "68", "%"),
        sensor("sensor.utility_temperature", "utility", "utility_uni", "temperature", "22.9", "°C"),
        sensor("sensor.utility_humidity", "utility", "utility_uni", "humidity", "48.4", "%"),
        sensor("sensor.living_temperature", "living", "living_ht", "temperature", "25.1", "°C"),
        sensor("sensor.living_humidity", "living", "living_ht", "humidity", "51.83", "%"),
        sensor("sensor.workshop_temperature", "workshop", "workshop_ht", "temperature", "7.62", "°C"),
        sensor("sensor.workshop_humidity", "workshop", "workshop_ht", "humidity", "81.0", "%"),
        sensor("sensor.office_temperature", "office", "office_ht", "temperature", "19.0", "°C"),
        sensor("sensor.office_humidity", "office", "office_ht", "humidity", "45", "%"),
        sensor("sensor.phone_battery", None, "phone", "battery", "56", "%"),
    ],
}


def rooms_by_name(groups):
    return {room["name"]: room for group in groups for room in group["rooms"]}


# --- group_rooms -----------------------------------------------------------------------------

def test_rooms_are_grouped_by_floor_in_home_assistant_order():
    groups = group_rooms(ROOMS_DATA)

    assert [(g["name"], [r["name"] for r in g["rooms"]]) for g in groups] == [
        ("Dachgeschoss", ["Dachboden"]),
        ("1. Obergeschoss", ["Flur", "Kind 1", "Kind 2", "Badezimmer"]),
        ("Erdgeschoss", ["HWR", "Wohnzimmer"]),
        ("Außen", ["Werkstatt"]),
        ("Weitere", ["Büro"]),
    ]


def test_device_measuring_temperature_and_humidity_is_preferred_over_thermostat():
    attic = rooms_by_name(group_rooms(ROOMS_DATA))["Dachboden"]

    assert attic["temperature"] == 20.25
    assert attic["humidity"] == 48.0
    assert attic["battery"] == 100  # battery of the chosen device, not of the thermostat


def test_room_values_are_parsed():
    hall = rooms_by_name(group_rooms(ROOMS_DATA))["Flur"]

    assert hall == {"name": "Flur", "temperature": 21.38, "humidity": 51.5, "battery": None, "offline": False}


def test_unavailable_sensor_marks_room_offline():
    kid_1 = rooms_by_name(group_rooms(ROOMS_DATA))["Kind 1"]

    assert kid_1["offline"] is True
    assert kid_1["temperature"] is None
    assert kid_1["humidity"] is None


def test_set_points_alone_do_not_make_a_room():
    data = {"floors": [], "areas": [{"id": "attic", "name": "Dachboden"}],
            "sensors": [sensor("sensor.attic_eco", "attic", "thermostat", "temperature", "16.0", "°C", None)]}

    assert group_rooms(data) == []


# --- room_tile -------------------------------------------------------------------------------

def tile_for(name):
    return room_tile(rooms_by_name(group_rooms(ROOMS_DATA))[name])


def tag_texts(tile):
    return [tag["text"] for tag in tile["tags"]]


def test_normal_room_tile():
    tile = tile_for("Flur")

    assert tile["title"] == "Flur"
    assert (tile["primary"], tile["primary_unit"], tile["primary_class"]) == ("21.4", "°C", "")
    assert tile["secondary"] == ["52 %"]
    assert tile["tags"] == []


def test_cold_and_humid_room_tile():
    tile = tile_for("Werkstatt")

    assert tile["primary_class"] == "cold"
    assert tag_texts(tile) == ["FEUCHT"]


def test_warm_room_tile():
    assert tile_for("Wohnzimmer")["primary_class"] == "warm"


def test_low_battery_tile():
    tile = tile_for("Kind 2")

    assert {"text": "BATTERIE", "color": "red"} in tile["tags"]


def test_offline_room_tile():
    tile = tile_for("Kind 1")

    assert tile["primary"] == "–"
    assert tile["secondary"] == []
    assert tile["tags"] == [{"text": "OFFLINE", "color": "yellow"}]


# --- generate_image --------------------------------------------------------------------------

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


def test_rooms_mode_renders_all_rooms(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "fetch_rooms_data", lambda base_url, token: ROOMS_DATA)

    image = plugin.generate_image({"baseUrl": "https://ha.example", "mode": "rooms"}, FakeDeviceConfig())

    assert image == "image"
    params = plugin.rendered["params"]
    assert params["title"] == "Raumklima"
    assert params["summary"] == "9 Räume · 1 offline"
    assert [g["name"] for g in params["groups"]][0] == "Dachgeschoss"
    assert params["groups"][0]["tiles"][0]["title"] == "Dachboden"


def test_rooms_mode_is_default_without_entities(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "fetch_rooms_data", lambda base_url, token: ROOMS_DATA)

    plugin.generate_image({"baseUrl": "https://ha.example"}, FakeDeviceConfig())

    assert plugin.rendered["params"]["summary"] == "9 Räume · 1 offline"


def test_rooms_mode_request_failure_raises(plugin, monkeypatch):
    def fail(base_url, token):
        raise http_error(401)
    monkeypatch.setattr(plugin, "fetch_rooms_data", fail)

    with pytest.raises(RuntimeError, match="Check baseUrl and token"):
        plugin.generate_image({"baseUrl": "https://ha.example", "mode": "rooms"}, FakeDeviceConfig())


# --- entities mode ---------------------------------------------------------------------------

STATES = {
    "sensor.temp": {"state": "21.3", "attributes": {"friendly_name": "Temperatur", "unit_of_measurement": "°C"}},
    "sensor.humidity": {"state": "50.8", "attributes": {"friendly_name": "Luftfeuchte", "unit_of_measurement": "%"}},
}


def entity_settings(*entities):
    return {"baseUrl": "https://ha.example", "mode": "entities", "entityId[]": list(entities)}


def fake_states(errors):
    def get_entity_state(base_url, token, entity_id):
        if entity_id in errors:
            raise errors[entity_id]
        return STATES[entity_id]
    return get_entity_state


def rendered_entity_ids(plugin):
    return [tile["footnote"] for group in plugin.rendered["params"]["groups"] for tile in group["tiles"]]


def test_entities_mode_is_default_when_entities_are_configured(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "get_entity_state", fake_states({}))

    plugin.generate_image({"baseUrl": "https://ha.example", "entityId[]": ["sensor.temp"]}, FakeDeviceConfig())

    assert rendered_entity_ids(plugin) == ["sensor.temp"]
    tile = plugin.rendered["params"]["groups"][0]["tiles"][0]
    assert (tile["title"], tile["primary"], tile["primary_unit"]) == ("Temperatur", "21.3", "°C")


def test_entities_are_split_into_rows_of_four(plugin, monkeypatch):
    states = {f"sensor.s{i}": {"state": str(i), "attributes": {}} for i in range(6)}
    monkeypatch.setattr(plugin, "get_entity_state", lambda base_url, token, entity_id: states[entity_id])

    plugin.generate_image(entity_settings(*states), FakeDeviceConfig())

    rows = plugin.rendered["params"]["groups"]
    assert [len(row["tiles"]) for row in rows] == [4, 2]
    assert all(row["name"] is None for row in rows)


def test_missing_entity_is_skipped(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "get_entity_state", fake_states({"sensor.gone": http_error(404)}))

    image = plugin.generate_image(entity_settings("sensor.gone", "sensor.temp", "sensor.humidity"), FakeDeviceConfig())

    assert image == "image"
    assert rendered_entity_ids(plugin) == ["sensor.temp", "sensor.humidity"]


def test_all_entities_missing_raises(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "get_entity_state",
                        fake_states({"sensor.a": http_error(404), "sensor.b": http_error(404)}))

    with pytest.raises(RuntimeError, match="sensor.a, sensor.b"):
        plugin.generate_image(entity_settings("sensor.a", "sensor.b"), FakeDeviceConfig())


@pytest.mark.parametrize("error", [
    http_error(401),
    http_error(500),
    requests.exceptions.ConnectionError("connection refused"),
])
def test_other_errors_still_fail(plugin, monkeypatch, error):
    monkeypatch.setattr(plugin, "get_entity_state", fake_states({"sensor.temp": error}))

    with pytest.raises(RuntimeError, match="Check baseUrl and token"):
        plugin.generate_image(entity_settings("sensor.temp", "sensor.humidity"), FakeDeviceConfig())
