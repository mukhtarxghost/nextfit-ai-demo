"""
Tests for conversational state and context management.

Tests all specified scenarios:
A. "I want to join." -> natural discovery
B. Name + membership provided -> remembers
C. "Actually I'm mostly interested in personal training." -> changes intent
D. "Tomorrow evening" -> "Actually Saturday" -> final = Saturday
E. "6 PM" -> "Actually make that 7" -> final = 7 PM
F. "What time are you open?" -> safe fallback
G. "Wait, what did you say?" -> repeat last answer
H. "No no, that's not what I meant." -> clarify
I. "I'll think about it." -> not confirmed
J. "Can someone call me?" -> callback intent, not auto-handoff
K. Name/goal early, 8+ turns later -> still remembered
"""

import sys

from conversation import ConversationMessage, ConversationState
from context import (
    build_conversation_context,
    build_known_info_text,
    clear_pending_topic,
    detect_active_intent,
    detect_topic_interrupt,
    get_known_information,
    handle_clarification,
    is_clarification_request,
    is_correction,
    process_correction,
    select_messages_for_llm,
    should_resume_topic,
    update_conversation_phase,
    update_conversation_summary,
)
from models import LeadProfile


# ============================================================
# HELPERS
# ============================================================


def make_state(**kwargs) -> ConversationState:
    """Create a ConversationState with optional overrides."""

    lead = kwargs.pop("lead", None)
    state = ConversationState(**kwargs)
    if lead:
        state.lead = lead
    return state


def add_messages(
    state: ConversationState,
    exchanges: list[tuple[str, str]],
) -> None:
    """Add user/assistant message pairs."""

    for user_msg, ai_msg in exchanges:
        state.messages.append(
            ConversationMessage(
                role="user",
                content=user_msg,
            )
        )
        state.messages.append(
            ConversationMessage(
                role="assistant",
                content=ai_msg,
            )
        )
        state.turn_count += 1


# ============================================================
# TEST A: "I want to join." -> natural discovery
# ============================================================


def test_a_intent_detection_membership():
    """Detect membership intent from 'I want to join'."""

    intent = detect_active_intent(
        "I want to join the gym",
        "unknown",
    )

    assert intent == "membership", (
        f"Expected 'membership', got '{intent}'"
    )


def test_a_phase_transitions_to_discovery():
    """After expressing joining intent, phase should move to discovery."""

    state = make_state()
    state.turn_count = 1
    state.lead.intent = "want to join gym"

    update_conversation_phase(state)

    assert state.conversation_phase in {
        "discovery",
        "qualification",
    }, (
        f"Expected discovery or qualification, "
        f"got '{state.conversation_phase}'"
    )


# ============================================================
# TEST B: Name + membership provided -> remembers
# ============================================================


def test_b_remembers_name():
    """After name is provided, it should be in known info."""

    state = make_state()
    state.lead.name = "Rahul"
    state.lead.training_preference = "membership"

    known = get_known_information(state.lead)

    assert "name" in known
    assert known["name"] == "Rahul"
    assert "training_preference" in known
    assert known["training_preference"] == "membership"


def test_b_known_info_text_includes_name():
    """Known info text should mention Rahul."""

    state = make_state()
    state.lead.name = "Rahul"
    state.lead.training_preference = "membership"

    text = build_known_info_text(state.lead)

    assert "Rahul" in text
    assert "membership" in text
    assert "do NOT ask" in text


def test_b_context_block_includes_already_known():
    """Context block should include ALREADY KNOWN section."""

    state = make_state()
    state.lead.name = "Rahul"
    state.lead.goal = "lose fat"

    context = build_conversation_context(state)

    assert "ALREADY KNOWN" in context
    assert "Rahul" in context
    assert "lose fat" in context


# ============================================================
# TEST C: "Actually I'm mostly interested in personal
#          training." -> changes intent
# ============================================================


def test_c_correction_detected():
    """'Actually' should be detected as a correction."""

    assert is_correction("Actually I'm mostly interested in personal training")


def test_c_intent_changes_on_correction():
    """Correction with 'actually' should update intent."""

    state = make_state()
    state.active_intent = "membership"

    new_intent = detect_active_intent(
        "Actually I'm mostly interested in personal training",
        "membership",
    )

    assert new_intent == "personal_training", (
        f"Expected 'personal_training', got '{new_intent}'"
    )


def test_c_correction_updates_training_preference():
    """Processing correction should update training preference."""

    state = make_state()
    state.lead.training_preference = "membership"

    process_correction(
        state,
        "Actually I'm more interested in personal training",
    )

    assert state.lead.training_preference == "personal_training"
    assert len(state.corrections) > 0


# ============================================================
# TEST D: "Tomorrow evening" -> "Actually Saturday" -> Saturday
# ============================================================


def test_d_date_correction():
    """'Actually Saturday' should update availability to Saturday."""

    state = make_state()
    state.lead.availability = "tomorrow evening"

    process_correction(state, "Actually Saturday works better")

    assert state.lead.availability == "saturday", (
        f"Expected 'saturday', got '{state.lead.availability}'"
    )


def test_d_correction_recorded():
    """Correction should be recorded in state."""

    state = make_state()
    state.lead.availability = "tomorrow evening"

    process_correction(state, "Actually Saturday works better")

    assert any(
        "saturday" in c.lower()
        for c in state.corrections
    )


# ============================================================
# TEST E: "6 PM" -> "Actually make that 7" -> 7 PM
# ============================================================


def test_e_time_correction():
    """'Make that 7' should update availability to 7."""

    state = make_state()
    state.lead.availability = "6 pm"

    process_correction(state, "Actually make that 7")

    assert state.lead.availability == "7", (
        f"Expected '7', got '{state.lead.availability}'"
    )


def test_e_time_correction_pm():
    """'Make that 7 PM' should update to 7 PM."""

    state = make_state()
    state.lead.availability = "6 pm"

    process_correction(state, "Wait, make that 7 PM")

    assert "7" in state.lead.availability


# ============================================================
# TEST F: "What time are you open?" -> safe fallback
# ============================================================


def test_f_not_correction():
    """'What time are you open?' should NOT be a correction."""

    assert not is_correction("What time are you open?")


def test_f_not_clarification():
    """'What time are you open?' should NOT be a clarification."""

    assert not is_clarification_request("What time are you open?")


# ============================================================
# TEST G: "Wait, what did you say?" -> repeat last answer
# ============================================================


def test_g_clarification_detected():
    """'What did you say?' should be detected as clarification."""

    assert is_clarification_request("Wait, what did you say?")


def test_g_repeats_last_response():
    """Clarification handler should return last_ai_response."""

    state = make_state()
    state.last_ai_response = (
        "We're open from 6 AM to 10 PM every day."
    )

    response = handle_clarification(
        state,
        "What did you say?",
    )

    assert response == (
        "We're open from 6 AM to 10 PM every day."
    )


def test_g_can_you_repeat():
    """'Can you repeat that?' should also work."""

    state = make_state()
    state.last_ai_response = (
        "Personal training starts at 5000 per month."
    )

    response = handle_clarification(
        state,
        "Can you repeat that?",
    )

    assert response == (
        "Personal training starts at 5000 per month."
    )


def test_g_say_that_again():
    """'Say that again?' should also work."""

    state = make_state()
    state.last_ai_response = "Gotcha, Saturday at 7 PM."

    response = handle_clarification(
        state,
        "Say that again?",
    )

    assert response == "Gotcha, Saturday at 7 PM."


def test_g_no_last_response():
    """If no last_ai_response, should return None (go to LLM)."""

    state = make_state()
    state.last_ai_response = None

    response = handle_clarification(
        state,
        "What did you say?",
    )

    assert response is None


# ============================================================
# TEST H: "No no, that's not what I meant." -> clarify
# ============================================================


def test_h_clarification_detected():
    """'That's not what I meant' should be clarification."""

    assert is_clarification_request(
        "No no, that's not what I meant"
    )


def test_h_goes_to_llm():
    """'That's not what I meant' should return None (go to LLM)."""

    state = make_state()
    state.last_ai_response = "Some response"

    response = handle_clarification(
        state,
        "No no, that's not what I meant",
    )

    assert response is None


# ============================================================
# TEST I: "I'll think about it." -> not confirmed
# ============================================================


def test_i_not_correction():
    """'I'll think about it' should NOT be a correction."""

    assert not is_correction("I'll think about it")


def test_i_not_clarification():
    """'I'll think about it' should NOT be a clarification."""

    assert not is_clarification_request("I'll think about it")


def test_i_next_step_maybe():
    """When extracted, 'I'll think about it' should map to maybe."""

    from context import INTENT_KEYWORDS

    # It should NOT trigger any intent detection
    intent = detect_active_intent(
        "I'll think about it",
        "membership",
    )

    assert intent is None, (
        f"Expected None, got '{intent}'"
    )


# ============================================================
# TEST J: "Can someone call me?" -> callback intent
# ============================================================


def test_j_callback_intent():
    """'Can someone call me?' should detect callback intent."""

    intent = detect_active_intent(
        "Can someone call me?",
        "unknown",
    )

    assert intent == "callback", (
        f"Expected 'callback', got '{intent}'"
    )


def test_j_not_correction():
    """'Can someone call me?' should NOT be a correction."""

    assert not is_correction("Can someone call me?")


def test_j_not_auto_handoff():
    """Callback intent should not automatically set needs_human."""

    state = make_state()
    state.active_intent = "callback"
    state.lead.next_step_intent = "unknown"
    state.lead.name = None
    state.lead.phone_number = None

    update_conversation_phase(state)

    assert state.conversation_phase != "closing"
    assert not state.handoff_required


# ============================================================
# TEST K: Name/goal early, 8+ turns later -> still remembered
# ============================================================


def test_k_name_persists_after_many_turns():
    """Name should persist in lead profile regardless of turn count."""

    state = make_state()
    state.lead.name = "Rahul"
    state.lead.goal = "lose fat"
    state.lead.experience = "beginner"

    # Simulate 10 more turns with no name/goal mentions
    for i in range(10):
        state.messages.append(
            ConversationMessage(
                role="user",
                content=f"Turn {i + 1} message",
            )
        )
        state.messages.append(
            ConversationMessage(
                role="assistant",
                content=f"Response {i + 1}",
            )
        )
        state.turn_count += 1

    # Name and goal should still be in lead
    assert state.lead.name == "Rahul"
    assert state.lead.goal == "lose fat"
    assert state.lead.experience == "beginner"

    # Known info should still include them
    known = get_known_information(state.lead)
    assert known["name"] == "Rahul"
    assert known["goal"] == "lose fat"


def test_k_context_after_many_turns():
    """Context block should still show known info after 10+ turns."""

    state = make_state()
    state.lead.name = "Rahul"
    state.lead.goal = "lose fat"
    state.turn_count = 10

    context = build_conversation_context(state)

    assert "Rahul" in context
    assert "lose fat" in context


# ============================================================
# ADDITIONAL: Phase transition tests
# ============================================================


def test_phase_greeting_initial():
    """New conversation should start in greeting phase."""

    state = make_state()
    assert state.conversation_phase == "greeting"


def test_phase_discovery_after_intent():
    """After expressing intent, phase should move to discovery."""

    state = make_state()
    state.turn_count = 2
    state.lead.intent = "looking for gym"

    update_conversation_phase(state)
    assert state.conversation_phase == "discovery"


def test_phase_qualification_after_goal():
    """After providing goal, phase should move to qualification."""

    state = make_state()
    state.lead.goal = "lose fat"
    state.lead.experience = "beginner"

    update_conversation_phase(state)
    assert state.conversation_phase == "qualification"


def test_phase_action_after_handoff_agreement():
    """After agreeing to handoff, phase should move to action."""

    state = make_state()
    state.lead.next_step_intent = "accepted"
    state.lead.name = "Rahul"

    update_conversation_phase(state)
    assert state.conversation_phase == "action"


def test_phase_closing_after_complete():
    """After handoff complete, phase should be closing."""

    state = make_state()
    state.conversation_complete = True

    update_conversation_phase(state)
    assert state.conversation_phase == "closing"


# ============================================================
# ADDITIONAL: Message selection tests
# ============================================================


def test_select_messages_small_conversation():
    """Small conversation should return all messages."""

    state = make_state()
    state.messages = [
        ConversationMessage(role="user", content="Hi"),
        ConversationMessage(role="assistant", content="Hello"),
    ]

    selected = select_messages_for_llm(state)
    assert len(selected) == 2


def test_select_messages_large_conversation():
    """Large conversation should return last 10 messages."""

    state = make_state()

    for i in range(20):
        state.messages.append(
            ConversationMessage(
                role="user",
                content=f"Message {i}",
            )
        )
        state.messages.append(
            ConversationMessage(
                role="assistant",
                content=f"Response {i}",
            )
        )

    selected = select_messages_for_llm(state)
    assert len(selected) == 6

    # Should be the last 6
    assert selected[0].content == "Message 17"
    assert selected[-1].content == "Response 19"


# ============================================================
# ADDITIONAL: Summary tests
# ============================================================


def test_summary_with_info():
    """Summary should include known lead info."""

    state = make_state()
    state.lead.name = "Rahul"
    state.lead.goal = "lose fat"
    state.lead.location = "Camp"

    update_conversation_summary(state)

    assert state.conversation_summary is not None
    assert "Rahul" in state.conversation_summary
    assert "lose fat" in state.conversation_summary
    assert "Camp" in state.conversation_summary


def test_summary_empty():
    """Summary should be None when no info available."""

    state = make_state()
    update_conversation_summary(state)
    assert state.conversation_summary is None


# ============================================================
# ADDITIONAL: Topic interrupt tests
# ============================================================


def test_topic_interrupt_detected():
    """'Before that' should be detected as topic interrupt."""

    state = make_state()
    state.last_question_asked = "What kind of training?"

    result = detect_topic_interrupt(
        state,
        "Before that, what time do you open?",
    )

    assert result is True
    assert state.pending_topic == "What kind of training?"


def test_topic_interrupt_no_pending():
    """Without a pending question, interrupt should not trigger."""

    state = make_state()
    state.last_question_asked = None

    result = detect_topic_interrupt(
        state,
        "What time do you open?",
    )

    assert result is False


# ============================================================
# ADDITIONAL: Correction pattern tests
# ============================================================


def test_correction_actually():
    assert is_correction("Actually, Saturday.")


def test_correction_make_that():
    assert is_correction("Make that 7 PM")


def test_correction_wait():
    assert is_correction("Wait, 6 PM")


def test_correction_no_no():
    assert is_correction("No no, I meant personal training")


def test_correction_i_meant():
    assert is_correction("I meant tomorrow")


def test_correction_forget():
    assert is_correction("Forget the trial, I want membership")


def test_correction_switch():
    assert is_correction("Switch to personal training")


def test_not_correction_normal():
    assert not is_correction("I want to join")


def test_not_correction_question():
    assert not is_correction("What time do you open?")


def test_not_correction_membership():
    assert not is_correction("Tell me about memberships")


# ============================================================
# ADDITIONAL: Clarification pattern tests
# ============================================================


def test_clarification_wait_what():
    assert is_clarification_request("Wait, what did you say?")


def test_clarification_repeat():
    assert is_clarification_request("Can you repeat that?")


def test_clarification_sorry():
    assert is_clarification_request("Sorry?")


def test_clarification_not_what_i_meant():
    assert is_clarification_request(
        "No no, that's not what I meant"
    )


def test_clarification_huh():
    assert is_clarification_request("Huh?")


def test_not_clarification_normal():
    assert not is_clarification_request("I want to join")


def test_not_clarification_goal():
    assert not is_clarification_request(
        "I want to lose weight"
    )


# ============================================================
# ADDITIONAL: Intent detection tests
# ============================================================


def test_intent_membership():
    assert detect_active_intent("I want a membership", "unknown") == "membership"


def test_intent_pt():
    assert detect_active_intent("I need personal training", "unknown") == "personal_training"


def test_intent_trial():
    assert detect_active_intent("Do you have a trial?", "unknown") == "trial"


def test_intent_callback():
    assert detect_active_intent("Can someone call me?", "unknown") == "callback"


def test_intent_no_change():
    result = detect_active_intent("Hello", "membership")
    assert result is None


def test_intent_correction_overrides():
    """Correction should override current intent."""

    result = detect_active_intent(
        "Actually, forget membership. I want personal training.",
        "membership",
    )

    assert result == "personal_training"


# ============================================================
# REGRESSION: CLARIFICATION FALSE POSITIVES
# ============================================================


def test_clarification_not_triggered_by_what_about():
    """'what about membership' must NOT be treated as clarification."""
    assert not is_clarification_request("what about membership")


def test_clarification_not_triggered_by_what_are_prices():
    """'what are your prices' must NOT be treated as clarification."""
    assert not is_clarification_request("what are your prices")


def test_clarification_not_triggered_by_what_time():
    """'what time do you open' must NOT be treated as clarification."""
    assert not is_clarification_request("what time do you open")


def test_clarification_triggered_by_standalone_what():
    """Standalone 'what' IS a clarification request."""
    assert is_clarification_request("what")


def test_clarification_triggered_by_what_question_mark():
    """'what?' IS a clarification request."""
    assert is_clarification_request("what?")


def test_max_context_messages_is_bounded():
    """MAX_CONTEXT_MESSAGES must be 6 to keep Groq input bounded."""
    from context import MAX_CONTEXT_MESSAGES
    assert MAX_CONTEXT_MESSAGES == 6


# ============================================================
# RUN ALL TESTS
# ============================================================


def run_all_tests():
    """Run all tests and report results."""

    test_functions = [
        name
        for name in globals()
        if name.startswith("test_")
    ]

    passed = 0
    failed = 0
    errors = []

    for name in sorted(test_functions):
        func = globals()[name]
        try:
            func()
            passed += 1
        except AssertionError as e:
            failed += 1
            errors.append((name, str(e)))
        except Exception as e:
            failed += 1
            errors.append((name, f"EXCEPTION: {e}"))

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")

    if errors:
        print("\nFAILED TESTS:")
        for name, error in errors:
            print(f"  FAIL: {name}")
            print(f"        {error}")

    print()
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
