"""School timetable ("Stundenplan") from the Home Assistant calendars of the WebUntis integration."""
from datetime import datetime, timedelta
import logging

import pytz
import requests

from plugins.base_plugin.base_plugin import BasePlugin, SkipRefresh
from plugins.timetable.lessons import child_column, day_title, parse_event, target_day

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DAY_MODES = ("today", "tomorrow")
FETCH_ERROR = "Failed to fetch the timetable calendars from Home Assistant. Check baseUrl and token."


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def configured_children(settings):
    names = as_list(settings.get("childName[]"))
    calendars = as_list(settings.get("childCalendar[]"))
    return [(name.strip(), calendar.strip()) for name, calendar in zip(names, calendars)
            if name.strip() and calendar.strip()]


class Timetable(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "Home Assistant",
            "expected_key": "HOME_ASSISTANT_TOKEN"
        }
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        base_url = settings.get("baseUrl")
        if not base_url:
            raise RuntimeError("Home Assistant base URL is required (e.g., http://homeassistant.local:8123)")
        token = device_config.load_env_key("HOME_ASSISTANT_TOKEN")
        if not token:
            raise RuntimeError("HOME_ASSISTANT_TOKEN is not configured. See docs/api_keys.md")
        children = configured_children(settings)
        if not children:
            raise RuntimeError("At least one child with a calendar entity is required.")
        mode = settings.get("day") if settings.get("day") in DAY_MODES else "today"

        tz = pytz.timezone(device_config.get_config("timezone", default="UTC"))
        now = self.current_time(tz)
        today = now.date()
        start = tz.localize(datetime.combine(today, datetime.min.time()))
        end = start + timedelta(days=2)

        # One request per child for today and tomorrow; a failing calendar only empties its column
        lessons = []
        for name, calendar in children:
            try:
                events = self.fetch_events(base_url, token, calendar, start, end)
                lessons.append([lesson for lesson in (parse_event(e, tz) for e in events) if lesson])
            except Exception as e:
                logger.error(f"Timetable calendar {calendar} could not be fetched: {e}")
                lessons.append(None)
        if all(child_lessons is None for child_lessons in lessons):
            raise RuntimeError(FETCH_ERROR)

        day = target_day([child_lessons for child_lessons in lessons if child_lessons is not None], today, mode)
        if day is None:
            raise SkipRefresh(f"no school {mode}")

        template_params = {
            "title": day_title(day, today),
            "timestamp": f"Stand {now:%H:%M}",
            "columns": [child_column(name, child_lessons, day)
                        for (name, _), child_lessons in zip(children, lessons)],
            "plugin_settings": settings,
        }
        return self.render_image(dimensions, "timetable.html", "timetable.css", template_params)

    def current_time(self, tz):
        return datetime.now(tz)

    def get_device_display_settings(self):
        return {'inky_saturation': 0.0}

    def fetch_events(self, base_url, token, calendar, start, end):
        url = f"{base_url.rstrip('/')}/api/calendars/{calendar}"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"start": start.isoformat(), "end": end.isoformat()}
        resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
