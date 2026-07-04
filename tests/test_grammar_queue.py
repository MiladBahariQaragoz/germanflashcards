"""Unified session: vocab and grammar share ONE in-memory queue per user.

The review pool is combined across both domains, so `get_grammar_session` and
`get_session` return the same SessionQueue instance and the grammar reset aliases
the single reset. (Previously the two domains had independent registries.)
"""

from bot.queue_manager import (
    get_session,
    get_grammar_session,
    reset_all_sessions,
    reset_all_grammar_sessions,
    drop_user,
    SessionQueue,
)

CARD_A = {"_id": "aaa", "word": "kommen", "translation": "to come"}
CARD_B = {"_id": "bbb", "word": "kaufen", "translation": "to buy"}


def test_get_grammar_session_returns_session_queue():
    s = get_grammar_session(1001)
    assert isinstance(s, SessionQueue)


def test_grammar_session_is_same_instance_as_vocab_session():
    assert get_session(2001) is get_grammar_session(2001)


def test_grammar_session_shares_the_unified_queue():
    vocab_session = get_session(2101)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])
    # Same user's grammar accessor sees the same cards — one shared pool.
    assert get_grammar_session(2101).remaining_count() == 1


def test_reset_all_grammar_sessions_resets_the_unified_session():
    session = get_session(3001)
    session.build(due_cards=[CARD_A, CARD_B], new_cards=[])

    reset_all_grammar_sessions()

    assert session.remaining_count() == 0


def test_reset_all_sessions_resets_the_unified_session():
    session = get_grammar_session(4001)
    session.build(due_cards=[CARD_A, CARD_B], new_cards=[])

    reset_all_sessions()

    assert session.remaining_count() == 0


def test_grammar_session_same_user_returns_same_instance():
    s1 = get_grammar_session(5001)
    s2 = get_grammar_session(5001)
    assert s1 is s2


def test_drop_user_clears_the_unified_session():
    session = get_session(6001)
    session.build(due_cards=[CARD_A], new_cards=[])
    drop_user(6001)
    # A fresh accessor after drop is a new, empty session.
    assert get_session(6001).remaining_count() == 0
    assert get_session(6001) is not session
