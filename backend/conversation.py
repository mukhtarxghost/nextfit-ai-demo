from typing import List, Literal, Optional
from pydantic import BaseModel

from .models import LeadProfile


class ConversationMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ConversationState(BaseModel):
    messages: List[ConversationMessage] = []
    lead: LeadProfile = LeadProfile()
    conversation_complete: bool = False
    handoff_required: bool = False
    turn_count: int = 0
    last_question: Optional[str] = None