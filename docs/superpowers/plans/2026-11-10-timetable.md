# School Timetable View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the children's WebUntis timetables (read from Home Assistant calendars) on the Inky in the morning
and the evening, and skip the view on days without school.

**Architecture:**
- A new plugin `timetable`:
  - `lessons.py` holds the pure lesson logic and the view model.
  - `timetable.py` holds the Home Assistant I/O and the rendering.
- A small core change lets any plugin raise `SkipRefresh`. Playlist refreshes then fall through to the next plugin
  of the playlist, and after that to the other active playlists.
- The schedule is plain device config on the Pi: two short playlists.

**Tech Stack:** Python 3.13, Flask/Jinja (InkyPi), requests, pytz, pytest, headless Chromium for rendering.

**Spec:** `docs/superpowers/specs/2026-11-10-timetable-design.md`

> **Amendment (2026-11-10, after Task 4):**
> - The day mode `next_school_day` (4-day look-ahead) was replaced by `tomorrow` (Friday and Saturday evening
>   are skipped). `LOOKAHEAD_DAYS` and the weekday-name title are gone. The plugin fetches today and tomorrow
>   only.
> - The code blocks below show the original version. The committed code and tests are authoritative.

## Global Constraints

- Run tests with `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest` (repo root). New tests import
  top-level modules (`plugins.…`, `model`, `refresh_task`), never `src.…`.
- No private data in the repo: test fixtures use `Kind A` / `Kind B`, `calendar.stundenplan_kind_a`, generic
  rooms. Real names and the Home Assistant host only go into `device.json` on the Pi.
- Styling follows the approved V2 style:
  - Courier Prime, pure palette colors only (`#000 #fff #ff0 #f00`)
  - text black on white or yellow, or yellow on black
  - color only for fills and markers
- The plugin returns `{'inky_saturation': 0.0}` from `get_device_display_settings`.
- Core diffs (`model.py`, `refresh_task.py`, `base_plugin.py`) stay minimal and generic.
- Summary prefixes of the WebUntis integration: `Cancelled: `, `Irregular: `, `Room change: `. Tags: `ENTFALL`,
  `VERTR.`, `RAUM`.
- Look-ahead for `next_school_day`: 4 days. Double-period merge gap: up to 5 minutes.
- Commit messages are English, without attribution lines. Work on branch `dev`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/plugins/base_plugin/base_plugin.py` (modify) | add `SkipRefresh` exception |
| `src/model.py` (modify) | `PlaylistManager.determine_active_playlists()` |
| `src/refresh_task.py` (modify) | skip fallback for playlist refreshes, quiet logging for manual skips |
| `tests/test_refresh_skip.py` (create) | fallback behavior |
| `src/plugins/timetable/lessons.py` (create) | parse events, group, merge, span, target day, view model |
| `tests/test_timetable_lessons.py` (create) | unit tests for `lessons.py` |
| `src/plugins/timetable/timetable.py` (create) | plugin class: settings, Home Assistant calendar fetch, render |
| `src/plugins/timetable/plugin-info.json`, `icon.png`, `settings.html` (create) | plugin registration and UI |
| `src/plugins/timetable/render/timetable.html`, `timetable.css` (create) | layout A |
| `tests/test_timetable_plugin.py` (create) | plugin tests with fake Home Assistant |

---

### Task 1: SkipRefresh and playlist fallback (core)

**Files:**
- Modify: `src/plugins/base_plugin/base_plugin.py` (after the imports/constants)
- Modify: `src/model.py:92-105` (`determine_active_playlist`)
- Modify: `src/refresh_task.py` (imports, `_run` refresh block lines ~137-162, new helper methods)
- Test: `tests/test_refresh_skip.py`

**Interfaces:**
- Produces:
  - `plugins.base_plugin.base_plugin.SkipRefresh(Exception)`
  - `PlaylistManager.determine_active_playlists(current_datetime) -> list[Playlist]`
  - `RefreshTask._execute_playlist_refresh(refresh_action, playlist_manager, current_dt) -> (PlaylistRefresh, plugin | None, Image | None)`

- [ ] **Step 1: Write the failing tests** – `tests/test_refresh_skip.py`

```python
from datetime import datetime

from PIL import Image

import refresh_task
from model import PlaylistManager
from plugins.base_plugin.base_plugin import SkipRefresh
from refresh_task import PlaylistRefresh, RefreshTask


def instance(plugin_id, name):
    return {"plugin_id": plugin_id, "name": name, "plugin_settings": {}, "refresh": {"interval": 300}}


PLAYLISTS = {
    "playlists": [
        {"name": "Default", "start_time": "00:00", "end_time": "24:00", "plugins": [instance("photos", "Fotos")]},
        {"name": "Abend", "start_time": "18:00", "end_time": "22:30",
         "plugins": [instance("photos", "Fotos Abend"), instance("rooms", "Raumklima Abend")]},
        {"name": "Schule Abend", "start_time": "19:30", "end_time": "20:30",
         "plugins": [instance("timetable", "Stundenplan Abend")]},
        {"name": "Schule Morgen", "start_time": "06:00", "end_time": "07:45",
         "plugins": [instance("timetable", "Stundenplan heute"), instance("weather", "Wetter Schule")]},
    ],
    "active_playlist": None,
}


def at(hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 14, hour, minute)


class FakePlugin:
    def __init__(self, plugin_id, skip):
        self.plugin_id = plugin_id
        self.skip = skip
        self.calls = 0
        self.config = {}

    def generate_image(self, settings, device_config):
        self.calls += 1
        if self.skip:
            raise SkipRefresh("nothing to show")
        return Image.new("RGB", (8, 8), "white")

    def get_device_display_settings(self):
        return {}


class FakeDeviceConfig:
    def __init__(self, image_dir):
        self.plugin_image_dir = str(image_dir)
        self.playlist_manager = PlaylistManager.from_dict(PLAYLISTS)

    def get_plugin(self, plugin_id):
        return {"id": plugin_id}


def setup(tmp_path, monkeypatch, skipping):
    plugins = {pid: FakePlugin(pid, pid in skipping) for pid in ("photos", "rooms", "timetable", "weather")}
    monkeypatch.setattr(refresh_task, "get_plugin_instance", lambda config: plugins[config["id"]])
    config = FakeDeviceConfig(tmp_path)
    return RefreshTask(config, display_manager=None), config.playlist_manager, plugins


def run_cycle(task, manager, dt):
    playlist = manager.determine_active_playlist(dt)
    return task._execute_playlist_refresh(PlaylistRefresh(playlist, playlist.get_next_plugin()), manager, dt)


def test_active_playlists_are_sorted_by_priority():
    manager = PlaylistManager.from_dict(PLAYLISTS)
    assert [p.name for p in manager.determine_active_playlists(at("20:00"))] == ["Schule Abend", "Abend", "Default"]
    assert [p.name for p in manager.determine_active_playlists(at("12:00"))] == ["Default"]
    assert manager.determine_active_playlist(at("20:00")).name == "Schule Abend"


def test_plugin_with_content_is_shown(tmp_path, monkeypatch):
    task, manager, _ = setup(tmp_path, monkeypatch, skipping=set())
    action, plugin, image = run_cycle(task, manager, at("20:00"))
    assert action.plugin_instance.name == "Stundenplan Abend"
    assert plugin.plugin_id == "timetable"
    assert image is not None


def test_skip_falls_through_to_next_plugin_of_the_same_playlist(tmp_path, monkeypatch):
    task, manager, _ = setup(tmp_path, monkeypatch, skipping={"timetable"})
    action, _, image = run_cycle(task, manager, at("07:00"))
    assert (action.playlist.name, action.plugin_instance.name) == ("Schule Morgen", "Wetter Schule")
    assert image is not None


def test_skip_falls_through_to_the_next_active_playlist(tmp_path, monkeypatch):
    task, manager, _ = setup(tmp_path, monkeypatch, skipping={"timetable"})
    action, _, image = run_cycle(task, manager, at("20:00"))
    assert (action.playlist.name, action.plugin_instance.name) == ("Abend", "Fotos Abend")
    assert image is not None


def test_fallback_playlist_keeps_rotating(tmp_path, monkeypatch):
    task, manager, _ = setup(tmp_path, monkeypatch, skipping={"timetable"})
    names = [run_cycle(task, manager, at("20:00"))[0].plugin_instance.name for _ in range(3)]
    assert names == ["Fotos Abend", "Raumklima Abend", "Fotos Abend"]


def test_everything_skipping_keeps_the_current_image(tmp_path, monkeypatch):
    task, manager, plugins = setup(tmp_path, monkeypatch, skipping={"timetable", "photos", "rooms"})
    _, plugin, image = run_cycle(task, manager, at("20:00"))
    assert plugin is None and image is None
    assert plugins["timetable"].calls == 1
    assert plugins["photos"].calls == 2   # "Fotos Abend" and "Fotos"
    assert plugins["rooms"].calls == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests/test_refresh_skip.py -v`
Expected: collection error `ImportError: cannot import name 'SkipRefresh'`.

- [ ] **Step 3: Add `SkipRefresh`** to `src/plugins/base_plugin/base_plugin.py`, directly after `BASE_PLUGIN_RENDER_DIR = …`:

```python


class SkipRefresh(Exception):
    """Raised by generate_image when a plugin has nothing to show right now (e.g. no school today).

    Playlist refreshes then move on to other content instead of treating it as an error."""
```

- [ ] **Step 4: Add `determine_active_playlists`** – replace `determine_active_playlist` in `src/model.py` with:

```python
    def determine_active_playlists(self, current_datetime):
        """All playlists active at the given time, highest priority (shortest time window) first."""
        current_time = current_datetime.strftime("%H:%M")  # Get current time in "HH:MM" format
        active_playlists = [p for p in self.playlists if p.is_active(current_time)]
        active_playlists.sort(key=lambda p: p.get_priority())
        return active_playlists

    def determine_active_playlist(self, current_datetime):
        """Determine the active playlist based on the current time."""
        active_playlists = self.determine_active_playlists(current_datetime)
        return active_playlists[0] if active_playlists else None
```

- [ ] **Step 5: Add the fallback to `src/refresh_task.py`**

Add the import after `from plugins.plugin_registry import get_plugin_instance`:

```python
from plugins.base_plugin.base_plugin import SkipRefresh
```

In `_run`, set `from_playlist = False` right before `if self.manual_update_request:`. In the playlist branch,
replace `refresh_action = PlaylistRefresh(playlist, plugin_instance)` with:

```python
                            refresh_action = PlaylistRefresh(playlist, plugin_instance)
                            from_playlist = True
```

Replace the block from `plugin_config = self.device_config.get_plugin(...)` through `image = refresh_action.execute(...)` with:

```python
                        if from_playlist:
                            refresh_action, plugin, image = self._execute_playlist_refresh(refresh_action, playlist_manager, current_dt)
                        else:
                            plugin, image = self._execute(refresh_action, current_dt)
                        if image is None:
                            continue
```

The rest of the block is unchanged: `image_hash = …`, `refresh_info`, the display update and the config write.

Directly before `except Exception as e:` in `_run`, add:

```python
            except SkipRefresh as e:
                logger.info(f"Nothing to show: {e}")
                self.refresh_result["exception"] = e
```

Add the helpers after `_determine_next_plugin`:

```python
    def _execute(self, refresh_action, current_dt):
        """Runs a refresh action. Returns (plugin, image), or (None, None) if the plugin is not configured."""
        plugin_config = self.device_config.get_plugin(refresh_action.get_plugin_id())
        if plugin_config is None:
            logger.error(f"Plugin config not found for '{refresh_action.get_plugin_id()}'.")
            return None, None
        plugin = get_plugin_instance(plugin_config)
        return plugin, refresh_action.execute(plugin, self.device_config, current_dt)

    def _execute_playlist_refresh(self, refresh_action, playlist_manager, current_dt):
        """Runs a playlist refresh. When the plugin has nothing to show (SkipRefresh), the other plugins of the
        playlist are tried, then those of the other active playlists by priority.

        Returns (refresh_action, plugin, image) of the refresh that produced an image; image is None if all skipped."""
        for action in self._fallback_actions(refresh_action, playlist_manager, current_dt):
            try:
                plugin, image = self._execute(action, current_dt)
            except SkipRefresh as e:
                logger.info(f"Nothing to show, skipping. | plugin_instance: {action.plugin_instance.name} | reason: {e}")
                continue
            return action, plugin, image
        logger.info("No plugin instance has anything to show, keeping the current image.")
        return refresh_action, None, None

    @staticmethod
    def _fallback_actions(refresh_action, playlist_manager, current_dt):
        """The given refresh, then the other plugins of its playlist, then those of the other active playlists."""
        yield refresh_action
        playlist = refresh_action.playlist
        for _ in range(len(playlist.plugins) - 1):
            yield PlaylistRefresh(playlist, playlist.get_next_plugin())
        for other in playlist_manager.determine_active_playlists(current_dt):
            if other is playlist:
                continue
            for _ in range(len(other.plugins)):
                yield PlaylistRefresh(other, other.get_next_plugin())
```

- [ ] **Step 6: Run the new tests and the full suite**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests -q`
Expected: all pass (including `test_buttons.py` and `test_model.py`).

- [ ] **Step 7: Commit**

```bash
git add src/plugins/base_plugin/base_plugin.py src/model.py src/refresh_task.py tests/test_refresh_skip.py
git commit -m "Let plugins skip a refresh when they have nothing to show"
```

---

### Task 2: Lesson logic (`lessons.py`)

**Files:**
- Create: `src/plugins/timetable/__init__.py` (empty), `src/plugins/timetable/lessons.py`
- Test: `tests/test_timetable_lessons.py`

**Interfaces:**
- Produces (all in `plugins.timetable.lessons`):
  - `LOOKAHEAD_DAYS = 4`
  - `parse_event(event: dict, tz=None) -> dict | None`. A lesson is
    `{"start": datetime, "end": datetime, "subject": str, "rooms": list[str], "state": str}`, where `state` is one
    of `regular`, `cancelled`, `irregular`, `room_change`.
  - `group_parallel(lessons) -> list[lesson]`
  - `merge_double_periods(lessons) -> list[lesson]`
  - `day_span(blocks) -> (datetime, datetime) | None`
  - `lessons_on(lessons, day: date) -> list[lesson]`
  - `target_day(lesson_lists: list[list[lesson]], today: date, mode: str) -> date | None`, with mode `today` or
    `next_school_day`
  - `day_title(day: date, today: date) -> str`
  - `child_column(name: str, lessons: list | None, day: date) -> dict`, which returns
    `{"name", "span", "rows", "notice"}`. Each row is `{"start", "end", "subject", "rooms", "tag", "cancelled", "weight"}`.

- [ ] **Step 1: Write the failing tests** – `tests/test_timetable_lessons.py`

```python
from datetime import date

import pytest
import pytz

from plugins.timetable.lessons import (child_column, day_span, day_title, group_parallel, lessons_on,
                                       merge_double_periods, parse_event, target_day)

TZ = pytz.timezone("Europe/Berlin")
WED = date(2026, 11, 11)


def event(start, end, summary, location="", day="2026-11-11"):
    return {"start": {"dateTime": f"{day}T{start}:00+01:00"}, "end": {"dateTime": f"{day}T{end}:00+01:00"},
            "summary": summary, "location": location, "description": ""}


def lessons(*events):
    return [parse_event(e, TZ) for e in events]


@pytest.mark.parametrize("summary,subject,state", [
    ("Mathematik", "Mathematik", "regular"),
    ("Cancelled: Mathematik", "Mathematik", "cancelled"),
    ("Irregular: Englisch", "Englisch", "irregular"),
    ("Room change: Physik", "Physik", "room_change"),
])
def test_parse_event_maps_prefixes_to_states(summary, subject, state):
    lesson = parse_event(event("08:00", "08:45", summary, "R1"), TZ)
    assert (lesson["subject"], lesson["state"], lesson["rooms"]) == (subject, state, ["R1"])
    assert lesson["start"].hour == 8 and lesson["end"].minute == 45


def test_parse_event_ignores_all_day_events_and_handles_missing_room():
    assert parse_event({"start": {"date": "2026-11-11"}, "end": {"date": "2026-11-12"}, "summary": "Ferien"}, TZ) is None
    assert parse_event(event("08:00", "08:45", "Sport"), TZ)["rooms"] == []


def test_parallel_courses_become_one_lesson_with_all_rooms():
    grouped = group_parallel(lessons(
        event("09:50", "10:35", "Philosophie", "R3"), event("09:50", "10:35", "Philosophie", "R1"),
        event("09:50", "10:35", "Philosophie", "R3"), event("09:50", "10:35", "Religion", "R2")))
    assert [(l["subject"], l["rooms"]) for l in grouped] == [("Philosophie", ["R3", "R1"]), ("Religion", ["R2"])]


def test_double_periods_are_merged_even_if_room_order_differs():
    blocks = merge_double_periods(group_parallel(lessons(
        event("08:00", "08:45", "Wirtschaft", "R1"), event("08:00", "08:45", "Wirtschaft", "R2"),
        event("08:45", "09:30", "Wirtschaft", "R2"), event("08:45", "09:30", "Wirtschaft", "R1"))))
    assert len(blocks) == 1
    assert (blocks[0]["start"].hour, blocks[0]["end"].minute, blocks[0]["rooms"]) == (8, 30, ["R1", "R2"])


def test_lessons_across_a_break_or_with_other_state_stay_separate():
    blocks = merge_double_periods(lessons(
        event("08:00", "08:45", "Musik", "R1"), event("08:45", "09:30", "Cancelled: Musik", "R1"),
        event("09:50", "10:35", "Musik", "R1"), event("10:35", "11:20", "Musik", "R1")))
    assert [(b["start"].strftime("%H:%M"), b["end"].strftime("%H:%M"), b["state"]) for b in blocks] == [
        ("08:00", "08:45", "regular"), ("08:45", "09:30", "cancelled"), ("09:50", "11:20", "regular")]


def test_day_span_ignores_cancelled_lessons():
    blocks = lessons(event("08:00", "09:30", "Cancelled: Sport"), event("09:50", "13:10", "Kunst"))
    start, end = day_span(blocks)
    assert (start.strftime("%H:%M"), end.strftime("%H:%M")) == ("09:50", "13:10")
    assert day_span(lessons(event("08:00", "09:30", "Cancelled: Sport"))) is None


def test_lessons_on_filters_by_local_date():
    all_lessons = lessons(event("08:00", "08:45", "A"), event("08:00", "08:45", "B", day="2026-11-12"))
    assert [l["subject"] for l in lessons_on(all_lessons, date(2026, 11, 12))] == ["B"]


def test_target_day_today():
    kid = lessons(event("08:00", "08:45", "Mathe"))
    assert target_day([kid, []], WED, "today") == WED
    assert target_day([[], []], WED, "today") is None


def test_next_school_day_skips_the_weekend_and_counts_cancelled_lessons():
    friday, monday = date(2026, 11, 13), date(2026, 11, 16)
    kid = lessons(event("08:00", "08:45", "Cancelled: Mathe", day="2026-11-16"))
    assert target_day([kid], friday, "next_school_day") == monday
    assert target_day([kid], monday, "next_school_day") is None   # nothing in the following 4 days


def test_day_title():
    assert day_title(date(2026, 11, 10), date(2026, 11, 10)) == "HEUTE · DI 10.11."
    assert day_title(date(2026, 11, 11), date(2026, 11, 10)) == "MORGEN · MI 11.11."
    assert day_title(date(2026, 11, 16), date(2026, 11, 13)) == "MONTAG · 16.11."


def test_child_column_rows_and_span():
    column = child_column("Kind A", lessons(
        event("08:00", "08:45", "Musik", "R1"), event("08:45", "09:30", "Musik", "R1"),
        event("09:50", "10:35", "Cancelled: Mathematik", "R2"), event("10:35", "11:20", "Irregular: Klassenrat", "R2"),
        event("11:40", "12:25", "Room change: Englisch", "R3"), event("11:40", "12:25", "Room change: Englisch", "R4"),
        event("08:00", "08:45", "Morgen", "R9", day="2026-11-12")), WED)
    assert column["name"] == "Kind A" and column["notice"] is None
    assert column["span"] == "8:00–12:25"
    assert [(r["start"], r["end"], r["subject"], r["rooms"], r["tag"], r["cancelled"]) for r in column["rows"]] == [
        ("8:00", "9:30", "Musik", "R1", None, False),
        ("9:50", "10:35", "Mathematik", "R2", "ENTFALL", True),
        ("10:35", "11:20", "Klassenrat", "R2", "VERTR.", False),
        ("11:40", "12:25", "Englisch", "R3 · R4", "RAUM", False),
    ]
    assert column["rows"][0]["weight"] > column["rows"][1]["weight"]


def test_child_column_notices():
    assert child_column("Kind A", None, WED)["notice"] == "KEINE DATEN"
    assert child_column("Kind A", lessons(event("08:00", "08:45", "X", day="2026-11-12")), WED)["notice"] == "KEIN UNTERRICHT"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests/test_timetable_lessons.py -q`
Expected: `ModuleNotFoundError: No module named 'plugins.timetable'`.

- [ ] **Step 3: Implement** `src/plugins/timetable/__init__.py` (empty) and `src/plugins/timetable/lessons.py`:

```python
"""Timetable logic: turns Home Assistant calendar events of the WebUntis integration into display rows.

Pure functions without I/O, so they can be tested without Home Assistant."""
from datetime import datetime, timedelta

LOOKAHEAD_DAYS = 4                 # next_school_day looks at most this many days ahead
MERGE_GAP = timedelta(minutes=5)   # consecutive lessons closer than this form one block (double period)

# Prefixes the WebUntis integration puts in front of the subject
STATE_PREFIXES = {"Cancelled: ": "cancelled", "Irregular: ": "irregular", "Room change: ": "room_change"}
TAGS = {"regular": None, "cancelled": "ENTFALL", "irregular": "VERTR.", "room_change": "RAUM"}

WEEKDAYS = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
WEEKDAY_NAMES = ["MONTAG", "DIENSTAG", "MITTWOCH", "DONNERSTAG", "FREITAG", "SAMSTAG", "SONNTAG"]


def parse_event(event, tz=None):
    """Home Assistant calendar event -> lesson, or None for events without a time (all-day)."""
    start = (event.get("start") or {}).get("dateTime")
    end = (event.get("end") or {}).get("dateTime")
    if not start or not end:
        return None
    start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if tz is not None:
        start, end = start.astimezone(tz), end.astimezone(tz)

    subject, state = (event.get("summary") or "").strip(), "regular"
    for prefix, prefix_state in STATE_PREFIXES.items():
        if subject.startswith(prefix):
            subject, state = subject[len(prefix):].strip(), prefix_state
            break
    room = (event.get("location") or "").strip()
    return {"start": start, "end": end, "subject": subject, "rooms": [room] if room else [], "state": state}


def by_time(lesson):
    return lesson["start"], lesson["end"]


def group_parallel(lessons):
    """Same time, subject and state (course groups of a class timetable) -> one lesson listing all rooms."""
    groups = {}
    for lesson in sorted(lessons, key=by_time):
        key = (lesson["start"], lesson["end"], lesson["subject"], lesson["state"])
        if key not in groups:
            groups[key] = {**lesson, "rooms": []}
        groups[key]["rooms"] += [room for room in lesson["rooms"] if room not in groups[key]["rooms"]]
    return list(groups.values())


def merge_double_periods(lessons):
    """Consecutive lessons with the same subject, rooms and state -> one block."""
    blocks = []
    for lesson in sorted(lessons, key=by_time):
        for block in blocks:
            same = (block["subject"], block["state"], set(block["rooms"])) == \
                   (lesson["subject"], lesson["state"], set(lesson["rooms"]))
            if same and timedelta(0) <= lesson["start"] - block["end"] <= MERGE_GAP:
                block["end"] = lesson["end"]
                break
        else:
            blocks.append(dict(lesson))
    return blocks


def day_span(blocks):
    """First start and last end of the lessons that take place, or None if all are cancelled."""
    held = [block for block in blocks if block["state"] != "cancelled"]
    if not held:
        return None
    return min(b["start"] for b in held), max(b["end"] for b in held)


def lessons_on(lessons, day):
    return [lesson for lesson in lessons if lesson["start"].date() == day]


def target_day(lesson_lists, today, mode):
    """The day to show: today, or the next day (up to LOOKAHEAD_DAYS ahead) with at least one lesson of any child.
    Cancelled lessons count. None if nobody has school."""
    if mode == "today":
        candidates = [today]
    else:
        candidates = [today + timedelta(days=offset) for offset in range(1, LOOKAHEAD_DAYS + 1)]
    for day in candidates:
        if any(lessons_on(lessons, day) for lessons in lesson_lists):
            return day
    return None


def day_title(day, today):
    if day == today:
        return f"HEUTE · {WEEKDAYS[day.weekday()]} {day:%d.%m.}"
    if day == today + timedelta(days=1):
        return f"MORGEN · {WEEKDAYS[day.weekday()]} {day:%d.%m.}"
    return f"{WEEKDAY_NAMES[day.weekday()]} · {day:%d.%m.}"


def hm(dt):
    return f"{dt.hour}:{dt.minute:02d}"


def block_row(block):
    minutes = (block["end"] - block["start"]).total_seconds() / 60
    return {
        "start": hm(block["start"]),
        "end": hm(block["end"]),
        "subject": block["subject"],
        "rooms": " · ".join(block["rooms"]),
        "tag": TAGS[block["state"]],
        "cancelled": block["state"] == "cancelled",
        "weight": round(minutes) + 10,  # flex-grow: row height follows the duration, short rows keep some room
    }


def child_column(name, lessons, day):
    """View model for one child's column. lessons is None when the calendar could not be fetched."""
    if lessons is None:
        return {"name": name, "span": "", "rows": [], "notice": "KEINE DATEN"}
    blocks = merge_double_periods(group_parallel(lessons_on(lessons, day)))
    if not blocks:
        return {"name": name, "span": "", "rows": [], "notice": "KEIN UNTERRICHT"}
    span = day_span(blocks)
    return {"name": name, "span": f"{hm(span[0])}–{hm(span[1])}" if span else "",
            "rows": [block_row(block) for block in blocks], "notice": None}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests/test_timetable_lessons.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/plugins/timetable/__init__.py src/plugins/timetable/lessons.py tests/test_timetable_lessons.py
git commit -m "Timetable: turn WebUntis calendar events into display rows"
```

---

### Task 3: Timetable plugin (fetch, render, settings)

**Files:**
- Create: `src/plugins/timetable/timetable.py`, `plugin-info.json`, `icon.png`, `settings.html`,
  `render/timetable.html`, `render/timetable.css`
- Test: `tests/test_timetable_plugin.py`

**Interfaces:**
- Consumes: `LOOKAHEAD_DAYS`, `parse_event`, `target_day`, `day_title`, `child_column` (Task 2); `SkipRefresh` (Task 1)
- Produces:
  - `Timetable.generate_image(settings, device_config)`. It reads the settings `baseUrl`, `day`,
    `childName[]` and `childCalendar[]`.
  - `Timetable.fetch_events(base_url, token, calendar, start, end) -> list[dict]`
  - `Timetable.current_time(tz) -> datetime` (overridden in tests)

- [ ] **Step 1: Write the failing tests** – `tests/test_timetable_plugin.py`

```python
from datetime import datetime

import pytest
import pytz
import requests

from plugins.base_plugin.base_plugin import SkipRefresh
from plugins.timetable.timetable import Timetable

TZ = pytz.timezone("Europe/Berlin")
SETTINGS = {"baseUrl": "http://ha.local:8123/", "day": "next_school_day",
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


def test_renders_the_next_school_day_for_all_children(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "fetch_events", serve(CALENDARS))
    assert plugin.generate_image(SETTINGS, FakeDeviceConfig()) == "image"
    dimensions, html_file, css_file, params = plugin.rendered
    assert (dimensions, html_file, css_file) == ([800, 480], "timetable.html", "timetable.css")
    assert params["title"] == "MORGEN · MI 11.11."
    assert params["timestamp"] == "Stand 19:45"
    kid_a, kid_b = params["columns"]
    assert (kid_a["name"], kid_a["span"], [r["subject"] for r in kid_a["rows"]]) == ("Kind A", "8:00–9:30", ["Musik"])
    assert (kid_b["span"], kid_b["rows"][0]["tag"]) == ("", "ENTFALL")


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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests/test_timetable_plugin.py -q`
Expected: `ModuleNotFoundError: No module named 'plugins.timetable.timetable'`.

- [ ] **Step 3: Implement** `src/plugins/timetable/timetable.py`:

```python
"""School timetable ("Stundenplan") from the Home Assistant calendars of the WebUntis integration."""
from datetime import datetime, timedelta
import logging

import pytz
import requests

from plugins.base_plugin.base_plugin import BasePlugin, SkipRefresh
from plugins.timetable.lessons import LOOKAHEAD_DAYS, child_column, day_title, parse_event, target_day

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DAY_MODES = ("today", "next_school_day")
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
        end = start + timedelta(days=LOOKAHEAD_DAYS + 1)

        # One request per child for the whole look-ahead window; a failing calendar only empties its column
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
            raise SkipRefresh("no school today" if mode == "today" else f"no school in the next {LOOKAHEAD_DAYS} days")

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
```

- [ ] **Step 4: Run the plugin tests**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests/test_timetable_plugin.py -q`
Expected: all pass.

- [ ] **Step 5: Add registration, template, CSS, settings and icon**

`src/plugins/timetable/plugin-info.json`:

```json
{
  "display_name": "Stundenplan",
  "id": "timetable",
  "class": "Timetable"
}
```

`src/plugins/timetable/render/timetable.html`:

```html
{% extends "plugin.html" %}

{% block content %}
<div class="tt">
  <header class="tt-header">
    <div class="tt-title">{{ title }}</div>
    <div class="tt-meta">{{ timestamp }}</div>
  </header>

  <main class="tt-columns">
    {% for column in columns %}
    <section class="tt-column">
      <div class="tt-kid">
        <span class="tt-tape">{{ column.name }}</span>
        <span class="tt-span">{{ column.span }}</span>
      </div>
      {% if column.notice %}
      <div class="tt-notice"><span class="tt-tag">{{ column.notice }}</span></div>
      {% else %}
      <div class="tt-rows">
        {% for row in column.rows %}
        <div class="tt-row{% if row.cancelled %} cancelled{% endif %}" style="flex-grow: {{ row.weight }}">
          <div class="tt-time">{{ row.start }}<br>{{ row.end }}</div>
          <div class="tt-body">
            <div class="tt-line">
              <span class="tt-subject">{{ row.subject }}</span>
              {% if row.tag %}<span class="tt-tag">{{ row.tag }}</span>{% endif %}
            </div>
            {% if row.rooms %}<div class="tt-rooms">{{ row.rooms }}</div>{% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </section>
    {% endfor %}
  </main>
</div>
{% endblock %}
```

`src/plugins/timetable/render/timetable.css`:

```css
/* Courier Prime (SIL Open Font License) is shipped with the Home Assistant plugin */
@font-face { font-family: "Courier Prime"; font-weight: normal; src: url("../../home_assistant/render/fonts/CourierPrime-Regular.ttf") format("truetype"); }
@font-face { font-family: "Courier Prime"; font-weight: bold; src: url("../../home_assistant/render/fonts/CourierPrime-Bold.ttf") format("truetype"); }

* { box-sizing: border-box; margin: 0; padding: 0; }

/* Pure Spectra 6 palette colors only; color is used for fills and markers, text stays black (see home_assistant.css) */
:root { --black: #000000; --white: #ffffff; --yellow: #ffff00; --red: #ff0000; }

.tt {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  font-family: "Courier Prime", "Courier New", monospace;
  color: var(--black);
  background: var(--white);
}

.tt-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-bottom: 3px;
  border-bottom: 4px solid var(--black);
}
.tt-title { font-weight: bold; font-size: 28px; line-height: 1; text-transform: uppercase; letter-spacing: 0.06em; }
.tt-meta { font-weight: bold; font-size: 16px; }

/* One column per child */
.tt-columns { flex: 1; display: flex; min-height: 0; }
.tt-column { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; padding: 6px 12px 0; }
.tt-column + .tt-column { border-left: 2px solid var(--black); }
.tt-column:first-child { padding-left: 0; }
.tt-column:last-child { padding-right: 0; }

.tt-kid { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.tt-tape {
  padding: 2px 10px;
  font-weight: bold;
  font-size: 18px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  background: var(--yellow);
}
.tt-span { font-weight: bold; font-size: 18px; }

/* Lesson blocks: height follows the duration (flex-grow set per row) */
.tt-rows { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.tt-row {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  border-top: 2px solid var(--black);
  container-type: size;
}
.tt-time { width: 64px; flex-shrink: 0; font-weight: bold; font-size: 17px; line-height: 1.15; }
.tt-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tt-line { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tt-subject {
  font-weight: bold;
  font-size: clamp(15px, min(36cqh, 6.4cqw), 25px);
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tt-rooms { font-weight: bold; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Changes: black label with yellow text; cancelled lessons are struck through in red */
.tt-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  font-weight: bold;
  font-size: 14px;
  letter-spacing: 0.1em;
  white-space: nowrap;
  background: var(--black);
  color: var(--yellow);
}
.cancelled .tt-subject { text-decoration: line-through; text-decoration-color: var(--red); text-decoration-thickness: 4px; }
.cancelled .tt-rooms { visibility: hidden; }

.tt-notice { flex: 1; display: flex; align-items: center; justify-content: center; border-top: 2px solid var(--black); }
.tt-notice .tt-tag { font-size: 18px; padding: 3px 10px; }
```

`src/plugins/timetable/settings.html`:

```html
<div class="form-group">
  <div class="form-group nowrap">
    <label for="baseUrl" class="form-label">Home Assistant Base URL</label>
    <input type="text" id="baseUrl" name="baseUrl" placeholder="http://homeassistant.local:8123" class="form-input">
  </div>
  <div class="form-group nowrap">
    <label for="day" class="form-label">Day</label>
    <select id="day" name="day" class="form-input">
      <option value="today">Today</option>
      <option value="next_school_day">Next school day (from tomorrow)</option>
    </select>
  </div>
</div>

<div class="form-group">
  <label class="form-label">Children: name and WebUntis calendar entity</label>
  <div id="childrenContainer" class="form-group"></div>
  <button type="button" id="addChild" class="header-button">+ Add Child</button>
</div>

<script>
  const childrenContainer = document.getElementById('childrenContainer');

  function addChildRow(name = '', calendar = '') {
    const row = document.createElement('div');
    row.className = 'form-group nowrap';
    row.innerHTML = `
      <input type="text" name="childName[]" placeholder="Name" class="form-input">
      <input type="text" name="childCalendar[]" placeholder="calendar.stundenplan_name" class="form-input">
      <button type="button" class="delete-button" title="Remove">
        <img src="{{ url_for('static', filename='icons/remove.png') }}" alt="remove" class="action-icon">
      </button>
    `;
    row.querySelector('[name="childName[]"]').value = name;
    row.querySelector('[name="childCalendar[]"]').value = calendar;
    row.querySelector('.delete-button').onclick = () => row.remove();
    childrenContainer.appendChild(row);
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (typeof loadPluginSettings !== 'undefined' && loadPluginSettings) {
      document.getElementById('baseUrl').value = pluginSettings.baseUrl || '';
      document.getElementById('day').value = pluginSettings.day || 'today';
      const names = [].concat(pluginSettings['childName[]'] || []);
      const calendars = [].concat(pluginSettings['childCalendar[]'] || []);
      names.forEach((name, i) => addChildRow(name, calendars[i] || ''));
    }
    if (!childrenContainer.children.length) addChildRow();
    document.getElementById('addChild').onclick = () => addChildRow();
  });
</script>
```

Icon (512×512, yellow timetable grid on white). Generate it once:

```bash
/tmp/inkypi-trial-venv/bin/python - <<'EOF'
from PIL import Image, ImageDraw
img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((32, 32, 480, 480), radius=48, fill="white", outline="black", width=24)
d.rectangle((32, 32, 480, 150), fill="#ffff00", outline="black", width=24)
for x in (181, 331):
    d.line((x, 150, x, 468), fill="black", width=16)
for y in (256, 362):
    d.line((44, y, 468, y), fill="black", width=16)
img.save("src/plugins/timetable/icon.png")
EOF
```

- [ ] **Step 6: Run the full test suite**

Run: `PYTHONPATH=src /tmp/inkypi-trial-venv/bin/python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/plugins/timetable tests/test_timetable_plugin.py
git commit -m "Add timetable plugin showing WebUntis timetables from Home Assistant"
```

---

### Task 4: Preview with real data (visual gate)

**Files:** none in the repo. Scratch files go in `/private/tmp/inkypi-harness/untis/`; they contain real names
and must not be committed.

- [ ] **Step 1: Export the real calendar events on the Pi.** The token stays on the Pi. Write a script
`/tmp/inkypi-harness/untis_export.sh`, pipe it to `ssh pi@inky.local 'bash -s'`, and save the output to
`/private/tmp/inkypi-harness/untis/events.json`. The script:
  1. reads `HOME_ASSISTANT_TOKEN` from `.env` and `baseUrl` from `device.json`
  2. fetches `GET /api/calendars/calendar.stundenplan_<kid>?start=<today 00:00>&end=<today+5d>` for both kids
  3. prints `{"calendar.stundenplan_<kid>": [...events]}` as JSON

- [ ] **Step 2: Render and simulate the panel.** Write `/tmp/inkypi-harness/preview_timetable.py`. It:
  1. instantiates `Timetable({"id": "timetable"})`
  2. replaces `fetch_events` with a lookup into `events.json`
  3. sets `current_time` to today 19:45 (`next_school_day`) and to 06:30 (`today`)
  4. calls `generate_image` with the kids' names and calendars
  5. saves the raw PNG

  Run it with `PATH=/tmp/inkypi-bin:$PATH SRC_DIR=src`, then run `/tmp/inkypi-harness/panelsim.py` on the PNG.
  Check the result yourself first: no clipping, umlauts intact, rows readable.

- [ ] **Step 3: Show the panel simulation to the user** (SendUserFile) and wait for approval. Fix the CSS if they
  ask for changes, rerun Task 3's tests, and commit with `"Timetable: <change>"`.

---

### Task 5: Deploy and schedule on the Pi

- [ ] **Step 1: Push and update the Pi**

```bash
git push origin dev
ssh -o HostKeyAlias=inky.fritz.box pi@inky.local 'cd /home/pi/InkyPi && git pull --ff-only && sudo systemctl restart inkypi'
```

Wait until `curl -s -o /dev/null -w "%{http_code}" http://localhost/` returns 200 on the Pi.

- [ ] **Step 2: Add the playlists** while the service is stopped:
  1. Run `sudo systemctl stop inkypi` and back up `device.json` to
     `~/inkypi-backup-20261109/device.before-timetable.json`.
  2. Edit the file with `sudo python3`:
     - **Playlist "Schule Morgen" (06:00–07:45)** with two instances:
       - `{"plugin_id": "timetable", "name": "Stundenplan heute", "plugin_settings": {"baseUrl": <HA baseUrl of the room view>, "day": "today", "childName[]": [<child names>], "childCalendar[]": [<their calendar entities>]}, "refresh": {"interval": 300}}`
       - a copy of the "Wetter Morgen" instance named "Wetter Schule", with `latest_refresh_time` removed
     - **Playlist "Schule Abend" (19:30–20:30)** with one instance: "Stundenplan Abend", same settings but
       `"day": "next_school_day"`
  3. Run `sudo systemctl start inkypi`.

- [ ] **Step 3: Verify on the device**
  1. Send `POST http://localhost/display_plugin_instance` with
     `{"playlist_name": "Schule Abend", "plugin_id": "timetable", "plugin_instance": "Stundenplan Abend"}`. It
     should answer `success: true`.
  2. Check `sudo journalctl -u inkypi --since "-5min"`: it should show `Updating display` for `Stundenplan Abend`
     and no exceptions.
  3. Ask the user for a photo of the display.

- [ ] **Step 4: Update memory.** Record in `inkypi-fork-workflow.md` that the fork now has the timetable plugin
  and the `SkipRefresh` core change.
