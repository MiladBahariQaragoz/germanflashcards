import random


class SessionQueue:
    """In-memory study queue for a single user."""

    def __init__(self):
        self.queue: list[dict] = []
        self.again_pile: list[dict] = []
        self.active: bool = False
        self.kill_switch: bool = False
        # Progress tracking for the in-session completion bar.
        self.total: int = 0       # cards in the session at build time
        self.reviewed: int = 0    # cards given a passing grade (won't be replayed)

    def build(self, due_cards: list[dict], new_cards: list[dict]) -> None:
        shuffled = list(due_cards)
        random.shuffle(shuffled)
        if len(due_cards) <= 150:
            shuffled.extend(new_cards)
        self.queue = shuffled
        self.again_pile = []
        self.active = True
        self.kill_switch = False
        self.total = len(self.queue)
        self.reviewed = 0

    def mark_reviewed(self) -> None:
        """Count a card as completed (graded anything but Again)."""
        self.reviewed += 1

    def progress(self) -> tuple[int, int]:
        """(completed, total) for the completion bar. Completed is capped at total."""
        return min(self.reviewed, self.total), self.total

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
        self.total = 0
        self.reviewed = 0


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


# Grammar session registry — completely independent from vocab sessions.
_grammar_sessions: dict[int, SessionQueue] = {}


def get_grammar_session(user_id: int) -> SessionQueue:
    """Return the grammar SessionQueue for this user, creating one if it doesn't exist."""
    if user_id not in _grammar_sessions:
        _grammar_sessions[user_id] = SessionQueue()
    return _grammar_sessions[user_id]


def reset_all_grammar_sessions() -> None:
    """Reset every active grammar session (called by the morning scheduler)."""
    for s in _grammar_sessions.values():
        s.reset()


def drop_user(user_id: int) -> None:
    """Forget a user's in-memory sessions in both domains (used on admin removal)."""
    _sessions.pop(user_id, None)
    _grammar_sessions.pop(user_id, None)
