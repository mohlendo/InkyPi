# School timetable on the Inky ("Stundenplan") – design

Status: approved in chat on 2026-11-10, ready for an implementation plan.

## Goal

Show the children's school timetables on the Inky Impression:

- **mornings 06:00–07:45:** today's timetable, alternating with the weather view
- **evenings 19:30–20:30:** the timetable of the next school day, exclusively

Days on which none of the configured children has school are skipped, so the display shows its normal content
instead.

## Decisions

- **Home Assistant is the data source.** The WebUntis community integration
  ([JonasJoKuJonas/homeassistant-WebUntis](https://github.com/JonasJoKuJonas/homeassistant-WebUntis)) already
  runs in Home Assistant and creates one calendar entity per child. InkyPi reads these calendars. It does not
  talk to WebUntis itself. WebUntis credentials stay in Home Assistant only. The Home Assistant dashboard is out
  of scope (it already exists).
- **New plugin `timetable`**, separate from the `home_assistant` plugin. It reuses the Home Assistant base URL
  (a setting) and the `HOME_ASSISTANT_TOKEN` env key.
- **Layout "A" (list)**, in the approved high-contrast style of the Home Assistant room view (see
  `src/plugins/home_assistant/render/home_assistant.css`):
  - Courier Prime, pure palette colors, color only as fills and markers.
  - One column per child.
  - One row per lesson block, showing the time range, the subject (large) and the rooms.
- **Small core change for skipping:** a plugin can signal "nothing to show" and the refresh loop moves on to
  other content.

## Data source

`GET {baseUrl}/api/calendars/{entity_id}?start=…&end=…` for each configured child. The response is the standard
Home Assistant calendar REST API: a list of events with `summary`, `start.dateTime`, `end.dateTime`, `location`
and `description`.

The integration encodes the lesson state as a prefix in `summary`:

| Prefix          | Meaning                        | Shown as                      |
|-----------------|--------------------------------|-------------------------------|
| `Cancelled: `   | lesson cancelled               | tag `ENTFALL`, subject struck through in red |
| `Irregular: `   | substitution or other change   | tag `VERTR.`                  |
| `Room change: ` | room changed                   | tag `RAUM`                    |
| none            | regular lesson                 | no tag                        |

`location` is the room. `description` (teacher/course info) is not shown.

Observed data:

- Lessons are 45-minute periods. Double periods come as two consecutive events.
- One child's calendar is a class timetable on purpose. Course groups therefore appear as several events with
  the same subject and time but different rooms.
- The calendar currently contains only the running school week.

## Plugin settings (per instance)

- `baseUrl`: Home Assistant URL
- `children`: ordered list of `{name, calendar}` pairs, for example `Kind A` → `calendar.stundenplan_kind_a`.
  Names live only in the device config on the Pi, never in the repo.
- `day`: `today` | `next_school_day`

## Processing

Pure functions, unit-tested without Home Assistant:

1. **Parse.** Turn an event into a lesson `{start, end, subject, room, state}`. The prefix is stripped from the
   subject and mapped to the state `cancelled` | `irregular` | `room_change` | `regular`.
2. **Group parallel courses.** Lessons with the same start, end, subject and state become one lesson with the
   list of their rooms. Order is kept and duplicates are removed.
3. **Merge double periods.** Consecutive lessons with the same subject, rooms and state, where one ends at or
   within 5 minutes before the next starts, become one block.
4. **Day span.** First start to last end, counting only lessons that are not cancelled. A child whose lessons
   are all cancelled shows the cancelled blocks and no span.
5. **Target day.**
   - `today`: the device-local date.
   - `next_school_day`: the first date from tomorrow up to 4 days ahead on which at least one child has at least
     one lesson. Cancelled lessons count, so a fully cancelled day is shown with its `ENTFALL` tags. Each child's
     calendar is fetched once for the whole look-ahead window.
6. **Skip.** If no child has any lesson on the target day (cancelled ones included), the plugin raises
   `SkipRefresh`.

## Rendering

- **Header:**
  - left: `HEUTE · DI 10.11.` or `MORGEN · MI 11.11.` (another weekday name if the next school day is not
    tomorrow, for example `MONTAG · 16.11.`)
  - right: `Stand HH:MM`
- **Per child column:** a yellow name tape and the day span (`8:00–13:10`), followed by rows. Double blocks get
  more height than single periods.
- **Rows:** start/end time, subject, tag (if any), rooms joined with ` · `.
- **Empty columns:**
  - a child without lessons on that day shows `KEIN UNTERRICHT`
  - a child whose calendar request failed shows `KEINE DATEN`
- Saturation override `{'inky_saturation': 0.0}` like the room view, so pure colors are not dithered.

## Skipping (core change)

- A new exception `SkipRefresh` lives in the base plugin module.
- `RefreshTask` handles it only for playlist refreshes. It tries the next plugins of the same playlist, then the
  plugins of the other active playlists in priority order. It stops at the first plugin that returns an image.
- If everything skips, nothing changes on the display.
- The skip is logged at info level, not as an error with a stack trace.
- A manual "display now" of a skipping instance reports the skip message to the caller (no display change).
- `PlaylistManager` gets a helper that returns all active playlists sorted by priority. The existing
  `determine_active_playlist` keeps its behavior.

## Schedule (device config on the Pi)

- **"Schule Morgen", 06:00–07:45:**
  - `Stundenplan heute` (`day: today`)
  - a copy of the morning weather instance
- **"Schule Abend", 19:30–20:30:** `Stundenplan nächster Schultag` (`day: next_school_day`)
- Both windows are shorter than "Morgen" and "Abend" and therefore take priority.
- Timetable instances refresh at every cycle (refresh interval ≤ cycle interval). Changes appear within 5
  minutes and the skip check runs every time. Unchanged images are not redrawn (existing image-hash check).

## Error handling

- Home Assistant unreachable, or every calendar request fails: the plugin raises a normal error, so the
  previous image stays and the error is logged.
- One calendar fails: only that column shows `KEINE DATEN`.

## Testing

- **Unit tests** (anonymized fixtures):
  - prefix parsing
  - grouping of parallel courses
  - merging of double periods
  - day span with cancellations
  - target-day selection, including weekends and look-ahead
  - skip
- **Render test:** `generate_image` with a fake Home Assistant response produces an 800×480 image. One column
  failing shows `KEINE DATEN`.
- **Refresh-loop tests** for `SkipRefresh`:
  - falls through to the next plugin in the playlist
  - falls through to the next active playlist
  - leaves the display untouched when everything skips
- **Visual check before deploying:** panel simulation with real data, shown to the user; afterwards a photo of
  the real display.

## Known risk

The calendar holds only the running week. If Monday's lessons are not yet in the calendar on Sunday evening,
the evening view is skipped. It is not shown wrong. Check on the first weekend.
