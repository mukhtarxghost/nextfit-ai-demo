import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel, ValidationError

from .conversation import (
    ConversationMessage,
    ConversationState,
)
from .models import LeadProfile
from .prompts import (
    LEAD_EXTRACTION_PROMPT,
    NEXTFIT_SYSTEM_PROMPT,
)
from .qualification import (
    calculate_qualification,
    get_missing_qualification_fields,
    get_qualification_status,
    is_fully_qualified,
)
from .nextfit_config import NEXTFIT_CONFIG


load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY is missing. Add it to your .env file."
    )

client = Groq(api_key=api_key)


app = FastAPI(
    title="Vantix NextFit AI Receptionist",
    description=(
        "Conversational AI receptionist and deterministic "
        "lead qualification system for NextFit."
    ),
    version="0.4.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    lead: LeadProfile
    score: int
    classification: str
    reasons: list[str]
    recommended_action: str


conversation = ConversationState()


def build_business_info() -> str:
    services = ", ".join(
        NEXTFIT_CONFIG["services"]
    )

    return f"""
BUSINESS CONFIGURATION

Business:
{NEXTFIT_CONFIG["business_name"]}

Primary Location:
{NEXTFIT_CONFIG["location"]}

Services:
{services}

Known Information:
{json.dumps(
    NEXTFIT_CONFIG["known_information"],
    indent=2,
)}

Business Rules:
{chr(10).join(
    "- " + rule
    for rule in NEXTFIT_CONFIG["rules"]
)}
"""


def build_qualification_status() -> str:
    lead = conversation.lead

    status = get_qualification_status(lead)

    labels = {
        "goal": "Goal",
        "current_situation": "Current situation",
        "experience": "Experience",
        "problem": "Main problem",
        "previous_attempts": "Previous attempts",
        "support_need": "Support/service need",
        "location": "Location",
        "timeline": "Joining timeline",
        "availability": "Availability",
    }

    lines = []

    for field, collected in status.items():
        state = (
            "COLLECTED"
            if collected
            else "MISSING"
        )

        lines.append(
            f"{labels[field]}: {state}"
        )

    missing = get_missing_qualification_fields(
        lead
    )

    next_priority = (
        missing[0]
        if missing
        else "none — core qualification complete"
    )

    return f"""
============================================================
QUALIFICATION STATUS
============================================================

{chr(10).join(lines)}

NEXT PRIORITY:
{next_priority}

CORE QUALIFICATION COMPLETE:
{"YES" if is_fully_qualified(lead) else "NO"}

CURRENT NEXT-STEP INTENT:
{lead.next_step_intent}

IMPORTANT:

Use this information internally.

Do not mention qualification fields to the customer.

Ask only ONE useful question.

Do not make the conversation sound like a checklist.

Do not ask for information already collected.

Do not initiate human handoff before core qualification is complete.
"""


def build_system_prompt() -> str:
    return (
        NEXTFIT_SYSTEM_PROMPT
        + "\n\n"
        + build_business_info()
        + "\n\n"
        + build_qualification_status()
    )


def clean_json_response(text: str) -> str:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

    first_brace = text.find("{")
    last_brace = text.rfind("}")

    if (
        first_brace != -1
        and last_brace != -1
    ):
        text = text[
            first_brace:last_brace + 1
        ]

    return text.strip()


def _normalize_text(value: Any) -> Any:
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.lower() in {
        "unknown",
        "none",
        "null",
        "n/a",
        "na",
        "-",
    }:
        return None

    return value


def _conversation_text() -> str:
    return "\n".join(
        f"{message.role.upper()}: {message.content}"
        for message in conversation.messages
    )


def _has_phrase(text: str, phrases: list[str]) -> bool:
    text = text.lower()

    return any(
        phrase in text
        for phrase in phrases
    )


def infer_experience_from_conversation(
    transcript: str,
) -> str | None:

    text = transcript.lower()

    if _has_phrase(
        text,
        [
            "new to the gym",
            "new to fitness",
            "never trained",
            "never worked out",
            "i'm a beginner",
            "i am a beginner",
        ],
    ):
        return "beginner"

    if _has_phrase(
        text,
        [
            "getting back into training",
            "getting back to training",
            "coming back to the gym",
            "started training again",
            "starting again",
            "returning to training",
        ],
    ):
        return "returning"

    if _has_phrase(
        text,
        [
            "currently train",
            "currently training",
            "currently working out",
            "i train ",
            "i work out ",
            "training five days",
            "training 5 days",
            "training six days",
            "training 6 days",
            "training regularly",
            "work out regularly",
            "workout regularly",
        ],
    ):
        return "currently_training"

    if re.search(
        r"\b(?:[2-9]|1[0-9])\s*(?:\+)?\s*years?\b",
        text,
    ):
        if _has_phrase(
            text,
            [
                "training",
                "workout",
                "gym",
                "lifting",
                "fitness",
            ],
        ):
            return "experienced"

    return None


def infer_timeline_from_conversation(
    transcript: str,
) -> str | None:

    text = transcript.lower()

    # Promotional/event dates are NOT joining intent.
    promotional_context = _has_phrase(
        text,
        [
            "independence day",
            "15th august",
            "15 august",
            "discount",
            "discounts",
            "offer",
            "offers",
            "promotion",
            "promo",
        ],
    )

    joining_context = _has_phrase(
        text,
        [
            "want to join",
            "want to start",
            "looking to start",
            "planning to join",
            "planning to start",
            "joining this week",
            "starting this week",
            "start today",
            "start tomorrow",
            "join today",
            "join tomorrow",
            "start next week",
            "join next week",
        ],
    )

    if promotional_context and not joining_context:
        return None

    if _has_phrase(
        text,
        [
            "right now",
            "today",
            "immediately",
            "as soon as possible",
        ],
    ):
        return "immediate"

    if _has_phrase(
        text,
        [
            "this week",
            "within a week",
            "within 7 days",
            "next few days",
        ],
    ):
        return "within_7_days"

    if _has_phrase(
        text,
        [
            "this month",
            "within a month",
            "within 30 days",
            "next month",
        ],
    ):
        return "within_30_days"

    if _has_phrase(
        text,
        [
            "just looking",
            "just exploring",
            "researching",
            "only researching",
        ],
    ):
        return "researching"

    if _has_phrase(
        text,
        [
            "later",
            "not yet",
            "maybe later",
        ],
    ):
        return "later"

    return None


def normalize_lead_data(
    data: dict[str, Any],
    transcript: str,
) -> dict[str, Any]:

    text_fields = [
        "name",
        "intent",
        "goal",
        "current_situation",
        "problem",
        "previous_attempts",
        "desired_outcome",
        "location",
        "availability",
    ]

    for field in text_fields:
        data[field] = _normalize_text(
            data.get(field)
        )

    # ========================================================
    # EXPERIENCE
    # ========================================================

    inferred_experience = infer_experience_from_conversation(
        transcript
    )

    if inferred_experience:
        data["experience"] = inferred_experience
    else:
        raw = data.get("experience")

        if raw is None:
            data["experience"] = "unknown"
        else:
            value = str(
                raw
            ).lower().strip()

            if value not in {
                "beginner",
                "returning",
                "currently_training",
                "experienced",
                "unknown",
            }:
                data["experience"] = "unknown"
            else:
                data["experience"] = value

    # ========================================================
    # TIMELINE
    # ========================================================

    inferred_timeline = infer_timeline_from_conversation(
        transcript
    )

    if inferred_timeline:
        data["timeline"] = inferred_timeline
    else:
        raw = data.get("timeline")

        if raw is None:
            data["timeline"] = "unknown"
        else:
            value = str(
                raw
            ).lower().strip()

            if value not in {
                "immediate",
                "within_7_days",
                "within_30_days",
                "later",
                "researching",
                "unknown",
            }:
                data["timeline"] = "unknown"
            else:
                data["timeline"] = value

    # ========================================================
    # TRAINING PREFERENCE
    # ========================================================

    raw = data.get(
        "training_preference"
    )

    if raw is None:
        data["training_preference"] = "unknown"
    else:
        value = str(
            raw
        ).lower().strip()

        if value not in {
            "membership",
            "personal_training",
            "hybrid",
            "trial",
            "unknown",
        }:
            if _has_phrase(
                value,
                [
                    "personal training",
                    "personal trainer",
                    "one-on-one",
                    "one on one",
                    "trainer",
                    "hands-on guidance",
                    "guided training",
                    "accountability",
                ],
            ):
                data["training_preference"] = (
                    "personal_training"
                )

            elif "hybrid" in value:
                data["training_preference"] = "hybrid"

            elif "trial" in value:
                data["training_preference"] = "trial"

            elif "membership" in value:
                data["training_preference"] = "membership"

            else:
                data["training_preference"] = "unknown"

        else:
            data["training_preference"] = value

    # ========================================================
    # NEXT STEP
    # ========================================================

    raw = data.get(
        "next_step_intent"
    )

    if raw is None:
        data["next_step_intent"] = "unknown"
    else:
        value = str(
            raw
        ).lower().strip()

        if value not in {
            "accepted",
            "interested",
            "maybe",
            "declined",
            "unknown",
        }:
            if _has_phrase(
                value,
                [
                    "accepted",
                    "agreed",
                    "definitely",
                    "happy to",
                    "yes",
                ],
            ):
                data["next_step_intent"] = "accepted"

            elif _has_phrase(
                value,
                [
                    "interested",
                    "open to",
                    "would like",
                    "sounds good",
                ],
            ):
                data["next_step_intent"] = "interested"

            elif "maybe" in value:
                data["next_step_intent"] = "maybe"

            elif _has_phrase(
                value,
                [
                    "not interested",
                    "don't want",
                    "do not want",
                    "declined",
                ],
            ):
                data["next_step_intent"] = "declined"

            else:
                data["next_step_intent"] = "unknown"
        else:
            data["next_step_intent"] = value

    # ========================================================
    # NUMERIC FIELDS
    # ========================================================

    for field in [
        "engagement",
        "program_fit",
        "goal_clarity",
    ]:
        value = data.get(
            field,
            0,
        )

        try:
            value = int(
                float(value)
            )
        except (
            TypeError,
            ValueError,
        ):
            value = 0

        data[field] = max(
            0,
            min(
                10,
                value,
            ),
        )

    # ========================================================
    # HUMAN
    # ========================================================

    data["needs_human"] = bool(
        data.get(
            "needs_human",
            False,
        )
    )

    return data


def merge_lead_data(
    existing: LeadProfile,
    extracted: dict[str, Any],
) -> LeadProfile:

    current = existing.model_dump()

    text_fields = [
        "name",
        "intent",
        "goal",
        "current_situation",
        "problem",
        "previous_attempts",
        "desired_outcome",
        "location",
        "availability",
    ]

    for field in text_fields:
        value = extracted.get(field)

        if value:
            current[field] = value

    categorical_fields = [
        "experience",
        "timeline",
        "training_preference",
        "next_step_intent",
    ]

    for field in categorical_fields:
        value = extracted.get(field)

        if value and value != "unknown":
            current[field] = value

    for field in [
        "engagement",
        "program_fit",
        "goal_clarity",
    ]:
        value = extracted.get(field)

        if value is not None:
            current[field] = value

    # needs_human is NOT allowed to determine final handoff.
    # Final handoff is decided deterministically later.
    current["needs_human"] = False

    return LeadProfile(
        **current
    )


def extract_lead_information() -> LeadProfile:

    if not conversation.messages:
        return conversation.lead

    transcript = _conversation_text()

    extraction_prompt = f"""
{LEAD_EXTRACTION_PROMPT}

CURRENT PROFILE:

{json.dumps(
    conversation.lead.model_dump(),
    indent=2,
    default=str,
)}

CONVERSATION:

{transcript}
"""

    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise lead extraction engine. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": extraction_prompt,
                },
            ],
            temperature=0.1,
            max_tokens=1000,
            reasoning_effort="none",
            reasoning_format="hidden",
        )

        raw_content = (
            completion
            .choices[0]
            .message
            .content
            or ""
        ).strip()

        cleaned = clean_json_response(
            raw_content
        )

        extracted_data = json.loads(
            cleaned
        )

        extracted_data = normalize_lead_data(
            extracted_data,
            transcript,
        )

        new_lead = merge_lead_data(
            conversation.lead,
            extracted_data,
        )

        conversation.lead = new_lead

        print()
        print("=" * 50)
        print("LEAD UPDATED")
        print("=" * 50)
        print(
            json.dumps(
                new_lead.model_dump(),
                indent=2,
                default=str,
            )
        )
        print("=" * 50)
        print()

        return new_lead

    except json.JSONDecodeError as error:
        print(
            "LEAD JSON ERROR:",
            repr(error),
        )
        return conversation.lead

    except ValidationError as error:
        print(
            "LEAD VALIDATION ERROR:",
            error,
        )
        return conversation.lead

    except Exception as error:
        print(
            "LEAD EXTRACTION ERROR:",
            type(error).__name__,
            str(error),
        )
        return conversation.lead


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Vantix NextFit AI Receptionist",
        "version": "0.4.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "conversation_turns": conversation.turn_count,
    }


@app.post(
    "/chat",
    response_model=ChatResponse,
)
def chat(
    request: ChatRequest,
):

    global conversation

    user_message = request.message.strip()

    if not user_message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty.",
        )

    conversation.messages.append(
        ConversationMessage(
            role="user",
            content=user_message,
        )
    )

    conversation.turn_count += 1

    # Extract user information BEFORE AI response
    # so the AI knows what is already known.
    lead = extract_lead_information()

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

    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            messages=messages,
            temperature=0.75,
            max_tokens=400,
            reasoning_effort="none",
            reasoning_format="hidden",
        )

        response_text = (
            completion
            .choices[0]
            .message
            .content
            or ""
        ).strip()

    except Exception as error:

        conversation.messages.pop()

        conversation.turn_count = max(
            0,
            conversation.turn_count - 1,
        )

        print(
            "GROQ CHAT ERROR:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=500,
            detail="AI service temporarily unavailable.",
        )

    conversation.messages.append(
        ConversationMessage(
            role="assistant",
            content=response_text,
        )
    )

    # Extract again after assistant response so the
    # persistent profile is refreshed from the full transcript.
    lead = extract_lead_information()

    result = calculate_qualification(
        lead
    )

    # ========================================================
    # DETERMINISTIC HANDOFF
    # ========================================================

    conversation.handoff_required = (
        is_fully_qualified(lead)
        and lead.next_step_intent
        in {
            "accepted",
            "interested",
        }
        and result.score >= 65
    )

    # Only expose handoff once deterministic conditions are met.
    lead.needs_human = conversation.handoff_required

    conversation.lead = lead

    conversation.conversation_complete = (
        conversation.handoff_required
    )

    return ChatResponse(
        response=response_text,
        lead=lead,
        score=result.score,
        classification=result.classification,
        reasons=result.reasons,
        recommended_action=result.recommended_action,
    )


@app.post("/reset")
def reset_conversation():

    global conversation

    conversation = ConversationState()

    return {
        "status": "reset",
        "message": "Conversation reset successfully.",
    }