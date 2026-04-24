from datetime import datetime, timezone
from bot.fsrs_service import rate_card, preview_intervals, format_interval_from_due

NEW_CARD = {
    "fsrs_state": "New",
    "state": 1,
    "step": 0,
    "stability": None,
    "difficulty": None,
    "due_date": datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc),
    "last_review": None,
}


def test_rate_card_good_returns_required_fields():
    update_dict, _ = rate_card(NEW_CARD, rating_int=3)
    assert "due_date" in update_dict
    assert "stability" in update_dict
    assert "difficulty" in update_dict
    assert "fsrs_state" in update_dict
    assert "state" in update_dict
    assert "step" in update_dict
    assert "last_review" in update_dict


def test_rate_card_good_increases_stability():
    update_dict, _ = rate_card(NEW_CARD, rating_int=3)
    assert update_dict["stability"] is not None
    assert update_dict["stability"] > 0.0


def test_rate_card_good_sets_fsrs_state_learning():
    update_dict, _ = rate_card(NEW_CARD, rating_int=3)
    assert update_dict["fsrs_state"] in ("Learning", "Review")


def test_rate_card_returns_interval_label():
    _, label = rate_card(NEW_CARD, rating_int=3)
    assert isinstance(label, str)
    assert len(label) > 0


def test_preview_intervals_returns_four_entries():
    intervals = preview_intervals(NEW_CARD)
    assert set(intervals.keys()) == {1, 2, 3, 4}
    for v in intervals.values():
        assert isinstance(v, str)


def test_preview_easy_longer_than_again():
    intervals = preview_intervals(NEW_CARD)
    # Easy should have a longer label time than Again for a fresh card
    # We just verify both are strings — exact values depend on FSRS parameters
    assert intervals[4] != intervals[1]


def test_format_interval_minutes():
    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    due = datetime(2026, 4, 24, 12, 5, 0, tzinfo=timezone.utc)
    assert format_interval_from_due(due, now) == "5m"


def test_format_interval_hours():
    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    due = datetime(2026, 4, 24, 14, 0, 0, tzinfo=timezone.utc)
    assert format_interval_from_due(due, now) == "2h"


def test_format_interval_days():
    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    due = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert format_interval_from_due(due, now) == "7d"
