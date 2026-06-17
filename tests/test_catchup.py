import pytest

from bot.catchup import plan_installments


def test_no_overflow_when_due_within_cap():
    assert plan_installments(60, 60) == []
    assert plan_installments(10, 60) == []
    assert plan_installments(0, 60) == []


def test_single_card_over_cap_goes_to_tomorrow():
    assert plan_installments(61, 60) == [1]


def test_overflow_fills_days_in_chunks_of_per_day():
    # 130 due, cap 60: keep 60 today, next 60 → +1 day, final 10 → +2 days.
    offsets = plan_installments(130, 60)
    assert len(offsets) == 70
    assert offsets[:60] == [1] * 60
    assert offsets[60:] == [2] * 10


def test_small_per_day_for_easy_reasoning():
    # 5 due, cap 2: keep 2 today; [3rd,4th] → +1; [5th] → +2.
    assert plan_installments(5, 2) == [1, 1, 2]


def test_offsets_length_equals_moved_count():
    assert len(plan_installments(200, 60)) == 200 - 60


def test_invalid_per_day_raises():
    with pytest.raises(ValueError):
        plan_installments(100, 0)
