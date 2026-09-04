from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from models import LeadProfile


class ConversationMessage(BaseModel):
    role: Literal[
        "system",
        "user",
        "assistant",
    ]

    content: str


class ConversationState(BaseModel):
    messages: List[ConversationMessage] = Field(
        default_factory=list
    )

    lead: LeadProfile = Field(
        default_factory=LeadProfile
    )

    conversation_complete: bool = False

    handoff_required: bool = False

    turn_count: int = 0

    # --------------------------------------------------------
    # Conversation phase
    # --------------------------------------------------------

    conversation_phase: Literal[
        "greeting",
        "discovery",
        "qualification",
        "action",
        "closing",
    ] = "greeting"

    # --------------------------------------------------------
    # Active intent tracking
    # --------------------------------------------------------

    active_intent: Literal[
        "membership",
        "personal_training",
        "trial",
        "class",
        "general_information",
        "existing_member",
        "callback",
        "other",
        "unknown",
    ] = "unknown"

    previous_intent: Optional[str] = None

    # --------------------------------------------------------
    # Conversation context
    # --------------------------------------------------------

    pending_topic: Optional[str] = None

    last_ai_response: Optional[str] = None

    last_user_answer: Optional[str] = None

    last_question_asked: Optional[str] = None

    # --------------------------------------------------------
    # Corrections / changes
    # --------------------------------------------------------

    corrections: List[str] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Conversation summary (rolling)
    # --------------------------------------------------------

    conversation_summary: Optional[str] = None

    # --------------------------------------------------------
    # Clarification tracking
    # --------------------------------------------------------

    clarification_requested: bool = False

    consecutive_clarifications: int = 0

    # --------------------------------------------------------
    # Customer history (loaded at call start, not persisted)
    # --------------------------------------------------------

    customer_history: Optional[Dict[str, Any]] = None
