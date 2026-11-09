from plugins.base_plugin.base_plugin import BasePlugin
from collections import defaultdict
from datetime import datetime
import json
import logging

import pytz
import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DEFAULT_TIME_FORMAT = "%d.%m.%Y %H:%M"

# Signal thresholds for the room view
COLD_BELOW = 18.0       # °C, temperature shown in blue
WARM_ABOVE = 24.0       # °C, temperature shown in red
HUMID_FROM = 65.0       # %, yellow FEUCHT tag
BATTERY_LOW_BELOW = 20  # %, red BATTERIE tag

OTHER_FLOOR_NAME = "Weitere"
ENTITY_TILES_PER_ROW = 4
UNAVAILABLE_STATES = {None, "", "unavailable", "unknown"}
FETCH_ERROR = "Failed to fetch data from Home Assistant. Check baseUrl and token."

# Rendered by Home Assistant (POST /api/template): floors in the order configured in Home Assistant,
# all areas, and every temperature/humidity/battery sensor with its area and device - in one request.
ROOMS_TEMPLATE = """
{%- set ns = namespace(floors=[], areas=[], sensors=[]) -%}
{%- for f in floors() -%}
  {%- set ns.floors = ns.floors + [{'id': f, 'name': floor_name(f), 'areas': floor_areas(f)}] -%}
{%- endfor -%}
{%- for a in areas() -%}
  {%- set ns.areas = ns.areas + [{'id': a, 'name': area_name(a)}] -%}
{%- endfor -%}
{%- for s in states.sensor if s.attributes.get('device_class') in ['temperature', 'humidity', 'battery'] -%}
  {%- set ns.sensors = ns.sensors + [{
        'entity_id': s.entity_id,
        'area_id': area_id(s.entity_id),
        'device_id': device_id(s.entity_id),
        'device_class': s.attributes.get('device_class'),
        'state_class': s.attributes.get('state_class'),
        'state': s.state,
        'unit': s.attributes.get('unit_of_measurement')}] -%}
{%- endfor -%}
{{ {'floors': ns.floors, 'areas': ns.areas, 'sensors': ns.sensors} | tojson }}
"""


def parse_number(state):
    if state in UNAVAILABLE_STATES:
        return None
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def build_room(name, sensors):
    """Picks the climate sensors of one area. Returns None if the area has no climate sensor.

    Only real measurements count (state_class "measurement"), so thermostat set points are ignored.
    A device that measures temperature and humidity (e.g. an H&T sensor) is preferred over others.
    """
    measured = [s for s in sensors if s.get("state_class") == "measurement"]
    temps = [s for s in measured if s.get("device_class") == "temperature"]
    hums = [s for s in measured if s.get("device_class") == "humidity"]
    if not temps and not hums:
        return None

    hum_devices = {s.get("device_id") for s in hums if s.get("device_id")}
    preferred = next((s["device_id"] for s in temps if s.get("device_id") in hum_devices), None)

    def pick(candidates):
        if not candidates:
            return None
        return next((s for s in candidates if preferred and s.get("device_id") == preferred), candidates[0])

    temp, hum = pick(temps), pick(hums)
    devices = {s.get("device_id") for s in (temp, hum) if s and s.get("device_id")}
    battery = next((s for s in sensors if s.get("device_class") == "battery" and s.get("device_id") in devices), None)

    chosen = [s for s in (temp, hum) if s]
    return {
        "name": name,
        "temperature": parse_number(temp["state"]) if temp else None,
        "humidity": parse_number(hum["state"]) if hum else None,
        "battery": parse_number(battery["state"]) if battery else None,
        "offline": any(s.get("state") in UNAVAILABLE_STATES for s in chosen),
    }


def group_rooms(data):
    """Groups the rooms (areas with climate sensors) by floor, in Home Assistant's floor order.

    Areas without a floor are collected in a trailing group.
    """
    area_names = {a["id"]: a["name"] for a in data.get("areas", [])}
    sensors_by_area = defaultdict(list)
    for s in data.get("sensors", []):
        if s.get("area_id"):
            sensors_by_area[s["area_id"]].append(s)

    def rooms_for(area_ids):
        rooms = (build_room(area_names.get(a, a), sensors_by_area.get(a, [])) for a in area_ids)
        return [r for r in rooms if r]

    groups = []
    placed = set()
    for floor in data.get("floors", []):
        placed.update(floor.get("areas", []))
        rooms = rooms_for(floor.get("areas", []))
        if rooms:
            groups.append({"name": floor["name"], "rooms": rooms})

    rest = rooms_for([a["id"] for a in data.get("areas", []) if a["id"] not in placed])
    if rest:
        groups.append({"name": OTHER_FLOOR_NAME if groups else None, "rooms": rest})
    return groups


def room_tile(room):
    temp, hum, battery = room["temperature"], room["humidity"], room["battery"]

    tags = []
    if room["offline"]:
        tags.append({"text": "OFFLINE", "color": "yellow"})
    if hum is not None and hum >= HUMID_FROM:
        tags.append({"text": "FEUCHT", "color": "yellow"})
    if battery is not None and battery < BATTERY_LOW_BELOW:
        tags.append({"text": "BATTERIE", "color": "red"})

    primary_class = ""
    if temp is not None and temp < COLD_BELOW:
        primary_class = "cold"
    elif temp is not None and temp > WARM_ABOVE:
        primary_class = "warm"

    return {
        "title": room["name"],
        "primary": f"{temp:.1f}" if temp is not None else "–",
        "primary_unit": "°C" if temp is not None else "",
        "primary_class": primary_class,
        "secondary": [f"{hum:.0f} %"] if hum is not None else [],
        "tags": tags,
        "footnote": "",
    }


def as_list(value):
    if isinstance(value, str):
        value = [value]
    return [v for v in (value or []) if v]


class HomeAssistant(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        # Advertise required API key
        template_params['api_key'] = {
            "required": True,
            "service": "Home Assistant",
            "expected_key": "HOME_ASSISTANT_TOKEN"
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        # Determine canvas size considering orientation
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        base_url = settings.get("baseUrl")
        if not base_url:
            raise RuntimeError("Home Assistant base URL is required (e.g., http://homeassistant.local:8123)")

        token = device_config.load_env_key("HOME_ASSISTANT_TOKEN")
        if not token:
            raise RuntimeError("HOME_ASSISTANT_TOKEN is not configured. See docs/api_keys.md")

        entities = as_list(settings.get("entityId[]") or settings.get("entityId"))
        mode = settings.get("mode")
        if mode not in ("rooms", "entities"):
            mode = "entities" if entities else "rooms"

        if mode == "rooms":
            groups, summary = self.room_groups(base_url, token)
            default_title = "Raumklima"
        else:
            groups, summary = self.entity_groups(base_url, token, entities, as_list(settings.get("attribute[]"))), ""
            default_title = "Home Assistant"

        timezone = pytz.timezone(device_config.get_config("timezone", default="UTC"))
        template_params = {
            "title": settings.get("title") or default_title,
            "summary": summary,
            "timestamp": datetime.now(timezone).strftime(settings.get("timeFormat") or DEFAULT_TIME_FORMAT),
            "groups": groups,
            "plugin_settings": settings
        }
        return self.render_image(dimensions, "home_assistant.html", "home_assistant.css", template_params)

    def room_groups(self, base_url, token):
        try:
            data = self.fetch_rooms_data(base_url, token)
        except Exception as e:
            logger.error(f"Home Assistant request failed: {e}")
            raise RuntimeError(FETCH_ERROR)

        floors = group_rooms(data)
        if not floors:
            raise RuntimeError("No rooms with temperature or humidity sensors found in Home Assistant.")

        rooms = [room for floor in floors for room in floor["rooms"]]
        offline = sum(1 for room in rooms if room["offline"])
        summary = f"{len(rooms)} {'Raum' if len(rooms) == 1 else 'Räume'}"
        if offline:
            summary += f" · {offline} offline"

        groups = [{"name": floor["name"], "tiles": [room_tile(room) for room in floor["rooms"]]} for floor in floors]
        return groups, summary

    def entity_groups(self, base_url, token, entities, attributes):
        if not entities:
            raise RuntimeError("At least one Home Assistant entityId is required.")

        # Entities that no longer exist (HTTP 404, e.g. after a rename in Home Assistant) are skipped so
        # the remaining ones are still shown. Any other error (auth, connection, server) fails the refresh.
        tiles = []
        missing = []
        for eid in entities:
            try:
                state_obj = self.get_entity_state(base_url, token, eid)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    logger.warning(f"Home Assistant entity not found, skipping: {eid}")
                    missing.append(eid)
                    continue
                logger.error(f"Home Assistant request failed: {e}")
                raise RuntimeError(FETCH_ERROR)
            except Exception as e:
                logger.error(f"Home Assistant request failed: {e}")
                raise RuntimeError(FETCH_ERROR)

            if not state_obj:
                continue
            attrs = state_obj.get("attributes", {})
            state = state_obj.get("state")
            offline = state in UNAVAILABLE_STATES

            # Attributes according to user selection; if none selected, pick a few common ones
            keys = attributes or [k for k in ("battery_level", "humidity", "temperature", "pressure", "power", "voltage") if k in attrs]
            tiles.append({
                "title": attrs.get("friendly_name", eid),
                "primary": "–" if offline else state,
                "primary_unit": "" if offline else attrs.get("unit_of_measurement", ""),
                "primary_class": "",
                "secondary": [f"{k.replace('_', ' ').title()}: {attrs[k]}" for k in keys if k in attrs],
                "tags": [{"text": "OFFLINE", "color": "yellow"}] if offline else [],
                "footnote": eid,
            })

        if not tiles and missing:
            raise RuntimeError(f"None of the configured Home Assistant entities were found: {', '.join(missing)}")

        return [{"name": None, "tiles": tiles[i:i + ENTITY_TILES_PER_ROW]}
                for i in range(0, len(tiles), ENTITY_TILES_PER_ROW)]

    def get_device_display_settings(self):
        return {'inky_saturation': 0.0}

    def fetch_rooms_data(self, base_url, token):
        url = f"{base_url.rstrip('/')}/api/template"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post(url, headers=headers, json={"template": ROOMS_TEMPLATE}, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return json.loads(resp.text)

    def get_entity_state(self, base_url, token, entity_id):
        url = f"{base_url.rstrip('/')}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
