from datetime import datetime

import pytest
import pytz
import requests

from plugins.base_plugin.base_plugin import SkipRefresh
from plugins.timetable.timetable import Timetable

TZ = pytz.timezone("Europe/Berlin")
SETTINGS = {"baseUrl": "http://ha.local:8123/", "day": "tomorrow",
            "childName[]": ["Kind A", "Kind B"],
            "childCalendar[]": ["calendar.stundenplan_kind_a", "calendar.stundenplan_kind_b"]}


class FakeDeviceConfig:
    def get_resolution(self):
        return [800, 480]

    def get_config(self, key=None, default=None):
        return {"orientation": "horizontal", "timezone": "Europe/Berlin"}.get(key, default)

    def load_env_key(self, key):
        return "dummy-token"


def event(day, start, end, summary, location="R1"):
    return {"start": {"dateTime": f"{day}T{start}:00+01:00"}, "end": {"dateTime": f"{day}T{end}:00+01:00"},
            "summary": summary, "location": location, "description": ""}


CALENDARS = {
    "calendar.stundenplan_kind_a": [event("2026-11-11", "08:00", "08:45", "Musik"),
                                    event("2026-11-11", "08:45", "09:30", "Musik")],
    "calendar.stundenplan_kind_b": [event("2026-11-11", "09:50", "10:35", "Cancelled: Sport")],
}


@pytest.fixture
def plugin(monkeypatch):
    plugin = Timetable({"id": "timetable"})
    plugin.rendered = None

    def fake_render_image(dimensions, html_file, css_file, template_params):
        plugin.rendered = (dimensions, html_file, css_file, template_params)
        return "image"

    monkeypatch.setattr(plugin, "render_image", fake_render_image)
    monkeypatch.setattr(plugin, "current_time", lambda tz: TZ.localize(datetime(2026, 11, 10, 19, 45)))
    return plugin


def serve(calendars):
    def fetch_events(base_url, token, calendar, start, end):
        result = calendars[calendar]
        if isinstance(result, Exception):
            raise result
        return result
    return fetch_events


def test_renders_tomorrow_for_all_children(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "fetch_events", serve(CALENDARS))
    assert plugin.generate_image(SETTINGS, FakeDeviceConfig()) == "image"
    dimensions, html_file, css_file, params = plugin.rendered
    assert (dimensions, html_file, css_file) == ([800, 480], "timetable.html", "timetable.css")
    assert params["title"] == "MORGEN · MI 11.11."
    assert params["timestamp"] == "Stand 19:45"
    kid_a, kid_b = params["columns"]
    assert (kid_a["name"], kid_a["span"], [r["subject"] for r in kid_a["rows"]]) == ("Kind A", "8:00–9:30", ["Musik"])
    assert (kid_b["span"], kid_b["rows"][0]["tag"]) == ("", "ENTFALL")


def test_fetches_today_and_tomorrow(plugin, monkeypatch):
    windows = []

    def fetch_events(base_url, token, calendar, start, end):
        windows.append((start.isoformat(), end.isoformat()))
        return CALENDARS[calendar]

    monkeypatch.setattr(plugin, "fetch_events", fetch_events)
    plugin.generate_image(SETTINGS, FakeDeviceConfig())
    assert windows == [("2026-11-10T00:00:00+01:00", "2026-11-12T00:00:00+01:00")] * 2


def test_failing_calendar_only_affects_its_column(plugin, monkeypatch):
    calendars = dict(CALENDARS, **{"calendar.stundenplan_kind_b": requests.exceptions.ConnectionError("down")})
    monkeypatch.setattr(plugin, "fetch_events", serve(calendars))
    plugin.generate_image(SETTINGS, FakeDeviceConfig())
    assert [c["notice"] for c in plugin.rendered[3]["columns"]] == [None, "KEINE DATEN"]


def test_all_calendars_failing_raises(plugin, monkeypatch):
    down = requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(plugin, "fetch_events", serve({k: down for k in CALENDARS}))
    with pytest.raises(RuntimeError, match="Home Assistant"):
        plugin.generate_image(SETTINGS, FakeDeviceConfig())


def test_no_school_skips(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "fetch_events", serve({k: [] for k in CALENDARS}))
    with pytest.raises(SkipRefresh):
        plugin.generate_image(SETTINGS, FakeDeviceConfig())
    with pytest.raises(SkipRefresh):
        plugin.generate_image(dict(SETTINGS, day="today"), FakeDeviceConfig())


@pytest.mark.parametrize("change,message", [({"baseUrl": ""}, "base URL"),
                                            ({"childName[]": [], "childCalendar[]": []}, "child")])
def test_missing_settings_raise(plugin, change, message):
    with pytest.raises(RuntimeError, match=message):
        plugin.generate_image(dict(SETTINGS, **change), FakeDeviceConfig())


def test_single_child_settings_as_plain_strings(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "fetch_events", serve(CALENDARS))
    settings = dict(SETTINGS, **{"childName[]": "Kind A", "childCalendar[]": "calendar.stundenplan_kind_a"})
    plugin.generate_image(settings, FakeDeviceConfig())
    assert [c["name"] for c in plugin.rendered[3]["columns"]] == ["Kind A"]


def test_fetch_events_calls_the_calendar_api(plugin, monkeypatch):
    calls = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return ["event"]

    def fake_get(url, headers, params, timeout):
        calls.update(url=url, headers=headers, params=params)
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    start = TZ.localize(datetime(2026, 11, 10))
    end = TZ.localize(datetime(2026, 11, 15))
    assert plugin.fetch_events("http://ha.local:8123/", "tok", "calendar.x", start, end) == ["event"]
    assert calls["url"] == "http://ha.local:8123/api/calendars/calendar.x"
    assert calls["headers"]["Authorization"] == "Bearer tok"
    assert calls["params"] == {"start": "2026-11-10T00:00:00+01:00", "end": "2026-11-15T00:00:00+01:00"}


def test_uses_pure_palette_without_dithering(plugin):
    assert plugin.get_device_display_settings() == {"inky_saturation": 0.0}
