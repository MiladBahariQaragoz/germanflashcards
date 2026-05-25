import random


class SessionQueue:
    """In-memory study queue for a single user."""

    def __init__(self):
        self.queue: list[dict] = []
        self.again_pile: list[dict] = []
        self.active: bool = False
        self.kill_switch: bool = False

    def build(self, due_cards: list[dict], new_cards: list[dict]) -> None:
        shuffled = list(due_cards)
        random.shuffle(shuffled)
        if len(due_cards) <= 150:
            shuffled.extend(new_cards)
        self.queue = shuffled
        self.again_pile = []
        self.active = True
        self.kill_switch = False

    def pop_next(self) -> dict | None:
        if self.queue:
            return self.queue.pop(0)
        if self.again_pile:
            self.queue = list(self.again_pile)
            self.again_pile = []
            return self.queue.pop(0)
        return None

    def add_to_again_pile(self, card: dict) -> None:
        self.again_pile.append(card)

    def remaining_count(self) -> int:
        return len(self.queue) + len(self.again_pile)

    def check_and_set_kill_switch(self) -> bool:
        if len(self.queue) == 0 and len(self.again_pile) == 0:
            self.kill_switch = True
            return True
        return False

    def reset(self) -> None:
        self.queue = []
        self.again_pile = []
        self.active = False
        self.kill_switch = False


# Per-user session registry.
# Keys are user_id (int), values are SessionQueue instances.
_sessions: dict[int, SessionQueue] = {}


def get_session(user_id: int) -> SessionQueue:
    """Return the SessionQueue for this user, creating one if it doesn't exist."""
    if user_id not in _sessions:
        _sessions[user_id] = SessionQueue()
    return _sessions[user_id]


def reset_all_sessions() -> None:
    """Reset every active session (called by the morning scheduler)."""
    for s in _sessions.values():
        s.reset()
