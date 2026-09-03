"""Conversation continuation state must survive a process restart, and the
sticky "an image was attached somewhere in this thread" flag must persist
independently of any single turn's own image -- both real, live-found gaps
(FAILURE_LOG.md F-039, F-042).
"""

from orderguard.agent.conversation_sessions import (
    conversation_sessions_engine, load_conversation_session, save_conversation_session,
    was_image_ever_attached,
)


def test_session_context_survives_a_fresh_engine_against_the_same_file(tmp_path):
    """Two separate Engine objects pointed at the same SQLite file stand in
    for "the process restarted" -- nothing here is held in Python memory
    between the two engines."""
    db_path = tmp_path / "conversation_sessions.db"
    write_engine = conversation_sessions_engine(db_path)
    save_conversation_session(write_engine, "session-1", "COMMERCE_GROCERY", {"resume": "sdk-abc"})

    read_engine = conversation_sessions_engine(db_path)
    assert load_conversation_session(read_engine, "session-1", "COMMERCE_GROCERY") == {"resume": "sdk-abc"}


def test_no_saved_session_returns_none():
    engine = conversation_sessions_engine(":memory:")
    assert load_conversation_session(engine, "never-seen", "COMMERCE_GROCERY") is None


def test_image_attached_flag_is_sticky_across_later_saves_without_an_image():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md F-042): turn 1
    attaches an image and widens eligibility; turn 2 replies with plain
    text and has no image of its own. The flag must still read True on
    turn 2, or the connector set an image established silently narrows
    back down mid-conversation."""
    engine = conversation_sessions_engine(":memory:")
    save_conversation_session(engine, "session-1", "COMMERCE_GENERAL", {"resume": "a"}, image_attached=True)
    assert was_image_ever_attached(engine, "session-1", "COMMERCE_GENERAL") is True

    save_conversation_session(engine, "session-1", "COMMERCE_GENERAL", {"resume": "b"}, image_attached=False)
    assert was_image_ever_attached(engine, "session-1", "COMMERCE_GENERAL") is True


def test_image_attached_flag_defaults_false_and_stays_false_without_one():
    engine = conversation_sessions_engine(":memory:")
    save_conversation_session(engine, "session-1", "COMMERCE_GENERAL", {"resume": "a"})
    assert was_image_ever_attached(engine, "session-1", "COMMERCE_GENERAL") is False


def test_image_attached_flag_is_scoped_to_its_own_session_and_category():
    engine = conversation_sessions_engine(":memory:")
    save_conversation_session(engine, "session-1", "COMMERCE_GENERAL", {"resume": "a"}, image_attached=True)
    assert was_image_ever_attached(engine, "session-2", "COMMERCE_GENERAL") is False
    assert was_image_ever_attached(engine, "session-1", "COMMERCE_GROCERY") is False
