"""Timetable logic: turns Home Assistant calendar events of the WebUntis integration into display rows.

Pure functions without I/O, so they can be tested without Home Assistant."""
from datetime import datetime, timedelta

MERGE_GAP = timedelta(minutes=5)   # consecutive lessons closer than this form one block (double period)

# Prefixes the WebUntis integration puts in front of the subject
STATE_PREFIXES = {"Cancelled: ": "cancelled", "Irregular: ": "irregular", "Room change: ": "room_change"}
TAGS = {"regular": None, "cancelled": "ENTFALL", "irregular": "VERTR.", "room_change": "RAUM"}

WEEKDAYS = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]


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
    """The day to show (today, or tomorrow for mode "tomorrow") if any child has at least one lesson then.
    Cancelled lessons count. None if nobody has school, e.g. on Friday and Saturday evening."""
    day = today if mode == "today" else today + timedelta(days=1)
    if any(lessons_on(lessons, day) for lessons in lesson_lists):
        return day
    return None


def day_title(day, today):
    return f"{'HEUTE' if day == today else 'MORGEN'} · {WEEKDAYS[day.weekday()]} {day:%d.%m.}"


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
