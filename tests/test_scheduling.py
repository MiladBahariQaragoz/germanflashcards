from datetime import datetime, timedelta, timezone

from bot.scheduling import due_session_for, interval_to_sessions

_NOW = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)


def _due(**kwargs):
    return _NOW + timedelta(**kwargs)


# ── interval_to_sessions ────────────────────────────────────────────────────────

def test_sub_day_intervals_map_to_zero_sessions():
    # FSRS learning steps (minutes/hours) reshow within the same session.
    assert interval_to_sessions(_due(minutes=1), _NOW) == 0
    assert interval_to_sessions(_due(minutes=10), _NOW) == 0
    assert interval_to_sessions(_due(hours=23), _NOW) == 0


def test_exactly_one_day_is_one_session():
    assert interval_to_sessions(_due(days=1), _NOW) == 1


def test_multi_day_interval_rounds_to_sessions():
    assert interval_to_sessions(_due(days=5), _NOW) == 5
    assert interval_to_sessions(_due(days=30), _NOW) == 30


def test_fractional_days_round_to_nearest_session():
    assert interval_to_sessions(_due(days=4, hours=14), _NOW) == 5  # 4.58d → 5
    assert interval_to_sessions(_due(days=4, hours=6), _NOW) == 4   # 4.25d → 4


def test_past_due_is_zero_sessions():
    # A due date already in the past never yields a negative wait.
    assert interval_to_sessions(_due(days=-3), _NOW) == 0


# ── due_session_for ─────────────────────────────────────────────────────────────

def test_due_session_offsets_counter_by_interval():
    assert due_session_for(0, 5) == 5
    assert due_session_for(12, 1) == 13
    assert due_session_for(7, 0) == 7  # 0-session card is due this/next session
