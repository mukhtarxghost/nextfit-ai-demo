import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from groq import Groq

from .conversation import ConversationState, ConversationMessage
from .models import LeadProfile
from .prompts import NEXTFIT_SYSTEM_PROMPT
from .qualification import calculate_qualification
from .nextfit_config import NEXTFIT_CONFIG


# --------------------------------------------------
# ENVIRONMENT
# --------------------------------------------------

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY is missing. Add it to your .env file."
    )

client = Groq(api_key=api_key)


# --------------------------------------------------
# FASTAPI APP
# --------------------------------------------------

app = FastAPI(
    title="Vantix NextFit AI Receptionist",
    description="Conversational AI receptionist and lead qualification system for NextFit.",
    version="0.1.0",
)


# --------------------------------------------------
# REQUEST / RESPONSE MODELS
# --------------------------------------------------

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    lead: LeadProfile
    score: int
    classification: str
    reasons: list[str]
    recommended_action: str


# --------------------------------------------------
# CONVERSATION STATE
# --------------------------------------------------

conversation = ConversationState()


# --------------------------------------------------
# SYSTEM PROMPT
# --------------------------------------------------

def build_system_prompt() -> str:
    business_info = f"""
BUSINESS CONFIGURATION

Business:
{NEXTFIT_CONFIG["business_name"]}

Location:
{NEXTFIT_CONFIG["location"]}

Services:
{", ".join(NEXTFIT_CONFIG["services"])}

Known Information:
{NEXTFIT_CONFIG["known_information"]}

Rules:
{NEXTFIT_CONFIG["rules"]}
"""

    return NEXTFIT_SYSTEM_PROMPT + "\n\n" + business_info


# --------------------------------------------------
# TEMPORARY LEAD EXTRACTION
# --------------------------------------------------

def get_current_lead() -> LeadProfile:
    """
    Temporary function.

    For now the conversation engine does not automatically
    extract lead information yet.

    We will connect structured Groq extraction next.
    """

    return conversation.lead


# --------------------------------------------------
# ROUTES
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Vantix NextFit AI Receptionist",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):

    if not request.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty.",
        )

    # Add customer message
    conversation.messages.append(
        ConversationMessage(
            role="user",
            content=request.message,
        )
    )

    conversation.turn_count += 1

    # --------------------------------------------------
    # BUILD GROQ MESSAGE HISTORY
    # --------------------------------------------------

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(),
        }
    ]

    for message in conversation.messages:
        messages.append(
            {
                "role": message.role,
                "content": message.content,
            }
        )

    # --------------------------------------------------
    # CALL GROQ
    # --------------------------------------------------

    completion = client.chat.completions.create(
    model="qwen/qwen3.6-27b",
    messages=messages,
    temperature=0.7,
    max_tokens=300,
    reasoning_effort="none",
    reasoning_format="hidden",
)

    response_text = completion.choices[0].message.content.strip()

    # --------------------------------------------------
    # STORE AI RESPONSE
    # --------------------------------------------------

    conversation.messages.append(
        ConversationMessage(
            role="assistant",
            content=response_text,
        )
    )

    # --------------------------------------------------
    # QUALIFICATION
    # --------------------------------------------------

    lead = get_current_lead()

    result = calculate_qualification(lead)

    return ChatResponse(
        response=response_text,
        lead=result.lead,
        score=result.score,
        classification=result.classification,
        reasons=result.reasons,
        recommended_action=result.recommended_action,
    )