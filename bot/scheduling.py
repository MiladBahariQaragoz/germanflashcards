"""
Pure session-scheduling math — no I/O, so it's unit-testable.

The bot schedules cards on a *session* axis instead of a calendar axis: a card FSRS
would space "5 days" out reappears 5 *sessions* later, where a session is any study
round the user starts. FSRS still computes intervals as real time deltas (it needs
elapsed time for its stability model); this module converts that delta into a whole
number of sessions to wait, and offsets it by the user's running session counter.

Sub-day FSRS intervals (the 1m/10m learning steps) map to 0 sessions so the card is
reshown within the same session via the queue's again-pile.
"""

from datetime import datetime


def interval_to_sessions(due: datetime, now: datetime) -> int:
    """FSRS interval (due - now) → number of sessions to wait.

    < 1 day → 0 (reshow same session); otherwise round(days). A due date in the
    past yields 0 (never negative).
    """
    days = (due - now).total_seconds() / 86400
    if days < 1:
        return 0
    return round(days)


def due_session_for(current_counter: int, interval_sessions: int) -> int:
    """Session number at which a just-rated card becomes due again."""
    return current_counter + interval_sessions
