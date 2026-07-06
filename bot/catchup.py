"""
Pure backlog-spreading math — no I/O, so it's unit-testable.

When a returning user has a large pile of due cards, "spreading" keeps the first
`today_max` earliest-due cards due today and pushes the rest onto upcoming days in
chunks of `next_day_max`, so the backlog is paid down in daily installments instead
of landing as one demoralizing wall of cards. Today's cap is larger than the
following days' (e.g. 60 today, 40/day after) so that the per-day review load on
those later days leaves room for the ~20 brand-new cards introduced each day,
keeping the total daily workload around 60. db.spread_backlog applies these offsets
to the actual Firestore progress docs.
"""


def plan_installments(
    due_count: int, today_max: int, next_day_max: int | None = None
) -> list[int]:
    """
    Day-offset for each OVERFLOW card (those beyond the first `today_max`), in
    due-date order. Element 0 corresponds to the (today_max+1)-th earliest-due card.
    Offsets start at 1 (tomorrow): cards 1..next_day_max of the overflow → +1 day,
    the next next_day_max → +2 days, and so on. `next_day_max` defaults to
    `today_max`. Returns [] when nothing overflows.
    """
    if today_max < 1:
        raise ValueError("today_max must be >= 1")
    if next_day_max is None:
        next_day_max = today_max
    if next_day_max < 1:
        raise ValueError("next_day_max must be >= 1")
    overflow = max(0, due_count - today_max)
    return [1 + j // next_day_max for j in range(overflow)]


def cap_daily_due(due_cards: list[dict], cap: int) -> list[dict]:
    """
    The at-most-`cap` earliest-due cards to serve in one session, oldest first.

    A session serves only this slice of the due backlog; the rest stay due and
    surface in later sessions. This bounds each session at `cap` review cards no
    matter how large the backlog is — so a 200-card pile never lands as one wall.
    It replaces relying on due_session offsets alone, which collapse because every
    session start advances the session counter and re-exposes the next installment.
    """
    return sorted(due_cards, key=lambda c: c.get("due_session", 0))[:cap]


def new_cards_allowed(combined_due: int, daily_cap: int) -> bool:
    """
    Whether to introduce new cards this session. New cards pause while a review
    backlog exists — they're only added when the user's COMBINED (vocab + grammar)
    due count is at or below the steady-state daily cap. Above that the user is
    catching up, so no new cards land until they're back under the cap.
    """
    return combined_due <= daily_cap
