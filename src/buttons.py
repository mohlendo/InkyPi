"""Hardware buttons A-D on the side of the Inky Impression.

Enable in device.json:  "buttons": {"enabled": true}
Optional overrides:     "pins": {"C": 25}, "actions": {"A": {"short": "show:clock", "long": ""}}

Actions: "show:<plugin_id>" (show that plugin now, freshly generated; instance from the active playlist first),
"hold" (pause the rotation / resume with the next view) and "shutdown" (shutdown screen, then power off).
"""
import logging
import os
import threading
import time
from datetime import timedelta

from PIL import Image, ImageDraw

from plugins.plugin_registry import get_plugin_instance
from refresh_task import PlaylistRefresh
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

# BCM GPIO numbers of the buttons from top to bottom; button C is GPIO 25 on the 13.3" Impression
DEFAULT_PINS = {"A": 5, "B": 6, "C": 16, "D": 24}
DEFAULT_ACTIONS = {
    "A": {"short": "show:home_assistant"},
    "B": {"short": "show:weather"},
    "C": {"short": "show:google_photos"},
    "D": {"short": "hold", "long": "shutdown"},
}
LONG_PRESS_S = 5.0
DEBOUNCE = timedelta(milliseconds=30)
HOLD_LABEL = "GEHALTEN"


class PressTracker:
    """Turns press/release edges into press durations. Presses that start while the display is busy
    (an action is running) are dropped instead of being executed afterwards."""

    def __init__(self):
        self.pressed_at = {}
        self.ignore_before = float("-inf")

    def ignore_until(self, timestamp):
        self.ignore_before = timestamp

    def feed(self, button, pressed, timestamp):
        if pressed:
            if timestamp >= self.ignore_before:
                self.pressed_at[button] = timestamp
            return None
        start = self.pressed_at.pop(button, None)
        return None if start is None else timestamp - start


def classify_press(duration):
    return "long" if duration >= LONG_PRESS_S else "short"


def find_instance(playlist_manager, plugin_id, now):
    """Instance of the plugin in the active playlist, otherwise the first one in any playlist."""
    active = playlist_manager.determine_active_playlist(now)
    playlists = ([active] if active else []) + [p for p in playlist_manager.playlists if p is not active]
    for playlist in playlists:
        for plugin_instance in playlist.plugins:
            if plugin_instance.plugin_id == plugin_id:
                return playlist, plugin_instance
    return None, None


def hold_marker(image):
    """Current image with a small label in the bottom right corner."""
    img = image.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    font = get_font("Jost", max(14, img.height // 24), "bold")
    left, top, right, bottom = draw.textbbox((0, 0), HOLD_LABEL, font=font)
    pad, margin = 6, 8
    x1, y1 = img.width - margin, img.height - margin
    x0, y0 = x1 - (right - left) - 2 * pad, y1 - (bottom - top) - 2 * pad
    draw.rectangle((x0, y0, x1, y1), fill=(0, 0, 0))
    draw.text((x0 + pad - left, y0 + pad - top), HOLD_LABEL, font=font, fill=(255, 255, 255))
    return img


def shutdown_screen(dimensions, now):
    """Shown before powering off: e-paper keeps its image without power, so stale data must not look live."""
    width, height = dimensions
    img = Image.new("RGB", dimensions, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(0, 0, 0), width=max(2, height // 120))
    draw.text((width / 2, height * 0.42), "AUSGESCHALTET", anchor="mm", fill=(0, 0, 0),
              font=get_font("Jost", height // 7, "bold"))
    small = get_font("Jost", height // 18)
    draw.text((width / 2, height * 0.58), f"seit {now:%d.%m.%Y %H:%M}", anchor="mm", fill=(0, 0, 0), font=small)
    draw.text((width / 2, height * 0.88), "Zum Einschalten kurz vom Strom trennen", anchor="mm", fill=(0, 0, 0),
              font=small)
    return img


def run_shutdown():
    return os.system("sudo shutdown -h now")


class ButtonActions:
    def __init__(self, device_config, refresh_task, display_manager, mapping=None,
                 display_settings_for=None, shutdown_command=run_shutdown):
        self.device_config = device_config
        self.refresh_task = refresh_task
        self.display_manager = display_manager
        self.mapping = self.resolve_mapping(mapping)
        self.display_settings_for = display_settings_for or self._plugin_display_settings
        self.shutdown_command = shutdown_command

    @staticmethod
    def resolve_mapping(configured):
        """Defaults merged with the configured actions; an empty action disables a default."""
        mapping = {}
        for source in (DEFAULT_ACTIONS, configured or {}):
            for button, presses in source.items():
                for press_type, action in (presses or {}).items():
                    if action:
                        mapping[(button, press_type)] = action
                    else:
                        mapping.pop((button, press_type), None)
        return mapping

    def handle(self, button, press_type):
        action = self.mapping.get((button, press_type))
        logger.info(f"Button {button} ({press_type} press) -> {action or 'no action'}")
        if not action:
            return
        try:
            if action.startswith("show:"):
                self.show(action.split(":", 1)[1])
            elif action == "hold":
                self.toggle_hold()
            elif action == "shutdown":
                self.shutdown()
            else:
                logger.warning(f"Unknown button action: {action}")
        except Exception:
            logger.exception(f"Button action failed: {action}")

    def show(self, plugin_id):
        now = self.refresh_task._get_current_datetime()
        playlist, plugin_instance = find_instance(self.device_config.get_playlist_manager(), plugin_id, now)
        if not plugin_instance:
            logger.warning(f"No playlist contains an instance of plugin '{plugin_id}'.")
            return
        self.refresh_task.manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))

    def toggle_hold(self):
        playlist_manager = self.device_config.get_playlist_manager()
        now = self.refresh_task._get_current_datetime()

        if self.refresh_task.is_held():
            self.refresh_task.release()
            with self.refresh_task.condition:
                playlist = playlist_manager.determine_active_playlist(now)
                if not playlist or not playlist.plugins:
                    return
                plugin_instance = playlist.get_next_plugin()
            self.refresh_task.manual_update(PlaylistRefresh(playlist, plugin_instance))
            return

        playlist = playlist_manager.determine_active_playlist(now)
        if not playlist:
            logger.warning("No active playlist, nothing to hold.")
            return
        self.refresh_task.hold(playlist.name)

        # Mark the current image so the hold is visible; the display lock keeps the refresh task out meanwhile
        with self.refresh_task.condition:
            with Image.open(self.device_config.current_image_file) as current:
                marked = hold_marker(current)
            plugin_id = self.device_config.get_refresh_info().plugin_id
            self.display_manager.display_image(marked, device_display_settings=self.display_settings_for(plugin_id))

    def shutdown(self):
        logger.info("Shutdown requested via button.")
        now = self.refresh_task._get_current_datetime()
        self.refresh_task.stop()  # no refresh may overwrite the shutdown screen anymore

        dimensions = tuple(self.device_config.get_resolution())
        if self.device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        self.display_manager.display_image(shutdown_screen(dimensions, now),
                                           device_display_settings={"inky_saturation": 0.0})

        result = self.shutdown_command()
        if result not in (0, None):
            logger.error(f"Shutdown command failed ({result}), resuming the refresh task.")
            self.refresh_task.start()

    def _plugin_display_settings(self, plugin_id):
        try:
            plugin_config = self.device_config.get_plugin(plugin_id)
            return get_plugin_instance(plugin_config).get_device_display_settings() if plugin_config else {}
        except Exception:
            return {}


class ButtonListener(threading.Thread):
    def __init__(self, pins, on_press):
        super().__init__(daemon=True, name="buttons")
        self.pins = pins
        self.on_press = on_press
        self.tracker = PressTracker()

    def _request_lines(self):
        import gpiod
        import gpiodevice
        from gpiod.line import Bias, Direction, Edge

        chip = gpiodevice.find_chip_by_platform()
        names = {chip.line_offset_from_id(pin): name for name, pin in self.pins.items()}
        settings = gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP,
                                      edge_detection=Edge.BOTH, debounce_period=DEBOUNCE)
        request = chip.request_lines(consumer="inkypi-buttons", config=dict.fromkeys(names, settings))
        return request, names, gpiod.EdgeEvent.Type.FALLING_EDGE

    def run(self):
        try:
            request, names, falling_edge = self._request_lines()
        except Exception:
            logger.exception("Buttons are not available.")
            return
        logger.info(f"Listening for buttons: {self.pins}")

        while True:
            try:
                for event in request.read_edge_events():
                    # Buttons pull the line low while pressed; timestamps use the monotonic clock
                    duration = self.tracker.feed(names.get(event.line_offset),
                                                 event.event_type == falling_edge,
                                                 event.timestamp_ns / 1e9)
                    if duration is not None:
                        self.on_press(names[event.line_offset], classify_press(duration))
                        self.tracker.ignore_until(time.monotonic_ns() / 1e9)
            except Exception:
                logger.exception("Error while reading buttons.")
                time.sleep(5)


def start_buttons(device_config, refresh_task, display_manager):
    config = device_config.get_config("buttons") or {}
    if not config.get("enabled"):
        return None
    pins = {**DEFAULT_PINS, **config.get("pins", {})}
    actions = ButtonActions(device_config, refresh_task, display_manager, mapping=config.get("actions"))
    listener = ButtonListener(pins, actions.handle)
    listener.start()
    return listener
