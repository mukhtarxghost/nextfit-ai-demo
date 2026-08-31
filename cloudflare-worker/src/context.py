"""
Context management for the NextFit AI Receptionist.

Handles:
- Conversation context building for LLM injection
- Correction detection and processing
- Duplicate question prevention
- Clarification / repeat detection
- Phase transitions
"""

import re
from typing import Optional

from conversation import ConversationMessage, ConversationState
from models import LeadProfile


# ============================================================
# PHASE TRANSITIONS
# ============================================================

def update_conversation_phase(
    state: ConversationState,
) -> None:
    """Update conversation phase based on current state."""

    lead = state.lead
    phase = state.conversation_phase

    if phase == "closing":
        return

    if state.conversation_complete or state.handoff_required:
        state.conversation_phase = "closing"
        return

    if (
        lead.next_step_intent in {"accepted", "interested"}
        and lead.name
        and lead.phone_number
    ):
        state.conversation_phase = "closing"
        return

    if (
        lead.next_step_intent in {"accepted", "interested"}
        or (lead.name and lead.phone_number)
    ):
        state.conversation_phase = "action"
        return

    has_qualification = bool(
        lead.goal
        or lead.current_situation
        or lead.problem
        or lead.experience != "unknown"
        or lead.location
        or lead.timeline != "unknown"
    )

    if has_qualification:
        state.conversation_phase = "qualification"
        return

    has_discovery = bool(
        lead.intent
        or lead.training_preference != "unknown"
        or lead.goal
    )

    if has_discovery:
        state.conversation_phase = "discovery"
        return

    if state.turn_count >= 2:
        state.conversation_phase = "discovery"


# ============================================================
# ACTIVE INTENT DETECTION
# ============================================================

INTENT_KEYWORDS = {
    "membership": [
        "membership", "member", "join", "joining",
        "gym access", "regular gym", "monthly",
    ],
    "personal_training": [
        "personal training", "personal trainer",
        "one-on-one", "one on one", "trainer",
        "coaching", "guided", "pt session",
    ],
    "trial": [
        "trial", "try out", "test", "free session",
        "demo class", "try a session",
    ],
    "class": [
        "class", "classes", "yoga", "zumba",
        "group", "crossfit", "hiit",
    ],
    "callback": [
        "call me", "call back", "callback",
        "someone call", "give me a call",
        "reach me", "contact me",
    ],
    "existing_member": [
        "already a member", "existing member",
        "i'm a member", "i am a member",
        "my membership", "renew", "renewal",
    ],
}


def detect_active_intent(
    message: str,
    current_intent: str,
) -> Optional[str]:
    """Detect active intent from a user message.

    Returns new intent if detected, None if no change.
    Does not override with a weaker signal.
    """

    text = message.lower()

    correction_signals = [
        "actually", "wait", "no no", "i meant",
        "make that", "instead", "forget",
        "change to", "switch to",
    ]

    is_correction = any(
        signal in text for signal in correction_signals
    )

    # On corrections, find the LAST matching intent
    # (the one the caller is switching TO)
    if is_correction:
        last_match = None
        for intent, keywords in INTENT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    last_match = intent
        return last_match

    for intent, keywords in INTENT_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                if current_intent == "unknown":
                    return intent
                if intent != current_intent:
                    return intent

    return None


# ============================================================
# CORRECTION DETECTION
# ============================================================

CORRECTION_PATTERNS = [
    r"\bactually\b",
    r"\bmake that\b",
    r"\bi meant\b",
    r"\bwait\b",
    r"\bno[,.]?\s*no\b",
    r"\binstead\b",
    r"\bforget\b",
    r"\bchange to\b",
    r"\bswitch to\b",
    r"\bcorrection\b",
    r"\bnot .+ but\b",
]


def is_correction(message: str) -> bool:
    """Detect if the user message is a correction."""

    text = message.lower().strip()

    for pattern in CORRECTION_PATTERNS:
        if re.search(pattern, text):
            return True

    return False


def process_correction(
    state: ConversationState,
    message: str,
) -> None:
    """Process a correction and update state accordingly."""

    text = message.lower().strip()

    date_patterns = [
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(today|tomorrow|day after tomorrow)\b",
        r"\bnext\s+(week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    ]

    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            date_value = match.group(0).strip()
            state.lead.availability = date_value
            state.corrections.append(
                f"date changed to {date_value}"
            )
            return

    time_patterns = [
        r"\b(\d{1,2})\s*(am|pm|o'?clock)\b",
        r"\b(morning|afternoon|evening|night)\b",
        r"\b(\d{1,2})\s*:\s*(\d{2})\b",
        r"\b(make that|change to)\s+(\d{1,2})\b",
        r"\b(\d{1,2})\s*$",
    ]

    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            time_value = match.group(0).strip()
            # Clean up prefix words
            time_value = re.sub(
                r"^(make that|change to|actually|wait)\s*",
                "",
                time_value,
            ).strip()
            state.lead.availability = time_value
            state.corrections.append(
                f"time changed to {time_value}"
            )
            return

    training_keywords = {
        "membership": "membership",
        "personal training": "personal_training",
        "pt": "personal_training",
        "trial": "trial",
    }

    for keyword, pref in training_keywords.items():
        if keyword in text:
            old_pref = state.lead.training_preference
            state.lead.training_preference = pref
            if old_pref != pref:
                state.previous_intent = state.active_intent
                state.active_intent = pref if pref != "personal_training" else "personal_training"
                state.corrections.append(
                    f"training preference changed from {old_pref} to {pref}"
                )
            return


# ============================================================
# CLARIFICATION DETECTION
# ============================================================

CLARIFICATION_PATTERNS = [
    r"\bwait[,.]?\s*what did you say\b",
    r"\bwhat did you say\b",
    r"\bcan you repeat\b",
    r"\brepeat that\b",
    r"\bsay that again\b",
    r"\bwhat\b(?!\s+(?:kind|type|time|area|goal))",
    r"\bhuh\b",
    r"\bpardon\b",
    r"\bsorry\b(?!.*(?:i|my|we))",
    r"\bno[,.]?\s*that'?s not what i meant\b",
    r"\bthat'?s not what i meant\b",
    r"\bi didn'?t mean\b",
    r"\bwhat do you mean\b",
    r"\bcould you clarify\b",
]


def is_clarification_request(message: str) -> bool:
    """Detect if the user is asking for clarification or repeating."""

    text = message.lower().strip()

    for pattern in CLARIFICATION_PATTERNS:
        if re.search(pattern, text):
            return True

    return False


def handle_clarification(
    state: ConversationState,
    message: str,
) -> Optional[str]:
    """Handle a clarification request.

    Returns a response if we can handle it locally,
    None if it should go to the LLM.
    """

    text = message.lower().strip()

    state.clarification_requested = True
    state.consecutive_clarifications += 1

    if state.consecutive_clarifications >= 3:
        state.consecutive_clarifications = 0
        return None

    repeat_patterns = [
        r"\bwhat did you say\b",
        r"\bcan you repeat\b",
        r"\brepeat that\b",
        r"\bsay that again\b",
    ]

    for pattern in repeat_patterns:
        if re.search(pattern, text):
            if state.last_ai_response:
                return state.last_ai_response

    return None


# ============================================================
# DUPLICATE QUESTION PREVENTION
# ============================================================

KNOWN_FIELD_DESCRIPTIONS = {
    "name": "caller's name",
    "goal": "caller's fitness goal",
    "experience": "caller's training experience",
    "location": "caller's location/area",
    "timeline": "caller's joining timeline",
    "training_preference": "caller's training preference",
    "availability": "caller's availability",
    "phone_number": "caller's phone number",
    "problem": "caller's main problem/obstacle",
    "current_situation": "caller's current training situation",
}


def get_known_information(
    lead: LeadProfile,
) -> dict[str, str]:
    """Return a dict of field -> known value for populated fields."""

    known = {}

    if lead.name:
        known["name"] = lead.name

    if lead.phone_number:
        known["phone_number"] = lead.phone_number

    if lead.goal:
        known["goal"] = lead.goal

    if lead.experience != "unknown":
        known["experience"] = lead.experience

    if lead.location:
        known["location"] = lead.location

    if lead.timeline != "unknown":
        known["timeline"] = lead.timeline

    if lead.training_preference != "unknown":
        known["training_preference"] = lead.training_preference

    if lead.availability:
        known["availability"] = lead.availability

    if lead.problem:
        known["problem"] = lead.problem

    if lead.current_situation:
        known["current_situation"] = lead.current_situation

    if lead.intent:
        known["intent"] = lead.intent

    return known


def build_known_info_text(lead: LeadProfile) -> str:
    """Build a text block of known information for the system prompt."""

    known = get_known_information(lead)

    if not known:
        return "KNOWN INFORMATION: None yet."

    lines = []
    for field, value in known.items():
        label = KNOWN_FIELD_DESCRIPTIONS.get(field, field)
        lines.append(f"- {label}: {value}")

    return (
        "KNOWN INFORMATION (do NOT ask about these again):\n"
        + "\n".join(lines)
    )


# ============================================================
# TOPIC INTERRUPT / RESUME
# ============================================================


def detect_topic_interrupt(
    state: ConversationState,
    message: str,
) -> bool:
    """Detect if the user is interrupting the current topic
    with a new question."""

    if not state.last_question_asked:
        return False

    interrupt_signals = [
        "before that",
        "before we continue",
        "wait",
        "hold on",
        "first",
        "quick question",
        "one thing",
        "by the way",
        "also",
    ]

    text = message.lower().strip()

    for signal in interrupt_signals:
        if text.startswith(signal):
            if state.pending_topic is None:
                state.pending_topic = (
                    state.last_question_asked
                )
            return True

    return False


def should_resume_topic(
    state: ConversationState,
) -> bool:
    """Determine if we should resume a pending topic."""

    if not state.pending_topic:
        return False

    if state.clarification_requested:
        return False

    return True


def clear_pending_topic(
    state: ConversationState,
) -> None:
    """Clear the pending topic after resuming."""

    state.pending_topic = None
    state.last_question_asked = None


# ============================================================
# CONTEXT BUILDER
# ============================================================


def build_conversation_context(
    state: ConversationState,
) -> str:
    """Build a lightweight context block for the system prompt.

    This replaces the need for full conversation history
    by providing structured state information.
    """

    sections = []

    sections.append(
        f"PHASE: {state.conversation_phase}"
    )

    if state.active_intent != "unknown":
        intent_line = f"ACTIVE INTENT: {state.active_intent}"
        if state.previous_intent:
            intent_line += (
                f" (was: {state.previous_intent})"
            )
        sections.append(intent_line)

    if state.corrections:
        recent = state.corrections[-3:]
        sections.append(
            "CORRECTIONS MADE: "
            + "; ".join(recent)
        )

    if state.pending_topic:
        sections.append(
            f"PENDING TOPIC TO RESUME: {state.pending_topic}"
        )

    known = get_known_information(state.lead)
    if known:
        known_parts = []
        for field, value in known.items():
            known_parts.append(f"{field}={value}")
        sections.append(
            "ALREADY KNOWN: "
            + ", ".join(known_parts)
        )

    if state.conversation_summary:
        sections.append(
            f"CONVERSATION SO FAR: {state.conversation_summary}"
        )

    return (
        "============================================================\n"
        "CONVERSATION CONTEXT\n"
        "============================================================\n"
        + "\n".join(sections)
        + "\n\n"
        "IMPORTANT: Use this context to avoid repeating questions, "
        "remember corrections, and maintain conversation flow. "
        "Do NOT expose this internal context to the caller."
    )


# ============================================================
# ROLLING SUMMARY
# ============================================================


def update_conversation_summary(
    state: ConversationState,
) -> None:
    """Update a lightweight rolling summary of the conversation.

    This is used when the conversation exceeds the message
    window so the LLM retains key context.
    """

    lead = state.lead
    parts = []

    if lead.name:
        parts.append(f"Caller is {lead.name}")

    if lead.intent:
        parts.append(f"interested in {lead.intent}")

    if lead.goal:
        parts.append(f"goal: {lead.goal}")

    if lead.experience != "unknown":
        parts.append(f"experience: {lead.experience}")

    if lead.location:
        parts.append(f"located in {lead.location}")

    if lead.training_preference != "unknown":
        parts.append(
            f"prefers {lead.training_preference}"
        )

    if lead.problem:
        parts.append(f"problem: {lead.problem}")

    if lead.timeline != "unknown":
        parts.append(f"timeline: {lead.timeline}")

    if lead.availability:
        parts.append(f"availability: {lead.availability}")

    if state.corrections:
        parts.append(
            f"made {len(state.corrections)} correction(s)"
        )

    if parts:
        state.conversation_summary = ". ".join(parts) + "."
    else:
        state.conversation_summary = None


# ============================================================
# MESSAGE SELECTION
# ============================================================

MAX_CONTEXT_MESSAGES = 10


def select_messages_for_llm(
    state: ConversationState,
) -> list[ConversationMessage]:
    """Select which messages to send to the LLM.

    Strategy:
    - Always include the last MAX_CONTEXT_MESSAGES messages
    - If conversation is longer, the summary compensates
    - Never drop the most recent 4 messages
    """

    messages = state.messages
    total = len(messages)

    if total <= MAX_CONTEXT_MESSAGES:
        return list(messages)

    return list(messages[-MAX_CONTEXT_MESSAGES:])
