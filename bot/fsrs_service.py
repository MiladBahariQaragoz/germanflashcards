from datetime import datetime, timezone
from fsrs import Card, Rating, Scheduler, State

_scheduler = Scheduler()

_STATE_NAMES = {
    State.Learning: "Learning",
    State.Review: "Review",
    State.Relearning: "Relearning",
}


def _dict_to_card(card_dict: dict) -> Card:
    due = card_dict.get("due_date") or datetime.now(timezone.utc)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)

    last_review = card_dict.get("last_review")
    if last_review is not None and last_review.tzinfo is None:
        last_review = last_review.replace(tzinfo=timezone.utc)

    return Card(
        state=State(card_dict.get("state", State.Learning.value)),
        step=card_dict.get("step", 0),
        stability=card_dict.get("stability"),
        difficulty=card_dict.get("difficulty"),
        due=due,
        last_review=last_review,
    )


def _card_to_update_dict(card: Card) -> dict:
    return {
        "due_date": card.due,
        "state": card.state.value,
        "step": card.step,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "last_review": card.last_review,
        "fsrs_state": _STATE_NAMES[card.state],
    }


def rate_card(card_dict: dict, rating_int: int) -> tuple[dict, str]:
    """Apply a rating. Returns (mongo_update_fields, interval_label)."""
    card = _dict_to_card(card_dict)
    now = datetime.now(timezone.utc)
    updated_card, _ = _scheduler.review_card(card, Rating(rating_int), review_datetime=now)
    label = format_interval_from_due(updated_card.due, now)
    return _card_to_update_dict(updated_card), label


def preview_intervals(card_dict: dict) -> dict[int, str]:
    """Return interval labels for all 4 ratings without mutating the stored card."""
    card = _dict_to_card(card_dict)
    now = datetime.now(timezone.utc)
    result = {}
    for rating in Rating:
        updated_card, _ = _scheduler.review_card(card, rating, review_datetime=now)
        result[rating.value] = format_interval_from_due(updated_card.due, now)
    return result


def format_interval_from_due(due: datetime, now: datetime) -> str:
    delta_seconds = max(0, (due - now).total_seconds())
    if delta_seconds < 3600:
        return f"{max(1, round(delta_seconds / 60))}m"
    if delta_seconds < 86400:
        return f"{round(delta_seconds / 3600)}h"
    return f"{round(delta_seconds / 86400)}d"
