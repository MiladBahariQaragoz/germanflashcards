import pytest

from bot.catchup import plan_installments, new_cards_allowed, cap_daily_due


def test_no_overflow_when_due_within_cap():
    assert plan_installments(60, 60) == []
    assert plan_installments(10, 60) == []
    assert plan_installments(0, 60) == []


def test_single_card_over_cap_goes_to_tomorrow():
    assert plan_installments(61, 60) == [1]


def test_next_day_max_defaults_to_today_max():
    # 130 due, cap 60, no next_day_max: keep 60 today, next 60 → +1, final 10 → +2.
    offsets = plan_installments(130, 60)
    assert len(offsets) == 70
    assert offsets[:60] == [1] * 60
    assert offsets[60:] == [2] * 10


def test_today_max_larger_than_next_day_max():
    # 60 today, then 40/day after. 150 due: keep 60, next 40 → +1, next 40 → +2,
    # final 10 → +3.
    offsets = plan_installments(150, 60, 40)
    assert len(offsets) == 90
    assert offsets[:40] == [1] * 40
    assert offsets[40:80] == [2] * 40
    assert offsets[80:] == [3] * 10


def test_small_caps_for_easy_reasoning():
    # 6 due, keep 3 today, 2/day after: [4th,5th] → +1; [6th] → +2.
    assert plan_installments(6, 3, 2) == [1, 1, 2]


def test_offsets_length_equals_moved_count():
    assert len(plan_installments(200, 60, 40)) == 200 - 60


def test_invalid_today_max_raises():
    with pytest.raises(ValueError):
        plan_installments(100, 0)


def test_invalid_next_day_max_raises():
    with pytest.raises(ValueError):
        plan_installments(100, 60, 0)


# ── new_cards_allowed ───────────────────────────────────────────────────────────

def test_new_cards_allowed_when_at_or_below_cap():
    assert new_cards_allowed(0, 40) is True
    assert new_cards_allowed(40, 40) is True


def test_new_cards_paused_when_backlog_over_cap():
    assert new_cards_allowed(41, 40) is False
    assert new_cards_allowed(175, 40) is False  # big combined backlog → no new


# ── cap_daily_due ─────────────────────────────────────────────────────────────

def test_cap_serves_oldest_due_first_up_to_cap():
    # A 200-card backlog capped at 60 yields exactly the 60 earliest-due cards.
    cards = [{"due_session": s} for s in range(200, 0, -1)]  # 200..1
    capped = cap_daily_due(cards, 60)
    assert len(capped) == 60
    assert [c["due_session"] for c in capped] == list(range(1, 61))


def test_cap_returns_all_when_under_cap_sorted_by_due_session():
    cards = [{"due_session": 3}, {"due_session": 1}, {"due_session": 2}]
    assert [c["due_session"] for c in cap_daily_due(cards, 60)] == [1, 2, 3]


def test_cap_empty_list():
    assert cap_daily_due([], 60) == []
