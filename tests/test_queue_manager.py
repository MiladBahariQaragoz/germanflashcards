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
