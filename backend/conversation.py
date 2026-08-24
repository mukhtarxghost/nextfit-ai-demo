from typing import List, Literal, Optional

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

    last_question: Optional[str] = None