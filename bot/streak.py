"""
Pure streak-state transitions — no I/O, so the logic is unit-testable.

A streak day requires BOTH the grammar and vocab sessions to be satisfied on the
same Berlin calendar date. A domain is "satisfied" when its session was cleared
today OR it had nothing due that day. Firestore persistence and the Berlin-date
clock live in db.py (record_session_cleared / get_streak); this module only does
the date-string arithmetic on a plain user-doc dict.
"""


def streak_transition(
    data: dict, domain: str, today: str, yesterday: str, other_domain_due: int
) -> tuple[dict, dict]:
    """
    `domain` ('vocab'|'grammar') was just cleared today. Given the current user
    doc `data`, return (firestore_updates, result) where result is
    {'both_done': bool, 'streak': int, 'advanced': bool}.

    The other domain counts as done if it was already cleared today or has 0 due.
    The streak only advances once per day, and resets to 1 if the previous advance
    wasn't yesterday.
    """
    other = "vocab" if domain == "grammar" else "grammar"
    other_done = data.get(f"{other}_cleared_date") == today or other_domain_due == 0

    updates = {f"{domain}_cleared_date": today}
    streak = data.get("streak_count", 0)
    advanced = False

    if other_done and data.get("last_streak_date") != today:
        streak = streak + 1 if data.get("last_streak_date") == yesterday else 1
        updates["last_streak_date"] = today
        updates["streak_count"] = streak
        advanced = True

    return updates, {"both_done": other_done, "streak": streak, "advanced": advanced}


def current_streak(data: dict, today: str, yesterday: str) -> int:
    """Streak to display: 0 if lapsed (last advance older than yesterday)."""
    if data.get("last_streak_date") in (today, yesterday):
        return data.get("streak_count", 0)
    return 0


def rank_streaks(
    users: list[dict], today: str, yesterday: str, limit: int = 10
) -> list[dict]:
    """
    Build the leaderboard from user-doc dicts: only currently-active streaks
    (lapsed ones are excluded), highest first, ties broken by username for a
    stable order. Returns [{'user_id', 'username', 'streak'}, ...] up to `limit`.
    """
    entries = []
    for u in users:
        streak = current_streak(u, today, yesterday)
        if streak <= 0:
            continue
        entries.append({
            "user_id": u.get("user_id"),
            "username": u.get("username") or "",
            "streak": streak,
        })
    entries.sort(key=lambda e: (-e["streak"], e["username"].lower()))
    return entries[:limit]
