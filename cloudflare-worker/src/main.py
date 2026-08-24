import json
import re
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from workers import env

from conversation import ConversationMessage, ConversationState
from models import LeadProfile
from prompts import (
    LEAD_EXTRACTION_PROMPT,
    NEXTFIT_SYSTEM_PROMPT,
)
from qualification import (
    calculate_qualification,
    get_missing_qualification_fields,
    get_qualification_status,
    is_fully_qualified,
)
from nextfit_config import NEXTFIT_CONFIG


# ============================================================
# CLOUDFLARE ENVIRONMENT / SECRETS
# ============================================================

GROQ_API_KEY = getattr(env, "GROQ_API_KEY", None)
ELEVENLABS_API_KEY = getattr(env, "ELEVENLABS_API_KEY", None)
ELEVENLABS_VOICE_ID = getattr(env, "ELEVENLABS_VOICE_ID", None)


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Vantix NextFit AI Receptionist",
    description=(
        "Conversational AI receptionist and deterministic "
        "lead qualification system for NextFit."
    ),
    version="0.7.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://vantixlab.info",
        "https://www.vantixlab.info",
        "https://vantixlab.in.net",
        "https://www.vantixlab.in.net",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    lead: LeadProfile
    score: int
    classification: str
    reasons: list[str]
    recommended_action: str


class TTSRequest(BaseModel):
    text: str


# ============================================================
# GLOBAL CONVERSATION STATE
# ============================================================

conversation = ConversationState()


# ============================================================
# API CONFIGURATION
# ============================================================


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

ELEVENLABS_URL = (
    "https://api.elevenlabs.io/v1/text-to-speech"
)


# ============================================================
# GROQ HTTP CLIENT
# ============================================================


async def groq_chat(
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_mode: bool = False,
) -> dict[str, Any]:

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing from Cloudflare environment."
        )

    payload: dict[str, Any] = {
        "model": "qwen/qwen3.6-27b",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        payload["response_format"] = {
            "type": "json_object"
        }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0)
    ) as client:

        response = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        print(
            "GROQ API ERROR:",
            response.status_code,
            response.text,
        )

        raise RuntimeError(
            f"Groq API returned {response.status_code}"
        )

    return response.json()


# ============================================================
# ELEVENLABS TTS
# ============================================================


async def elevenlabs_tts(
    text: str,
) -> bytes:

    if not ELEVENLABS_API_KEY:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is missing from Cloudflare environment."
        )

    if not ELEVENLABS_VOICE_ID:
        raise RuntimeError(
            "ELEVENLABS_VOICE_ID is missing from Cloudflare environment."
        )

    url = (
        f"{ELEVENLABS_URL}/"
        f"{ELEVENLABS_VOICE_ID}"
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0)
    ) as client:

        response = await client.post(
            url,
            params={
                "output_format": "mp3_22050_32"
            },
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_flash_v2_5",
            },
        )

    if response.status_code >= 400:

        print(
            "ELEVENLABS API ERROR:",
            response.status_code,
            response.text,
        )

        raise RuntimeError(
            f"ElevenLabs API returned {response.status_code}"
        )

    return response.content


# ============================================================
# BUSINESS INFORMATION
# ============================================================


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


# ============================================================
# CONTACT STATUS
# ============================================================


def build_contact_status() -> str:

    lead = conversation.lead

    name_status = (
        "COLLECTED"
        if lead.name
        else "MISSING"
    )

    phone_status = (
        "COLLECTED"
        if lead.phone_number
        else "MISSING"
    )

    availability_status = (
        "COLLECTED"
        if lead.availability
        else "MISSING"
    )

    qualification_ready = (
        is_fully_qualified(lead)
        and lead.next_step_intent
        in {"accepted", "interested"}
    )

    if not qualification_ready:

        next_contact_step = (
            "Do not collect contact details yet. "
            "Continue natural qualification."
        )

    elif not lead.name:

        next_contact_step = (
            "The customer has indicated willingness "
            "to continue. Ask for their name."
        )

    elif not lead.phone_number:

        next_contact_step = (
            "Name is collected. "
            "Ask for the best phone number."
        )

    elif not lead.availability:

        next_contact_step = (
            "Name and phone are collected. "
            "Ask what time of day usually works best."
        )

    else:

        next_contact_step = (
            "Contact details are complete. "
            "Do not ask another contact question."
        )

    return f"""
============================================================
CONTACT / HANDOFF STATUS
============================================================

NAME:
{name_status}

PHONE NUMBER:
{phone_status}

AVAILABILITY:
{availability_status}

QUALIFIED + WILLING TO CONTINUE:
{"YES" if qualification_ready else "NO"}

NEXT CONTACT STEP:
{next_contact_step}

IMPORTANT:

Never invent contact information.

Never repeat a contact question if the information
is already collected.

Ask only ONE contact question at a time.

Do not collect name or phone number before genuine
willingness to continue with the NextFit team.

Do not claim a booking or notification occurred.
"""


# ============================================================
# QUALIFICATION STATUS
# ============================================================


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

Do not initiate human handoff before core qualification
is complete.
"""


# ============================================================
# COMPLETE SYSTEM PROMPT
# ============================================================


def build_system_prompt() -> str:

    return (
        NEXTFIT_SYSTEM_PROMPT
        + "\n\n"
        + build_business_info()
        + "\n\n"
        + build_qualification_status()
        + "\n\n"
        + build_contact_status()
    )


# ============================================================
# JSON CLEANING
# ============================================================


def clean_json_response(
    text: str,
) -> str:

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
        and last_brace >= first_brace
    ):

        text = text[
            first_brace:last_brace + 1
        ]

    return text.strip()


# ============================================================
# TEXT NORMALIZATION
# ============================================================


def _normalize_text(
    value: Any,
) -> Any:

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


# ============================================================
# PHONE NORMALIZATION
# ============================================================


def normalize_phone_number(
    value: Any,
) -> str | None:

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    cleaned = re.sub(
        r"[^\d+]",
        "",
        value,
    )

    if not cleaned:
        return None

    digits = re.sub(
        r"\D",
        "",
        cleaned,
    )

    if len(digits) == 10:
        return digits

    if (
        len(digits) == 12
        and digits.startswith("91")
    ):
        return "+" + digits

    if len(digits) >= 7:

        if cleaned.startswith("+"):
            return "+" + digits

        return digits

    return None


# ============================================================
# CONVERSATION TRANSCRIPT
# ============================================================


def _conversation_text() -> str:

    return "\n".join(
        f"{message.role.upper()}: {message.content}"
        for message in conversation.messages
    )


def _has_phrase(
    text: str,
    phrases: list[str],
) -> bool:

    text = text.lower()

    return any(
        phrase.lower() in text
        for phrase in phrases
    )


# ============================================================
# EXPERIENCE INFERENCE
# ============================================================


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
            "im a beginner",
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
            "getting back into the gym",
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
            "i've been training",
            "i have been training",
        ],
    ):

        if re.search(
            r"\b(?:2|3|4|5|6|7|8|9|1[0-9])\s*(?:\+)?\s*years?\b",
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

        return "currently_training"

    if re.search(
        r"\b(?:2|3|4|5|6|7|8|9|1[0-9])\s*(?:\+)?\s*years?\b",
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


# ============================================================
# TIMELINE INFERENCE
# ============================================================


def infer_timeline_from_conversation(
    transcript: str,
) -> str | None:

    text = transcript.lower()

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
            "want to begin",
            "looking to join",
            "ready to join",
            "ready to start",
            "i want to join",
            "i wanna join",
            "i want to start",
            "i wanna start",
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
            "asap",
        ],
    ) and joining_context:

        return "immediate"

    if _has_phrase(
        text,
        [
            "this week",
            "within a week",
            "within 7 days",
            "next few days",
            "in a few days",
        ],
    ) and joining_context:

        return "within_7_days"

    if _has_phrase(
        text,
        [
            "this month",
            "within a month",
            "within 30 days",
            "next month",
        ],
    ) and joining_context:

        return "within_30_days"

    if _has_phrase(
        text,
        [
            "just looking",
            "just exploring",
            "researching",
            "only researching",
            "just checking",
            "looking around",
        ],
    ):
        return "researching"

    if _has_phrase(
        text,
        [
            "maybe later",
            "not yet",
            "later on",
        ],
    ):
        return "later"

    return None


# ============================================================
# NORMALIZE EXTRACTED LEAD
# ============================================================


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

    data["phone_number"] = normalize_phone_number(
        data.get("phone_number")
    )

    # --------------------------------------------------------
    # EXPERIENCE
    # --------------------------------------------------------

    inferred_experience = (
        infer_experience_from_conversation(
            transcript
        )
    )

    if inferred_experience:

        data["experience"] = (
            inferred_experience
        )

    else:

        raw = data.get("experience")

        if raw is None:

            data["experience"] = "unknown"

        else:

            value = (
                str(raw)
                .lower()
                .strip()
            )

            allowed = {
                "beginner",
                "returning",
                "currently_training",
                "experienced",
                "unknown",
            }

            if value not in allowed:
                data["experience"] = "unknown"
            else:
                data["experience"] = value

    # --------------------------------------------------------
    # TIMELINE
    # --------------------------------------------------------

    inferred_timeline = (
        infer_timeline_from_conversation(
            transcript
        )
    )

    if inferred_timeline:

        data["timeline"] = inferred_timeline

    else:

        raw = data.get("timeline")

        if raw is None:

            data["timeline"] = "unknown"

        else:

            value = (
                str(raw)
                .lower()
                .strip()
            )

            allowed = {
                "immediate",
                "within_7_days",
                "within_30_days",
                "later",
                "researching",
                "unknown",
            }

            if value not in allowed:
                data["timeline"] = "unknown"
            else:
                data["timeline"] = value

    # --------------------------------------------------------
    # TRAINING PREFERENCE
    # --------------------------------------------------------

    raw = data.get(
        "training_preference"
    )

    if raw is None:

        data["training_preference"] = (
            "unknown"
        )

    else:

        value = (
            str(raw)
            .lower()
            .strip()
        )

        allowed = {
            "membership",
            "personal_training",
            "hybrid",
            "trial",
            "unknown",
        }

        if value not in allowed:

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
                ],
            ):

                data["training_preference"] = (
                    "personal_training"
                )

            elif "hybrid" in value:

                data["training_preference"] = (
                    "hybrid"
                )

            elif "trial" in value:

                data["training_preference"] = (
                    "trial"
                )

            elif "membership" in value:

                data["training_preference"] = (
                    "membership"
                )

            else:

                data["training_preference"] = (
                    "unknown"
                )

        else:

            data["training_preference"] = value

    # --------------------------------------------------------
    # NEXT STEP INTENT
    # --------------------------------------------------------

    raw = data.get(
        "next_step_intent"
    )

    if raw is None:

        data["next_step_intent"] = (
            "unknown"
        )

    else:

        value = (
            str(raw)
            .lower()
            .strip()
        )

        allowed = {
            "accepted",
            "interested",
            "maybe",
            "declined",
            "unknown",
        }

        if value not in allowed:

            if _has_phrase(
                value,
                [
                    "accepted",
                    "agreed",
                    "definitely",
                    "happy to",
                    "yes",
                    "okay",
                    "ok",
                    "sure",
                ],
            ):

                data["next_step_intent"] = (
                    "accepted"
                )

            elif _has_phrase(
                value,
                [
                    "interested",
                    "open to",
                    "would like",
                    "sounds good",
                ],
            ):

                data["next_step_intent"] = (
                    "interested"
                )

            elif "maybe" in value:

                data["next_step_intent"] = (
                    "maybe"
                )

            elif _has_phrase(
                value,
                [
                    "not interested",
                    "don't want",
                    "do not want",
                    "declined",
                ],
            ):

                data["next_step_intent"] = (
                    "declined"
                )

            else:

                data["next_step_intent"] = (
                    "unknown"
                )

        else:

            data["next_step_intent"] = value

    # --------------------------------------------------------
    # NUMERIC FIELDS
    # --------------------------------------------------------

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
            min(10, value),
        )

    # LLM NEVER DIRECTLY CONTROLS HANDOFF

    data["needs_human"] = False

    return data


# ============================================================
# MERGE LEAD DATA
# ============================================================


def merge_lead_data(
    existing: LeadProfile,
    extracted: dict[str, Any],
) -> LeadProfile:

    current = existing.model_dump()

    text_fields = [
        "name",
        "phone_number",
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

    current["needs_human"] = False

    return LeadProfile(
        **current
    )


# ============================================================
# LEAD EXTRACTION
# ============================================================


async def extract_lead_information() -> LeadProfile:

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

        completion = await groq_chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise lead "
                        "extraction engine. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": extraction_prompt,
                },
            ],
            temperature=0.1,
            max_tokens=1200,
            json_mode=True,
        )

        raw_content = (
            completion
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
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
        print("=" * 60)
        print("LEAD UPDATED")
        print("=" * 60)
        print(
            json.dumps(
                new_lead.model_dump(),
                indent=2,
                default=str,
            )
        )
        print("=" * 60)
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


# ============================================================
# CONTACT COMPLETION
# ============================================================


def contact_details_complete(
    lead: LeadProfile,
) -> bool:

    return bool(
        lead.name
        and lead.phone_number
        and lead.availability
    )


# ============================================================
# HANDOFF ELIGIBILITY
# ============================================================


def handoff_eligible(
    lead: LeadProfile,
    score: int,
) -> bool:

    return (
        is_fully_qualified(lead)
        and lead.next_step_intent
        in {
            "accepted",
            "interested",
        }
        and score >= 65
    )


# ============================================================
# ROOT
# ============================================================


@app.get("/")
async def root():

    return {
        "status": "online",
        "service": "Vantix NextFit AI Receptionist",
        "version": "0.7.0",
        "tts": "elevenlabs",
        "runtime": "cloudflare-workers",
    }


# ============================================================
# HEALTH
# ============================================================


@app.get("/health")
async def health():

    score_result = calculate_qualification(
        conversation.lead
    )

    return {
        "status": "healthy",
        "conversation_turns": (
            conversation.turn_count
        ),
        "handoff_eligible": handoff_eligible(
            conversation.lead,
            score_result.score,
        ),
        "contact_details_complete": (
            contact_details_complete(
                conversation.lead
            )
        ),
        "groq_configured": bool(
            GROQ_API_KEY
        ),
        "tts_configured": bool(
            ELEVENLABS_API_KEY
            and ELEVENLABS_VOICE_ID
        ),
    }


# ============================================================
# CHAT
# ============================================================


@app.post(
    "/chat",
    response_model=ChatResponse,
)
async def chat(
    request: ChatRequest,
):

    global conversation

    user_message = request.message.strip()

    if not user_message:

        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty.",
        )

    # --------------------------------------------------------
    # ADD USER MESSAGE
    # --------------------------------------------------------

    conversation.messages.append(
        ConversationMessage(
            role="user",
            content=user_message,
        )
    )

    conversation.turn_count += 1

    # --------------------------------------------------------
    # EXTRACT BEFORE AI RESPONSE
    # --------------------------------------------------------

    lead = await extract_lead_information()

    # --------------------------------------------------------
    # BUILD CONVERSATION
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # GENERATE AI RESPONSE
    # --------------------------------------------------------

    try:

        completion = await groq_chat(
            messages=messages,
            temperature=0.75,
            max_tokens=400,
        )

        response_text = (
            completion
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
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

    # --------------------------------------------------------
    # ADD ASSISTANT RESPONSE
    # --------------------------------------------------------

    conversation.messages.append(
        ConversationMessage(
            role="assistant",
            content=response_text,
        )
    )

    # --------------------------------------------------------
    # EXTRACT AGAIN FROM FULL TRANSCRIPT
    # --------------------------------------------------------

    lead = await extract_lead_information()

    # --------------------------------------------------------
    # DETERMINISTIC QUALIFICATION
    # --------------------------------------------------------

    result = calculate_qualification(
        lead
    )

    # --------------------------------------------------------
    # DETERMINISTIC HANDOFF
    # --------------------------------------------------------

    qualification_ready = handoff_eligible(
        lead,
        result.score,
    )

    contact_complete = contact_details_complete(
        lead
    )

    conversation.handoff_required = (
        qualification_ready
        and contact_complete
    )

    lead.needs_human = (
        conversation.handoff_required
    )

    conversation.lead = lead

    conversation.conversation_complete = (
        conversation.handoff_required
    )

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return ChatResponse(
        response=response_text,
        lead=lead,
        score=result.score,
        classification=result.classification,
        reasons=result.reasons,
        recommended_action=result.recommended_action,
    )


# ============================================================
# ELEVENLABS TEXT TO SPEECH
# ============================================================


@app.post("/tts")
async def text_to_speech(
    request: TTSRequest,
):

    text = request.text.strip()

    if not text:

        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    if len(text) > 5000:

        raise HTTPException(
            status_code=400,
            detail=(
                "Text is too long for a single "
                "TTS request."
            ),
        )

    try:

        audio_bytes = await elevenlabs_tts(
            text
        )

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": (
                    "inline; filename=nextfit-tts.mp3"
                )
            },
        )

    except Exception as error:

        print(
            "ELEVENLABS TTS ERROR:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Text-to-speech service "
                "temporarily unavailable."
            ),
        )


# ============================================================
# RESET
# ============================================================


@app.post("/reset")
async def reset_conversation():

    global conversation

    conversation = ConversationState()

    return {
        "status": "reset",
        "message": (
            "Conversation reset successfully."
        ),
    }