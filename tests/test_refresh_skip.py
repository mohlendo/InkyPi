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
