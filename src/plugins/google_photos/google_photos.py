from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session
from utils.image_utils import pad_image_blur
from PIL import Image, ImageColor, ImageEnhance, ImageOps
from io import BytesIO
from typing import List, Dict, Optional, Any
import logging
import math
import random
import re
import time
import json5

ImageInfo = Dict[str, Any]
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 40
ALBUM_CACHE_TTL_S = 60 * 60   # the album list is re-read at most once per hour
AUTO_FILL_MIN_KEPT = 0.75     # "auto" fills the display if at least this share of the photo stays visible


# --- album page parsing ----------------------------------------------------------------------

def get_shared_album_html(album_shared_url: str) -> Optional[str]:
    try:
        response = get_http_session().get(album_shared_url, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error loading Google Photos album page {album_shared_url}: {e}")
    return None

def parse_phase_1(input_html: str) -> Optional[str]:
    """
    Extracts the longest JSON-like string.
    """
    re_pattern = r"(?<=AF_initDataCallback\()(?=.*data)(\{[\s\S]*?)(\);<\/script>)"

    longest_match_content = ""
    for match in re.finditer(re_pattern, input_html):
        current_match_content = match.group(1)
        if len(current_match_content) > len(longest_match_content):
            longest_match_content = current_match_content

    return longest_match_content if longest_match_content else None

def parse_phase_2(input_str: str) -> Optional[Any]:
    try:
        return json5.loads(input_str)
    except Exception as e:
        logger.error(f"Error parsing JS object with json5 in parse_phase_2: {e}")
        logger.error(f"Problematic string start: {input_str[:200]}...")
        return None

def is_contain_data(obj: Any) -> bool:
    """Checks if an object is a dictionary and contains a 'data' key."""
    return isinstance(obj, dict) and 'data' in obj

def is_array(obj: Any) -> bool:
    """Checks if an object is a list."""
    return isinstance(obj, list)

def parse_phase_3(input_data: Any) -> Optional[List[ImageInfo]]:
    if not is_contain_data(input_data):
        return None

    d = input_data.get('data')
    if not is_array(d) or not d:
        return None

    # Assuming d[1] is the main array of image entries based on observed Google Photos structure
    arr = d[1]
    if not is_array(arr):
        return None

    parsed_images: List[ImageInfo] = []
    for e in arr:
        # Each 'e' is expected to be an array with at least 6 elements
        if not is_array(e) or len(e) < 6:
            continue

        uid = e[0]
        image_update_date = e[2]
        album_add_date = e[5]

        # Basic type checks for the main elements
        if not isinstance(uid, str) or not isinstance(image_update_date, (int, float)) or not isinstance(
                album_add_date, (int, float)):
            continue

        detail = e[1]  # This is expected to be another nested array for URL, width, height
        if not is_array(detail) or len(detail) < 3:
            continue

        url = detail[0]
        width = detail[1]
        height = detail[2]

        # Basic type checks for image details
        if not isinstance(url, str) or not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            continue
        if width <= 0 or height <= 0:
            continue

        # Append the parsed ImageInfo
        parsed_images.append({"uid": uid, "url": url, "width": int(width), "height": int(height),
                              "imageUpdateDate": int(image_update_date), "albumAddDate": int(album_add_date), })

    return parsed_images if parsed_images else None

def parse_album_html(html: str) -> Optional[List[ImageInfo]]:
    ph1 = parse_phase_1(html)
    if ph1 is None:
        logger.error("Phase 1 parsing failed: Could not extract data block from HTML.")
        return None

    ph2 = parse_phase_2(ph1)
    if ph2 is None:
        logger.error("Phase 2 parsing failed: Could not parse JSON/JS object data.")
        return None

    result = parse_phase_3(ph2)
    if result is None:
        logger.error("Phase 3 parsing failed: Could not extract image info from parsed data.")
    return result

def fetch_image_urls(album_shared_url: str) -> Optional[List[ImageInfo]]:
    html = get_shared_album_html(album_shared_url)
    if html is None:
        return None
    return parse_album_html(html)


# --- album cache and photo selection -------------------------------------------------------

class AlbumCache:
    """Keeps the parsed photo list per album URL, so the ~1 MB album page is not loaded on every refresh.

    If reloading fails, the last known list is used.
    """

    def __init__(self, ttl_seconds):
        self.ttl_seconds = ttl_seconds
        self.entries = {}

    def get(self, url, fetch, now):
        entry = self.entries.get(url)
        if entry and now - entry[0] < self.ttl_seconds:
            return entry[1]

        try:
            infos = fetch(url)
        except Exception as e:
            logger.error(f"Loading Google Photos album failed: {e}")
            infos = None

        if infos:
            self.entries[url] = (now, infos)
            return infos
        if entry:
            logger.warning("Using cached Google Photos album list.")
            return entry[1]
        return None


class PhotoPicker:
    """Random order without repetition: every photo is shown once per round, and a new round
    never starts with the photo that ended the previous one. Photos added to the album join
    the current round."""

    def __init__(self):
        self.shown = set()
        self.last = None

    def pick(self, ids, rng=random):
        candidates = [i for i in ids if i not in self.shown]
        if not candidates:
            self.shown = set()
            candidates = [i for i in ids if i != self.last] or list(ids)
        choice = rng.choice(candidates)
        self.shown.add(choice)
        self.last = choice
        return choice


# InkyPi keeps one plugin object per process, so this state lives as long as the service runs.
# Keyed by album URL, so several playlist instances showing the same album share one round.
_album_cache = AlbumCache(ALBUM_CACHE_TTL_S)
_pickers: Dict[str, PhotoPicker] = {}


# --- image sizing and processing -------------------------------------------------------------

def cover_size(width, height, dimensions):
    """Smallest size with the photo's aspect ratio that covers the whole display."""
    target_w, target_h = dimensions
    scale = max(target_w / width, target_h / height)
    return (max(target_w, math.ceil(width * scale - 1e-6)),
            max(target_h, math.ceil(height * scale - 1e-6)))

def kept_fraction(width, height, dimensions):
    """Share of the photo that stays visible when it is cropped to fill the display."""
    photo_ratio = width / height
    display_ratio = dimensions[0] / dimensions[1]
    return min(photo_ratio, display_ratio) / max(photo_ratio, display_ratio)

def resolve_scale_mode(mode, width, height, dimensions):
    if mode in ("fill", "fit"):
        return mode
    return "fill" if kept_fraction(width, height, dimensions) >= AUTO_FILL_MIN_KEPT else "fit"

def download_url(image_info, size):
    # Google scales server-side to fit into w x h; requesting the cover size avoids any upscaling later
    base_url = re.sub(r'=[swh]\d+(-h\d+)?$', '', image_info['url'])
    return f"{base_url}=w{size[0]}-h{size[1]}"

def grab_image(image_info, size):
    url = download_url(image_info, size)
    try:
        response = get_http_session().get(url, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        img = ImageOps.exif_transpose(Image.open(BytesIO(response.content)))
        return img.convert("RGB")
    except Exception as e:
        logger.error(f"Error downloading photo {url}: {e}")
        return None

def compose(img, dimensions, scale_mode, background_option="blur", background_color=None):
    if scale_mode == "fill":
        return ImageOps.fit(img, dimensions, method=Image.Resampling.LANCZOS)
    if background_option == "color":
        color = ImageColor.getrgb(background_color or "#ffffff")
        return ImageOps.pad(img, dimensions, color=color, method=Image.Resampling.LANCZOS)
    return pad_image_blur(img, dimensions)

def parse_factor(value, default=1.0):
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return default
    return factor if math.isfinite(factor) and factor > 0 else default

def enhance(img, saturation=1.0, contrast=1.0):
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    return img


class GooglePhotos(BasePlugin):
    def generate_image(self, settings, device_config):
        url = settings.get('url')
        if not url:
            raise RuntimeError("URL is required.")

        dimensions = tuple(device_config.get_resolution())
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        image_infos = _album_cache.get(url, fetch_image_urls, time.monotonic())
        if not image_infos:
            raise RuntimeError("Could not fetch image infos from Google Photos album.")

        photos = {info["uid"]: info for info in image_infos}
        photo = photos[_pickers.setdefault(url, PhotoPicker()).pick(list(photos))]

        size = cover_size(photo["width"], photo["height"], dimensions)
        scale_mode = resolve_scale_mode(settings.get("scaleMode", "auto"), photo["width"], photo["height"], dimensions)
        logger.info(f"Showing photo {photo['uid'][:12]}… ({photo['width']}x{photo['height']}, {scale_mode}) "
                    f"from {len(photos)} photos")

        image = grab_image(photo, size)
        if not image:
            raise RuntimeError("Failed to load image, please check logs.")

        image = compose(image, dimensions, scale_mode, settings.get("backgroundOption", "blur"),
                        settings.get("backgroundColor"))
        return enhance(image, parse_factor(settings.get("saturation")), parse_factor(settings.get("contrast")))
