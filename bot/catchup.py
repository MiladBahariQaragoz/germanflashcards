"""
Pure backlog-spreading math — no I/O, so it's unit-testable.

When a returning user has a large pile of due cards, "spreading" keeps the first
`per_day` earliest-due cards due today and pushes the rest onto upcoming days in
chunks of `per_day`, so the backlog is paid down in daily installments instead of
landing as one demoralizing wall of cards. db.spread_backlog applies these offsets
to the actual Firestore progress docs.
"""


def plan_installments(due_count: int, per_day: int) -> list[int]:
    """
    Day-offset for each OVERFLOW card (those beyond the first `per_day`), in
    due-date order. Element 0 corresponds to the (per_day+1)-th earliest-due card.
    Offsets start at 1 (tomorrow): cards 1..per_day of the overflow → +1 day,
    the next per_day → +2 days, and so on. Returns [] when nothing overflows.
    """
    if per_day < 1:
        raise ValueError("per_day must be >= 1")
    overflow = max(0, due_count - per_day)
    return [1 + j // per_day for j in range(overflow)]
