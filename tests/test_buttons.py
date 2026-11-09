import threading
from datetime import datetime

import pytest
from PIL import Image

import buttons
from model import PlaylistManager, RefreshInfo
from refresh_task import PlaylistRefresh, RefreshTask


def instance(plugin_id, name):
    return {"plugin_id": plugin_id, "name": name, "plugin_settings": {}, "refresh": {"interval": 300}}


PLAYLISTS = {
    "playlists": [
        {"name": "Default", "start_time": "00:00", "end_time": "24:00", "current_plugin_index": 0,
         "plugins": [instance("google_photos", "Fotos"), instance("home_assistant", "Raumklima"),
                     instance("weather", "Wetter")]},
        {"name": "Morgen", "start_time": "06:00", "end_time": "09:00", "current_plugin_index": None,
         "plugins": [instance("weather", "Wetter Morgen"), instance("home_assistant", "Raumklima Morgen")]},
        {"name": "Nacht", "start_time": "22:30", "end_time": "06:00", "current_plugin_index": None,
         "plugins": [instance("year_progress", "Jahr")]},
    ],
    "active_playlist": "Default",
}


def at(hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 10, hour, minute)


# --- press detection -------------------------------------------------------------------------

def test_press_duration_is_measured_between_press_and_release():
    tracker = buttons.PressTracker()

    assert tracker.feed("A", pressed=True, timestamp=10.0) is None
    assert tracker.feed("A", pressed=False, timestamp=10.3) == pytest.approx(0.3)


def test_release_without_press_is_ignored():
    assert buttons.PressTracker().feed("A", pressed=False, timestamp=1.0) is None


def test_presses_during_busy_window_are_discarded():
    tracker = buttons.PressTracker()
    tracker.ignore_until(20.0)

    tracker.feed("B", pressed=True, timestamp=15.0)     # pressed while the display was busy
    assert tracker.feed("B", pressed=False, timestamp=21.0) is None
    tracker.feed("B", pressed=True, timestamp=22.0)
    assert tracker.feed("B", pressed=False, timestamp=22.2) == pytest.approx(0.2)


@pytest.mark.parametrize("duration,expected", [(0.05, "short"), (4.9, "short"), (5.0, "long"), (12, "long")])
def test_classify_press(duration, expected):
    assert buttons.classify_press(duration) == expected


def test_default_actions():
    actions = buttons.ButtonActions.resolve_mapping({})

    assert actions == {
        ("A", "short"): "show:home_assistant",
        ("B", "short"): "show:weather",
        ("C", "short"): "show:google_photos",
        ("D", "short"): "hold",
        ("D", "long"): "shutdown",
    }


def test_configured_actions_override_defaults():
    actions = buttons.ButtonActions.resolve_mapping({"A": {"short": "show:year_progress"}})

    assert actions[("A", "short")] == "show:year_progress"
    assert actions[("D", "long")] == "shutdown"


# --- finding instances -----------------------------------------------------------------------

def test_instance_from_active_playlist_is_preferred():
    manager = PlaylistManager.from_dict(PLAYLISTS)

    playlist, plugin_instance = buttons.find_instance(manager, "home_assistant", at("07:00"))

    assert (playlist.name, plugin_instance.name) == ("Morgen", "Raumklima Morgen")


def test_instance_from_other_playlist_as_fallback():
    manager = PlaylistManager.from_dict(PLAYLISTS)

    playlist, plugin_instance = buttons.find_instance(manager, "google_photos", at("23:00"))

    assert (playlist.name, plugin_instance.name) == ("Default", "Fotos")


def test_unknown_plugin_has_no_instance():
    manager = PlaylistManager.from_dict(PLAYLISTS)

    assert buttons.find_instance(manager, "clock", at("12:00")) == (None, None)


# --- hold in the refresh task ----------------------------------------------------------------

class ConfigStub:
    def __init__(self):
        self.playlist_manager = PlaylistManager.from_dict(PLAYLISTS)

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_config(self, key=None, default=None):
        return default


def test_hold_blocks_rotation_while_playlist_is_unchanged():
    task = RefreshTask(ConfigStub(), display_manager=None)
    manager = PlaylistManager.from_dict(PLAYLISTS)
    task.hold("Default")

    assert task.hold_blocks_refresh(manager, at("12:00")) is True
    assert task.is_held()


def test_hold_is_released_when_playlist_changes():
    task = RefreshTask(ConfigStub(), display_manager=None)
    manager = PlaylistManager.from_dict(PLAYLISTS)
    task.hold("Default")

    assert task.hold_blocks_refresh(manager, at("22:45")) is False
    assert not task.is_held()


def test_no_hold_never_blocks():
    task = RefreshTask(ConfigStub(), display_manager=None)

    assert task.hold_blocks_refresh(PlaylistManager.from_dict(PLAYLISTS), at("12:00")) is False


# --- actions ---------------------------------------------------------------------------------

class FakeRefreshTask:
    def __init__(self, now):
        self.condition = threading.Condition(threading.Lock())
        self.now = now
        self.manual_updates = []
        self.held_playlist = None
        self.stopped = False

    def _get_current_datetime(self):
        return self.now

    def manual_update(self, refresh_action):
        # manual_update must never be called while holding the display lock (it would deadlock)
        assert not self.condition._is_owned()
        self.manual_updates.append(refresh_action)

    def hold(self, playlist_name):
        self.held_playlist = playlist_name

    def release(self):
        self.held_playlist = None

    def is_held(self):
        return self.held_playlist is not None

    def stop(self):
        self.stopped = True


class FakeDisplayManager:
    def __init__(self):
        self.shown = []

    def display_image(self, image, image_settings=[], device_display_settings=None):
        self.shown.append((image, device_display_settings))


class FakeDeviceConfig:
    def __init__(self, current_image_file):
        self.playlist_manager = PlaylistManager.from_dict(PLAYLISTS)
        self.current_image_file = current_image_file

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_refresh_info(self):
        return RefreshInfo("Playlist", "home_assistant", "2026-11-10T12:00:00", "hash",
                           playlist="Default", plugin_instance="Raumklima")

    def get_resolution(self):
        return [800, 480]

    def get_config(self, key=None, default=None):
        return {"orientation": "horizontal"}.get(key, default)


@pytest.fixture
def setup(tmp_path):
    image_file = tmp_path / "current_image.png"
    Image.new("RGB", (800, 480), "white").save(image_file)
    refresh_task = FakeRefreshTask(now=at("12:00"))
    display = FakeDisplayManager()
    shutdowns = []
    actions = buttons.ButtonActions(
        FakeDeviceConfig(str(image_file)), refresh_task, display, mapping={},
        display_settings_for=lambda plugin_id: {"inky_saturation": 0.0} if plugin_id == "home_assistant" else {},
        shutdown_command=lambda: shutdowns.append(True))
    return actions, refresh_task, display, shutdowns


def test_show_refreshes_instance_of_active_playlist(setup):
    actions, refresh_task, display, _ = setup

    actions.handle("A", "short")

    action = refresh_task.manual_updates[0]
    assert isinstance(action, PlaylistRefresh)
    assert (action.playlist.name, action.plugin_instance.name, action.force) == ("Default", "Raumklima", True)


def test_unmapped_press_does_nothing(setup):
    actions, refresh_task, display, shutdowns = setup

    actions.handle("A", "long")

    assert refresh_task.manual_updates == [] and display.shown == [] and shutdowns == []


def test_hold_marks_current_image(setup):
    actions, refresh_task, display, _ = setup

    actions.handle("D", "short")

    assert refresh_task.held_playlist == "Default"
    image, settings = display.shown[0]
    assert image.size == (800, 480)
    assert image.getpixel((799 - 12, 479 - 12)) == (0, 0, 0)   # label in the bottom right corner
    assert image.getpixel((10, 10)) == (255, 255, 255)        # rest of the image untouched
    assert settings == {"inky_saturation": 0.0}               # same driver settings as the shown plugin
    assert refresh_task.manual_updates == []


def test_second_hold_press_resumes_with_next_view(setup):
    actions, refresh_task, display, _ = setup
    actions.handle("D", "short")

    actions.handle("D", "short")

    assert not refresh_task.is_held()
    action = refresh_task.manual_updates[0]
    assert (action.playlist.name, action.plugin_instance.name, action.force) == ("Default", "Raumklima", False)


def test_shutdown_shows_screen_then_shuts_down(setup):
    actions, refresh_task, display, shutdowns = setup

    actions.handle("D", "long")

    assert refresh_task.stopped
    image, settings = display.shown[0]
    assert image.size == (800, 480)
    assert shutdowns == [True]


def test_failing_action_does_not_raise(setup):
    actions, refresh_task, display, _ = setup

    def broken(refresh_action):
        raise RuntimeError("plugin failed")
    refresh_task.manual_update = broken

    actions.handle("A", "short")  # logged, listener thread keeps running


def test_start_buttons_disabled_by_default():
    class Config:
        def get_config(self, key=None, default=None):
            return default

    assert buttons.start_buttons(Config(), refresh_task=None, display_manager=None) is None
