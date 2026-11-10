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
