from bot.streak import streak_transition, current_streak, rank_streaks

TODAY = "2026-06-17"
YESTERDAY = "2026-06-16"


def transition(data, domain="vocab", other_due=0):
    return streak_transition(data, domain, TODAY, YESTERDAY, other_due)


# ── streak_transition ─────────────────────────────────────────────────────────

def test_first_ever_completion_starts_streak_at_one():
    updates, result = transition({}, "vocab", other_due=0)
    assert result == {"both_done": True, "streak": 1, "advanced": True}
    assert updates["vocab_cleared_date"] == TODAY
    assert updates["last_streak_date"] == TODAY
    assert updates["streak_count"] == 1


def test_other_domain_with_due_cards_blocks_advance():
    # Vocab cleared but grammar still has 5 due and wasn't cleared today.
    updates, result = transition({}, "vocab", other_due=5)
    assert result["both_done"] is False
    assert result["advanced"] is False
    assert "last_streak_date" not in updates
    assert updates["vocab_cleared_date"] == TODAY  # still recorded


def test_other_domain_cleared_today_completes_day():
    data = {"grammar_cleared_date": TODAY, "streak_count": 3, "last_streak_date": YESTERDAY}
    updates, result = transition(data, "vocab", other_due=99)  # due ignored, already cleared
    assert result["both_done"] is True
    assert result["streak"] == 4
    assert result["advanced"] is True


def test_consecutive_day_increments():
    data = {"streak_count": 7, "last_streak_date": YESTERDAY}
    _, result = transition(data, "vocab", other_due=0)
    assert result["streak"] == 8


def test_gap_resets_streak_to_one():
    data = {"streak_count": 7, "last_streak_date": "2026-06-10"}  # long ago
    _, result = transition(data, "vocab", other_due=0)
    assert result["streak"] == 1


def test_second_clear_same_day_does_not_double_count():
    data = {"streak_count": 5, "last_streak_date": TODAY, "vocab_cleared_date": TODAY}
    updates, result = transition(data, "grammar", other_due=0)
    assert result["advanced"] is False
    assert result["streak"] == 5
    assert "streak_count" not in updates  # no re-write of the count


# ── current_streak (display) ──────────────────────────────────────────────────

def test_current_streak_shows_when_advanced_today():
    assert current_streak({"streak_count": 4, "last_streak_date": TODAY}, TODAY, YESTERDAY) == 4


def test_current_streak_shows_when_advanced_yesterday():
    assert current_streak({"streak_count": 4, "last_streak_date": YESTERDAY}, TODAY, YESTERDAY) == 4


def test_current_streak_lapsed_returns_zero():
    assert current_streak({"streak_count": 9, "last_streak_date": "2026-06-01"}, TODAY, YESTERDAY) == 0


def test_current_streak_empty_doc_is_zero():
    assert current_streak({}, TODAY, YESTERDAY) == 0


# ── rank_streaks (leaderboard) ────────────────────────────────────────────────

def _user(uid, name, count, last):
    return {"user_id": uid, "username": name, "streak_count": count, "last_streak_date": last}


def test_leaderboard_orders_by_streak_desc():
    users = [
        _user(1, "anna", 3, TODAY),
        _user(2, "ben", 9, YESTERDAY),
        _user(3, "cara", 5, TODAY),
    ]
    board = rank_streaks(users, TODAY, YESTERDAY)
    assert [e["username"] for e in board] == ["ben", "cara", "anna"]
    assert board[0]["streak"] == 9


def test_leaderboard_excludes_lapsed_streaks():
    users = [
        _user(1, "anna", 12, "2026-06-01"),  # lapsed → excluded
        _user(2, "ben", 4, TODAY),
    ]
    board = rank_streaks(users, TODAY, YESTERDAY)
    assert [e["username"] for e in board] == ["ben"]


def test_leaderboard_ties_broken_by_username():
    users = [_user(1, "zoe", 5, TODAY), _user(2, "amy", 5, TODAY)]
    board = rank_streaks(users, TODAY, YESTERDAY)
    assert [e["username"] for e in board] == ["amy", "zoe"]


def test_leaderboard_respects_limit():
    users = [_user(i, f"u{i}", i + 1, TODAY) for i in range(20)]
    assert len(rank_streaks(users, TODAY, YESTERDAY, limit=10)) == 10


def test_leaderboard_empty_when_no_active_streaks():
    assert rank_streaks([_user(1, "anna", 3, "2026-01-01")], TODAY, YESTERDAY) == []
