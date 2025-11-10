from plugins.base_plugin.base_plugin import BasePlugin
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10

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

        # Settings: list of entity ids; allow multiple values using "entityId[]" pattern used elsewhere
        entities = settings.get("entityId[]") or settings.get("entityId")
        if isinstance(entities, str):
            entities = [entities]
        if not entities:
            raise RuntimeError("At least one Home Assistant entityId is required.")

        # Optional list of attributes to display per entity (same attributes for all) e.g. ["battery_level","humidity"]
        attributes = settings.get("attribute[]") or []
        if isinstance(attributes, str):
            attributes = [attributes]

        # Fetch states for each entity
        items = []
        try:
            for eid in entities:
                state_obj = self.get_entity_state(base_url, token, eid)
                if not state_obj:
                    continue
                attrs = state_obj.get("attributes", {})
                # friendly name or entity id
                name = attrs.get("friendly_name", eid)
                state = state_obj.get("state")
                unit = attrs.get("unit_of_measurement", "")

                # Build attribute list according to user selection; if none selected, pick a few common ones
                display_attrs = []
                keys = attributes or [k for k in ("battery_level","humidity","temperature","pressure","power","voltage") if k in attrs]
                for k in keys:
                    if k in attrs:
                        display_attrs.append({"key": k.replace("_", " ").title(), "value": attrs[k]})

                items.append({
                    "entity_id": eid,
                    "name": name,
                    "state": state,
                    "unit": unit,
                    "attributes": display_attrs
                })
        except Exception as e:
            logger.error(f"Home Assistant request failed: {e}")
            raise RuntimeError("Failed to fetch data from Home Assistant. Check baseUrl and token.")

        # Build template params
        title = settings.get("title") or "Home Assistant"
        time_format = settings.get("timeFormat") or "%Y-%m-%d %H:%M"
        template_params = {
            "title": title,
            "items": items,
            "last_refresh_time": datetime.now().strftime(time_format),
            "plugin_settings": settings
        }

        # Render via HTML template similar to weather plugin
        return self.render_image(dimensions, "home_assistant.html", "home_assistant.css", template_params)

    def get_entity_state(self, base_url, token, entity_id):
        url = f"{base_url.rstrip('/')}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
