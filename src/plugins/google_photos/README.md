# Load image from Google Photos album

Display a random photo from a shared Google Photos album on your InkyPi.

No API key is required. The plugin fetches the shared album page and extracts image URLs, then downloads a photo sized to your device's resolution.

## Requirements
- A Google Photos album with sharing enabled ("Anyone with the link").
- The album share link URL (looks like `https://photos.app.goo.gl/XXXXXXXXXXXX`).
- Network access from the InkyPi host.

## Setup
1. Create or open an album in Google Photos and add the photos you want to display.
2. Click Share → Create link, then copy the album share link.
3. In InkyPi, add the "Google Photos Album" plugin.
4. Paste the album share link into the plugin settings under "Google Photos album share link URL" and save.

## Settings
- **Framing**
  - *Automatic* (default): photos that keep at least 75 % of their area when cropped (e.g. 16:9, 4:3, 3:2 landscape) fill the display; portrait, square and panorama photos are shown completely.
  - *Always fill the display*: crop every photo to the display.
  - *Always show the whole photo*: never crop.
- **Background around fitted photos**: a blurred copy of the photo (default) or a solid color.
- **Photo saturation / contrast**: optional boost applied before the image is sent to the display (1.00 = unchanged). E-paper photos often look pale; values around 1.2–1.4 can help.

## How it works (brief)
- The plugin loads the album's shared page and parses the embedded data to find image entries. The photo list is cached for one hour; if reloading fails, the last known list is used.
- Photos are shown in random order without repetition: every photo is shown once before any photo repeats. Instances showing the same album share one round. The order restarts when the InkyPi service restarts.
- Google scales the photo server-side to the smallest size that covers the display (based on the photo's original size), so it is never upscaled afterwards.

## Notes & limitations
- Google may change the shared page format at any time; if parsing fails, check logs and update InkyPi.
- Very large albums can take longer to parse on first load.
- Only photos are supported; videos (if present in the album) are ignored.
- If your device is configured for vertical orientation, the plugin will request swapped width/height to match.

## Troubleshooting
- "Failed to load image" or empty screen:
  - Ensure the album is shared with "Anyone with the link" and the URL is correct.
  - Try opening the link in a browser where you are not logged in to confirm access.
  - Check your network connectivity and DNS on the InkyPi host.
- HTTP 403/429 errors:
  - Wait and retry; Google may temporarily rate-limit access.
- Look at the InkyPi logs for messages starting with `google_photos` for detailed errors.

## Security & privacy
- Shared album links are unlisted but not secret; anyone with the link can view the album.
- The plugin downloads images directly from Google Photos for rendering and does not upload your images elsewhere.

