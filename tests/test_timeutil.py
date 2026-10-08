from datetime import UTC, datetime, timedelta, timezone

import pytest

from core import db
from core.schema import Question
from core.timeutil import from_db, to_db, to_utc
from tests.conftest import make_question

IST = timezone(timedelta(hours=5, minutes=30))


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        to_utc(datetime(2026, 1, 1, 12, 0))
    with pytest.raises(ValueError, match="timezone"):
        to_db(datetime(2026, 1, 1, 12, 0))


def test_other_offsets_are_converted_to_utc() -> None:
    local = datetime(2026, 3, 1, 10, 0, tzinfo=IST)
    assert to_utc(local) == datetime(2026, 3, 1, 4, 30, tzinfo=UTC)
    assert to_utc(local).utcoffset() == timedelta(0)


def test_storage_format_is_fixed_width_utc() -> None:
    assert to_db(datetime(2026, 3, 1, 10, 0, tzinfo=IST)) == "2026-03-01T04:30:00.000000Z"
    assert to_db(datetime(2026, 3, 1, 4, 30, 0, 5, tzinfo=UTC)) == "2026-03-01T04:30:00.000005Z"


def test_round_trip_and_legacy_format() -> None:
    t = datetime(2026, 3, 1, 4, 30, 1, 250, tzinfo=UTC)
    assert from_db(to_db(t)) == t and from_db(to_db(t)).tzinfo is UTC
    assert from_db("2026-03-01T04:30:00+00:00") == datetime(2026, 3, 1, 4, 30, tzinfo=UTC)


def test_string_order_equals_time_order_across_offsets() -> None:
    times = [
        datetime(2026, 3, 1, 10, 0, tzinfo=IST),  # 04:30 UTC
        datetime(2026, 3, 1, 4, 29, 59, 999999, tzinfo=UTC),
        datetime(2026, 3, 1, 4, 30, 0, 1, tzinfo=UTC),
        datetime(2026, 2, 28, 23, 59, tzinfo=timezone(timedelta(hours=-8))),  # 07:59 UTC next day
        datetime(2026, 3, 1, 4, 30, tzinfo=UTC),
    ]
    assert sorted(times, key=lambda t: to_db(t)) == sorted(times)


def test_from_db_rejects_naive_strings() -> None:
    with pytest.raises(ValueError):
        from_db("2026-03-01T04:30:00")


def test_question_created_at_is_stored_as_utc_z() -> None:
    conn = db.connect(":memory:")
    q = Question.model_validate(make_question(created_at="2026-03-01T10:00:00+05:30"))
    db.upsert_questions(conn, [q])
    raw = conn.execute("SELECT created_at FROM questions").fetchone()[0]
    assert raw == "2026-03-01T04:30:00.000000Z"
    got = db.get_question(conn, q.id)
    assert got.created_at == datetime(2026, 3, 1, 4, 30, tzinfo=UTC) and got == q.model_copy(
        update={"created_at": got.created_at}
    )
