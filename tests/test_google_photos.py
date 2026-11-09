import random

import pytest
from PIL import Image

from src.plugins.google_photos import google_photos as gp


def album_entry(uid, width, height, url=None):
    url = url or f"https://lh3.googleusercontent.com/pw/{uid}"
    return f'["{uid}",["{url}",{width},{height},null],1700000000000,null,null,1700000000001]'


def album_html(*entries):
    """Minimal synthetic version of the data block embedded in a shared album page."""
    data = f"[null,[{','.join(entries)}]]"
    return (f"<html><script>AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});"
            f"</script></html>")


def info(uid, width, height):
    return {"uid": uid, "url": f"https://lh3.googleusercontent.com/pw/{uid}", "width": width, "height": height,
            "imageUpdateDate": 1, "albumAddDate": 1}


DIMS = (800, 480)


# --- parsing ---------------------------------------------------------------------------------

def test_parse_album_html():
    html = album_html(album_entry("a", 4032, 2268), album_entry("b", 2268, 4032))

    infos = gp.parse_album_html(html)

    assert [(i["uid"], i["width"], i["height"]) for i in infos] == [("a", 4032, 2268), ("b", 2268, 4032)]
    assert infos[0]["url"] == "https://lh3.googleusercontent.com/pw/a"


def test_parse_album_html_skips_malformed_entries():
    html = album_html(album_entry("a", 4032, 2268), '["broken"]')

    assert [i["uid"] for i in gp.parse_album_html(html)] == ["a"]


def test_parse_album_html_without_data_returns_none():
    assert gp.parse_album_html("<html>nothing here</html>") is None


# --- sizes and scale mode --------------------------------------------------------------------

@pytest.mark.parametrize("width,height,expected", [
    (2268, 4032, (800, 1423)),   # portrait: full display width, overflowing height
    (4032, 2268, (854, 480)),    # 16:9 landscape: full display height, overflowing width
    (4000, 2400, (800, 480)),    # exactly the display ratio
])
def test_cover_size_covers_the_display(width, height, expected):
    assert gp.cover_size(width, height, DIMS) == expected


def test_download_url_requests_cover_size():
    url = gp.download_url(info("a", 2268, 4032), (800, 1423))

    assert url == "https://lh3.googleusercontent.com/pw/a=w800-h1423"


def test_download_url_replaces_existing_size_suffix():
    photo = info("a", 2268, 4032)
    photo["url"] += "=w100-h100"

    assert gp.download_url(photo, (800, 1423)) == "https://lh3.googleusercontent.com/pw/a=w800-h1423"


@pytest.mark.parametrize("width,height,expected", [
    (4032, 2268, "fill"),  # 16:9 keeps 94 %
    (4032, 3024, "fill"),  # 4:3 keeps 80 %
    (3000, 3000, "fit"),   # square would lose 40 %
    (2268, 4032, "fit"),   # portrait would lose two thirds
])
def test_auto_scale_mode(width, height, expected):
    assert gp.resolve_scale_mode("auto", width, height, DIMS) == expected


@pytest.mark.parametrize("mode", ["fill", "fit"])
def test_explicit_scale_mode(mode):
    assert gp.resolve_scale_mode(mode, 2268, 4032, DIMS) == mode


# --- picking without repetition --------------------------------------------------------------

def test_picker_shows_every_photo_once_per_round():
    picker = gp.PhotoPicker()
    ids = [str(i) for i in range(11)]
    rng = random.Random(1)

    first_round = [picker.pick(ids, rng) for _ in ids]

    assert sorted(first_round) == sorted(ids)


def test_picker_never_repeats_across_round_boundary():
    ids = ["a", "b", "c"]
    for seed in range(50):
        picker = gp.PhotoPicker()
        rng = random.Random(seed)
        picks = [picker.pick(ids, rng) for _ in range(30)]
        assert all(a != b for a, b in zip(picks, picks[1:]))


def test_picker_handles_album_changes():
    picker = gp.PhotoPicker()
    rng = random.Random(3)
    first = picker.pick(["a", "b"], rng)

    # a photo added to the album joins the current round, the one already shown does not come back
    picks = [picker.pick(["a", "b", "c"], rng) for _ in range(2)]

    assert set(picks) == {"a", "b", "c"} - {first}


def test_single_photo_album():
    picker = gp.PhotoPicker()
    assert [picker.pick(["a"], random.Random(0)) for _ in range(3)] == ["a", "a", "a"]


# --- album cache -----------------------------------------------------------------------------

class CountingFetch:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_album_cache_reuses_list_within_ttl():
    cache = gp.AlbumCache(ttl_seconds=3600)
    fetch = CountingFetch([info("a", 1, 1)], [info("b", 1, 1)])

    first = cache.get("url", fetch, now=0)
    second = cache.get("url", fetch, now=3599)
    third = cache.get("url", fetch, now=3600)

    assert fetch.calls == 2
    assert first == second == [info("a", 1, 1)]
    assert third == [info("b", 1, 1)]


@pytest.mark.parametrize("failure", [None, ConnectionError("offline")])
def test_album_cache_falls_back_to_stale_list(failure):
    cache = gp.AlbumCache(ttl_seconds=10)
    fetch = CountingFetch([info("a", 1, 1)], failure)

    cache.get("url", fetch, now=0)

    assert cache.get("url", fetch, now=100) == [info("a", 1, 1)]


def test_album_cache_without_data_returns_none():
    cache = gp.AlbumCache(ttl_seconds=10)

    assert cache.get("url", CountingFetch(None), now=0) is None


# --- color -----------------------------------------------------------------------------------

def test_enhance_defaults_leave_image_unchanged():
    img = Image.new("RGB", (4, 4), (120, 80, 60))

    assert gp.enhance(img, 1.0, 1.0).tobytes() == img.tobytes()


def test_enhance_boosts_saturation():
    img = Image.new("RGB", (4, 4), (120, 80, 60))

    r, g, b = gp.enhance(img, 1.5, 1.0).getpixel((0, 0))

    assert r - b > 120 - 60


@pytest.mark.parametrize("value,expected", [(None, 1.0), ("", 1.0), ("1.3", 1.3), ("abc", 1.0), ("-1", 1.0)])
def test_parse_factor(value, expected):
    assert gp.parse_factor(value) == expected


# --- generate_image --------------------------------------------------------------------------

class FakeDeviceConfig:
    def __init__(self, orientation="horizontal"):
        self.orientation = orientation

    def get_resolution(self):
        return [800, 480]

    def get_config(self, key=None, default=None):
        return {"orientation": self.orientation}.get(key, default)


@pytest.fixture
def album(monkeypatch):
    monkeypatch.setattr(gp, "_album_cache", gp.AlbumCache(ttl_seconds=3600))
    monkeypatch.setattr(gp, "_pickers", {})
    requested = []

    def fake_grab_image(photo, size):
        requested.append((photo["uid"], size))
        return Image.new("RGB", size, (200, 30, 30))

    monkeypatch.setattr(gp, "grab_image", fake_grab_image)

    def use(*infos):
        monkeypatch.setattr(gp, "fetch_image_urls", lambda url: list(infos))
        return requested
    return use


def generate(settings, device_config=None):
    plugin = gp.GooglePhotos({"id": "google_photos"})
    return plugin.generate_image({"url": "https://photos.app.goo.gl/x", **settings}, device_config or FakeDeviceConfig())


def test_generate_fills_landscape_photo_at_display_size(album):
    requested = album(info("a", 4032, 2268))

    img = generate({})

    assert img.size == DIMS
    assert requested == [("a", (854, 480))]


def test_generate_fits_portrait_photo_with_color_background(album):
    album(info("p", 2268, 4032))

    img = generate({"backgroundOption": "color", "backgroundColor": "#00ff00"})

    assert img.size == DIMS
    assert img.getpixel((5, 240)) == (0, 255, 0)        # side border
    assert img.getpixel((400, 240)) == (200, 30, 30)    # photo in the middle


def test_generate_fits_portrait_photo_with_blur_background(album):
    album(info("p", 2268, 4032))

    img = generate({})

    assert img.size == DIMS
    assert img.getpixel((5, 240)) == (200, 30, 30)  # blurred copy of the photo, not a flat color


def test_generate_vertical_display(album):
    requested = album(info("a", 4032, 2268))

    img = generate({}, FakeDeviceConfig(orientation="vertical"))

    assert img.size == (480, 800)
    assert requested[0][1] == gp.cover_size(4032, 2268, (480, 800))


def test_generate_cycles_through_album_without_repeats(album):
    requested = album(*[info(str(i), 4032, 2268) for i in range(5)])

    for _ in range(5):
        generate({})

    assert sorted(uid for uid, _ in requested) == ["0", "1", "2", "3", "4"]


def test_generate_without_url_raises():
    with pytest.raises(RuntimeError, match="URL is required"):
        gp.GooglePhotos({"id": "google_photos"}).generate_image({}, FakeDeviceConfig())


def test_generate_unreachable_album_raises(album, monkeypatch):
    monkeypatch.setattr(gp, "fetch_image_urls", lambda url: None)

    with pytest.raises(RuntimeError, match="Could not fetch"):
        generate({})


def test_generate_failed_download_raises(album, monkeypatch):
    album(info("a", 4032, 2268))
    monkeypatch.setattr(gp, "grab_image", lambda photo, size: None)

    with pytest.raises(RuntimeError, match="Failed to load image"):
        generate({})
