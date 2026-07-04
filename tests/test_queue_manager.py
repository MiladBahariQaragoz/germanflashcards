from bot import queue_manager as qm
from bot.queue_manager import SessionQueue

CARD_A = {"_id": "aaa", "word": "Hund", "translation": "dog"}
CARD_B = {"_id": "bbb", "word": "Katze", "translation": "cat"}
CARD_C = {"_id": "ccc", "word": "Baum", "translation": "tree"}


def make_queue(due=None, new=None):
    q = SessionQueue()
    q.build(due_cards=due or [], new_cards=new or [])
    return q


def test_build_combines_due_then_new():
    q = make_queue(due=[CARD_A], new=[CARD_B])
    assert q.remaining_count() == 2


def test_pop_returns_cards_in_order():
    q = make_queue(due=[CARD_A, CARD_B])
    first = q.pop_next()
    assert first["_id"] in ("aaa", "bbb")
    assert q.remaining_count() == 1


def test_pop_returns_none_when_empty():
    q = make_queue()
    assert q.pop_next() is None


def test_again_pile_appended_after_main_queue():
    q = make_queue(due=[CARD_A])
    q.pop_next()
    q.add_to_again_pile(CARD_B)
    assert q.remaining_count() == 1
    second = q.pop_next()
    assert second["_id"] == "bbb"


def test_kill_switch_false_initially():
    q = make_queue(due=[CARD_A])
    assert q.kill_switch is False


def test_kill_switch_set_when_all_exhausted():
    q = make_queue(due=[CARD_A])
    q.pop_next()
    q.check_and_set_kill_switch()
    assert q.kill_switch is True


def test_reset_clears_state():
    q = make_queue(due=[CARD_A])
    q.kill_switch = True
    q.reset()
    assert q.remaining_count() == 0
    assert q.kill_switch is False
    assert q.active is False


def test_throttle_no_new_cards_when_due_exceeds_150():
    due_cards = [{"_id": str(i)} for i in range(151)]
    new_cards = [{"_id": "new_1"}]
    q = SessionQueue()
    q.build(due_cards=due_cards, new_cards=new_cards)
    assert q.remaining_count() == 151


def test_again_pile_replays_after_queue_exhausted():
    q = make_queue(due=[CARD_A, CARD_B])
    q.pop_next()
    q.pop_next()
    q.add_to_again_pile(CARD_C)
    assert q.remaining_count() == 1
    replayed = q.pop_next()
    assert replayed["_id"] == "ccc"
    assert q.remaining_count() == 0


# ── Progress bar tracking ─────────────────────────────────────────────────────

def test_progress_starts_at_zero():
    q = make_queue(due=[CARD_A, CARD_B])
    assert q.progress() == (0, 2)


def test_progress_increments_on_mark_reviewed():
    q = make_queue(due=[CARD_A, CARD_B])
    q.pop_next()
    q.mark_reviewed()
    assert q.progress() == (1, 2)


def test_progress_total_excludes_new_cards_when_throttled():
    due_cards = [{"_id": str(i)} for i in range(151)]
    q = SessionQueue()
    q.build(due_cards=due_cards, new_cards=[{"_id": "new_1"}])
    assert q.progress() == (0, 151)


def test_progress_completed_capped_at_total():
    q = make_queue(due=[CARD_A])
    q.mark_reviewed()
    q.mark_reviewed()  # Again-then-pass edge cases must never overflow the bar
    assert q.progress() == (1, 1)


def test_reset_clears_progress():
    q = make_queue(due=[CARD_A])
    q.mark_reviewed()
    q.reset()
    assert q.progress() == (0, 0)


def test_drop_user_forgets_the_unified_session():
    uid = 99999
    qm.get_session(uid)
    # Grammar accessor is an alias — the same single session per user.
    assert qm.get_grammar_session(uid) is qm.get_session(uid)
    assert uid in qm._sessions
    qm.drop_user(uid)
    assert uid not in qm._sessions
    # Idempotent — dropping an unknown user doesn't raise.
    qm.drop_user(uid)
