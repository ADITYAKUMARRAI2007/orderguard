"""Conversation continuation state must survive a process restart, and the
sticky "an image was attached somewhere in this thread" flag must persist
independently of any single turn's own image -- both real, live-found gaps
(FAILURE_LOG.md F-039, F-042).
"""

from orderguard.agent.conversation_sessions import (
    conversation_sessions_engine, ever_attempted_connector_ids, ever_failed_connector_ids,
    load_conversation_session, save_conversation_session, was_image_ever_attached,
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


def test_attempted_connector_ids_accumulate_across_turns_instead_of_replacing():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md F-044): turn 1
    really searches shopify only; turn 2 really searches swiggy-instamart
    only. The thread's cumulative "ever attempted" set must contain BOTH,
    not just whichever turn saved last -- this is what lets a later turn
    correctly identify an eligible connector as already-verified instead
    of nudging about it forever."""
    engine = conversation_sessions_engine(":memory:")
    save_conversation_session(
        engine, "session-1", "COMMERCE_GENERAL", {"resume": "a"}, attempted_connector_ids=["shopify"],
    )
    assert ever_attempted_connector_ids(engine, "session-1", "COMMERCE_GENERAL") == frozenset({"shopify"})

    save_conversation_session(
        engine, "session-1", "COMMERCE_GENERAL", {"resume": "b"},
        attempted_connector_ids=["swiggy-instamart"],
    )
    assert ever_attempted_connector_ids(engine, "session-1", "COMMERCE_GENERAL") == frozenset(
        {"shopify", "swiggy-instamart"}
    )


def test_no_saved_session_has_no_attempted_connectors():
    engine = conversation_sessions_engine(":memory:")
    assert ever_attempted_connector_ids(engine, "never-seen", "COMMERCE_GENERAL") == frozenset()


def test_failed_connector_ids_are_sticky_and_separate_from_attempted():
    """Real, live-found gap (2026-09-04, see FAILURE_LOG.md F-044's
    addendum): a connector's MCP handshake failing (real evidence from the
    SDK's own init message) is a DIFFERENT fact from "never attempted" --
    conflating them would make the unverified-connector nudge keep asking
    the model to retry something already confirmed broken."""
    engine = conversation_sessions_engine(":memory:")
    save_conversation_session(
        engine, "session-1", "COMMERCE_GENERAL", {"resume": "a"},
        failed_connector_ids=["swiggy-instamart"],
    )
    assert ever_failed_connector_ids(engine, "session-1", "COMMERCE_GENERAL") == frozenset({"swiggy-instamart"})
    assert ever_attempted_connector_ids(engine, "session-1", "COMMERCE_GENERAL") == frozenset()

    save_conversation_session(
        engine, "session-1", "COMMERCE_GENERAL", {"resume": "b"}, failed_connector_ids=["github"],
    )
    assert ever_failed_connector_ids(engine, "session-1", "COMMERCE_GENERAL") == frozenset(
        {"swiggy-instamart", "github"}
    )


def test_no_saved_session_has_no_failed_connectors():
    engine = conversation_sessions_engine(":memory:")
    assert ever_failed_connector_ids(engine, "never-seen", "COMMERCE_GENERAL") == frozenset()
