from bot.queue_manager import (
    get_session,
    get_grammar_session,
    reset_all_sessions,
    reset_all_grammar_sessions,
    SessionQueue,
)

CARD_A = {"_id": "aaa", "word": "kommen", "translation": "to come"}
CARD_B = {"_id": "bbb", "word": "kaufen", "translation": "to buy"}


def test_get_grammar_session_returns_session_queue():
    s = get_grammar_session(1001)
    assert isinstance(s, SessionQueue)


def test_grammar_session_isolated_from_vocab_session():
    vocab_session = get_session(2001)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])

    grammar_session = get_grammar_session(2001)
    assert grammar_session.remaining_count() == 0


def test_reset_all_grammar_sessions_does_not_affect_vocab():
    vocab_session = get_session(3001)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])

    grammar_session = get_grammar_session(3001)
    grammar_session.build(due_cards=[CARD_B], new_cards=[])

    reset_all_grammar_sessions()

    assert grammar_session.remaining_count() == 0
    assert vocab_session.remaining_count() == 1


def test_reset_all_sessions_does_not_affect_grammar():
    vocab_session = get_session(4001)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])

    grammar_session = get_grammar_session(4001)
    grammar_session.build(due_cards=[CARD_B], new_cards=[])

    reset_all_sessions()

    assert vocab_session.remaining_count() == 0
    assert grammar_session.remaining_count() == 1


def test_grammar_session_same_user_returns_same_instance():
    s1 = get_grammar_session(5001)
    s2 = get_grammar_session(5001)
    assert s1 is s2
