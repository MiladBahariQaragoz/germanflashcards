"""Vocab and grammar have SEPARATE in-memory queues per user.

Each domain is its own study session: opening /vocab must not disturb a grammar
session in progress and vice versa. (An earlier design unified them into one
shared queue; that was reverted so per-domain counts stay independent.)
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


def test_grammar_session_is_a_different_instance_from_vocab_session():
    assert get_session(2001) is not get_grammar_session(2001)


def test_vocab_and_grammar_queues_are_independent():
    vocab_session = get_session(2101)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])
    # The grammar accessor for the same user is a separate, empty queue.
    assert get_grammar_session(2101).remaining_count() == 0


def test_reset_all_grammar_sessions_leaves_vocab_untouched():
    vocab = get_session(3001)
    grammar = get_grammar_session(3001)
    vocab.build(due_cards=[CARD_A, CARD_B], new_cards=[])
    grammar.build(due_cards=[CARD_A], new_cards=[])

    reset_all_grammar_sessions()

    assert grammar.remaining_count() == 0
    assert vocab.remaining_count() == 2


def test_reset_all_sessions_leaves_grammar_untouched():
    vocab = get_session(4001)
    grammar = get_grammar_session(4001)
    vocab.build(due_cards=[CARD_A], new_cards=[])
    grammar.build(due_cards=[CARD_A, CARD_B], new_cards=[])

    reset_all_sessions()

    assert vocab.remaining_count() == 0
    assert grammar.remaining_count() == 2


def test_grammar_session_same_user_returns_same_instance():
    s1 = get_grammar_session(5001)
    s2 = get_grammar_session(5001)
    assert s1 is s2


def test_drop_user_clears_both_domain_sessions():
    vocab = get_session(6001)
    grammar = get_grammar_session(6001)
    vocab.build(due_cards=[CARD_A], new_cards=[])
    grammar.build(due_cards=[CARD_B], new_cards=[])
    drop_user(6001)
    assert get_session(6001).remaining_count() == 0
    assert get_grammar_session(6001).remaining_count() == 0
    assert get_session(6001) is not vocab
    assert get_grammar_session(6001) is not grammar
