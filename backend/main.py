import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel, ValidationError

from .conversation import ConversationState, ConversationMessage
from .models import LeadProfile
from .prompts import NEXTFIT_SYSTEM_PROMPT, LEAD_EXTRACTION_PROMPT
from .qualification import calculate_qualification
from .nextfit_config import NEXTFIT_CONFIG


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY is missing. Add it to your .env file."
    )

client = Groq(api_key=api_key)


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Vantix NextFit AI Receptionist",
    description=(
        "Conversational AI receptionist and lead qualification "
        "system for NextFit."
    ),
    version="0.2.0",
)
# ============================================================
# CORS
# ============================================================

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


# ============================================================
# CONVERSATION STATE
# ============================================================

conversation = ConversationState()


# ============================================================
# SYSTEM PROMPT
# ============================================================

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


# ============================================================
# JSON HELPERS
# ============================================================

def clean_json_response(text: str) -> str:
    """
    Remove markdown code fences and surrounding junk from
    model-generated JSON.
    """

    text = text.strip()

    # Remove ```json ... ``` blocks
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

    # Find the first JSON object if the model added commentary
    first_brace = text.find("{")
    last_brace = text.rfind("}")

    if first_brace != -1 and last_brace != -1:
        text = text[first_brace:last_brace + 1]

    return text.strip()


# ============================================================
# LEAD NORMALIZATION
# ============================================================

def normalize_lead_data(data: dict[str, Any]) -> dict[str, Any]:
    """
    Groq is intentionally allowed to return natural language.

    This function converts that flexible output into the exact
    enum values required by LeadProfile.
    """

    # --------------------------------------------------------
    # BASIC TEXT FIELDS
    # --------------------------------------------------------

    text_fields = [
        "name",
        "intent",
        "goal",
        "current_situation",
        "problem",
        "desired_outcome",
        "location",
        "availability",
    ]

    for field in text_fields:
        value = data.get(field)

        if value is not None:
            value = str(value).strip()

            if not value:
                data[field] = None
            else:
                data[field] = value

    # --------------------------------------------------------
    # EXPERIENCE
    # --------------------------------------------------------

    raw_experience = data.get("experience")

    if raw_experience is None:
        data["experience"] = "unknown"

    else:
        experience = str(raw_experience).lower().strip()

        if experience in {
            "beginner",
            "returning",
            "currently_training",
            "experienced",
            "unknown",
        }:
            data["experience"] = experience

        elif any(
            phrase in experience
            for phrase in [
                "two years",
                "3 years",
                "three years",
                "4 years",
                "four years",
                "5 years",
                "five years",
                "years of training",
                "years training",
                "experienced",
                "advanced",
            ]
        ):
            data["experience"] = "experienced"

        elif any(
            phrase in experience
            for phrase in [
                "currently training",
                "currently working out",
                "training regularly",
                "work out regularly",
                "workout regularly",
            ]
        ):
            data["experience"] = "currently_training"

        elif any(
            phrase in experience
            for phrase in [
                "returning",
                "coming back",
                "started again",
                "getting back",
            ]
        ):
            data["experience"] = "returning"

        elif any(
            phrase in experience
            for phrase in [
                "beginner",
                "never trained",
                "new to training",
                "new to fitness",
            ]
        ):
            data["experience"] = "beginner"

        else:
            data["experience"] = "unknown"

    # --------------------------------------------------------
    # TIMELINE
    # --------------------------------------------------------

    raw_timeline = data.get("timeline")

    if raw_timeline is None:
        data["timeline"] = "unknown"

    else:
        timeline = str(raw_timeline).lower().strip()

        if timeline in {
            "immediate",
            "within_7_days",
            "within_30_days",
            "later",
            "researching",
            "unknown",
        }:
            data["timeline"] = timeline

        elif any(
            phrase in timeline
            for phrase in [
                "today",
                "right now",
                "immediately",
                "as soon as possible",
            ]
        ):
            data["timeline"] = "immediate"

        elif any(
            phrase in timeline
            for phrase in [
                "this week",
                "within a week",
                "within 7 days",
                "next few days",
                "next week",
                "soon",
            ]
        ):
            data["timeline"] = "within_7_days"

        elif any(
            phrase in timeline
            for phrase in [
                "this month",
                "within a month",
                "within 30 days",
                "next month",
            ]
        ):
            data["timeline"] = "within_30_days"

        elif any(
            phrase in timeline
            for phrase in [
                "researching",
                "just looking",
                "just exploring",
                "exploring options",
            ]
        ):
            data["timeline"] = "researching"

        elif "later" in timeline:
            data["timeline"] = "later"

        else:
            data["timeline"] = "unknown"

    # --------------------------------------------------------
    # TRAINING PREFERENCE
    # --------------------------------------------------------

    raw_preference = data.get("training_preference")

    if raw_preference is None:
        data["training_preference"] = "unknown"

    else:
        preference = str(raw_preference).lower().strip()

        if preference in {
            "membership",
            "personal_training",
            "hybrid",
            "trial",
            "unknown",
        }:
            data["training_preference"] = preference

        elif any(
            phrase in preference
            for phrase in [
                "personal training",
                "personal trainer",
                "one-on-one",
                "one on one",
                "directly with a trainer",
                "hands-on guidance",
                "guided approach",
            ]
        ):
            data["training_preference"] = "personal_training"

        elif "hybrid" in preference:
            data["training_preference"] = "hybrid"

        elif "trial" in preference:
            data["training_preference"] = "trial"

        elif "membership" in preference:
            data["training_preference"] = "membership"

        else:
            # Important:
            # "Trains 5 days a week" is NOT a service preference.
            data["training_preference"] = "unknown"

    # --------------------------------------------------------
    # NEXT STEP INTENT
    # --------------------------------------------------------

    raw_next_step = data.get("next_step_intent")

    if raw_next_step is None:
        data["next_step_intent"] = "unknown"

    else:
        next_step = str(raw_next_step).lower().strip()

        if next_step in {
            "accepted",
            "interested",
            "maybe",
            "declined",
            "unknown",
        }:
            data["next_step_intent"] = next_step

        elif any(
            phrase in next_step
            for phrase in [
                "accepted",
                "agreed",
                "yes",
                "definitely",
                "happy to",
                "sure",
            ]
        ):
            data["next_step_intent"] = "accepted"

        elif any(
            phrase in next_step
            for phrase in [
                "interested",
                "open to",
                "would like",
                "sounds good",
            ]
        ):
            data["next_step_intent"] = "interested"

        elif "maybe" in next_step:
            data["next_step_intent"] = "maybe"

        elif any(
            phrase in next_step
            for phrase in [
                "declined",
                "no",
                "not interested",
                "don't want",
            ]
        ):
            data["next_step_intent"] = "declined"

        else:
            data["next_step_intent"] = "unknown"

    # --------------------------------------------------------
    # NUMERIC FIELDS
    # --------------------------------------------------------

    for field in [
        "engagement",
        "program_fit",
        "goal_clarity",
    ]:
        value = data.get(field, 0)

        try:
            value = int(float(value))
        except (TypeError, ValueError):
            value = 0

        data[field] = max(0, min(10, value))

    # --------------------------------------------------------
    # HUMAN HANDOFF
    # --------------------------------------------------------

    value = data.get("needs_human", False)

    if isinstance(value, str):
        data["needs_human"] = value.lower() in {
            "true",
            "yes",
            "1",
        }
    else:
        data["needs_human"] = bool(value)

    return data


# ============================================================
# LEAD EXTRACTION
# ============================================================

def extract_lead_information() -> LeadProfile:
    """
    Analyse the full conversation and extract the current
    structured lead profile.
    """

    if not conversation.messages:
        return conversation.lead

    conversation_text = []

    for message in conversation.messages:
        conversation_text.append(
            f"{message.role.upper()}: {message.content}"
        )

    transcript = "\n".join(conversation_text)

    extraction_prompt = f"""
{LEAD_EXTRACTION_PROMPT}

VERY IMPORTANT:

Return ONLY valid JSON.

Do not return markdown.
Do not explain your answer.
Do not add comments.

Use these EXACT values for enum fields:

experience:
- beginner
- returning
- currently_training
- experienced
- unknown

timeline:
- immediate
- within_7_days
- within_30_days
- later
- researching
- unknown

training_preference:
- membership
- personal_training
- hybrid
- trial
- unknown

next_step_intent:
- accepted
- interested
- maybe
- declined
- unknown

If information is not known, use null for normal text fields
and "unknown" for enum fields.

IMPORTANT FIELD DISTINCTIONS:

experience:
How experienced the person is with fitness/training.

current_situation:
What they currently do.
Example:
"Training five days a week independently."

training_preference:
What kind of NextFit support/service they want.
Example:
"personal_training"

Do NOT put their current training schedule into training_preference.

timeline:
When they want to start.

availability:
Their preferred days/times for training or contact.

next_step_intent:
Whether they accepted, showed interest in, were unsure about,
or declined the next step.

needs_human:
True only when human follow-up is actually appropriate.

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
                        "You are a precise lead data extraction "
                        "engine. Return valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": extraction_prompt,
                },
            ],
            temperature=0.1,
            max_tokens=800,
            reasoning_effort="none",
            reasoning_format="hidden",
        )

        raw_content = (
            completion.choices[0].message.content or ""
        ).strip()

        cleaned_content = clean_json_response(raw_content)

        extracted_data = json.loads(cleaned_content)

        print()
        print("========================================")
        print("RAW EXTRACTED DATA")
        print("========================================")
        print(json.dumps(extracted_data, indent=2))
        print("========================================")

        # Normalize before Pydantic validation
        extracted_data = normalize_lead_data(
            extracted_data
        )

        print("NORMALIZED LEAD DATA")
        print("========================================")
        print(json.dumps(extracted_data, indent=2))
        print("========================================")

        # ----------------------------------------------------
        # Pydantic validation
        # ----------------------------------------------------

        new_lead = LeadProfile(**extracted_data)

        # ----------------------------------------------------
        # Update conversation state
        # ----------------------------------------------------

        conversation.lead = new_lead

        print("LEAD UPDATED SUCCESSFULLY")
        print("========================================")
        print(new_lead.model_dump())
        print("========================================")
        print()

        return new_lead

    except json.JSONDecodeError as error:
        print()
        print("========================================")
        print("LEAD EXTRACTION JSON ERROR")
        print("========================================")
        print(repr(error))
        print("RAW CONTENT:")
        print(raw_content if "raw_content" in locals() else "N/A")
        print("========================================")
        print()

        return conversation.lead

    except ValidationError as error:
        print()
        print("========================================")
        print("LEAD PYDANTIC VALIDATION ERROR")
        print("========================================")
        print(error)
        print()
        print("EXTRACTED DATA:")
        print(
            json.dumps(
                extracted_data,
                indent=2,
                default=str,
            )
        )
        print("========================================")
        print()

        return conversation.lead

    except Exception as error:
        print()
        print("========================================")
        print("LEAD EXTRACTION ERROR")
        print("========================================")
        print(type(error).__name__)
        print(str(error))
        print("========================================")
        print()

        return conversation.lead


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Vantix NextFit AI Receptionist",
        "version": "0.2.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "conversation_turns": conversation.turn_count,
    }


# ============================================================
# CHAT
# ============================================================

@app.post(
    "/chat",
    response_model=ChatResponse,
)
def chat(request: ChatRequest):

    # --------------------------------------------------------
    # VALIDATE MESSAGE
    # --------------------------------------------------------

    if not request.message.strip():
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
            content=request.message.strip(),
        )
    )

    conversation.turn_count += 1

    # --------------------------------------------------------
    # BUILD MESSAGE HISTORY
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
    # CALL GROQ FOR CONVERSATION
    # --------------------------------------------------------

    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            messages=messages,
            temperature=0.7,
            max_tokens=350,
            reasoning_effort="none",
            reasoning_format="hidden",
        )

        response_text = (
            completion.choices[0].message.content or ""
        ).strip()

    except Exception as error:
        # Remove the user message if the AI call completely failed
        # so the conversation state isn't corrupted.
        conversation.messages.pop()
        conversation.turn_count = max(
            0,
            conversation.turn_count - 1,
        )

        print()
        print("========================================")
        print("GROQ CHAT ERROR")
        print("========================================")
        print(type(error).__name__)
        print(str(error))
        print("========================================")
        print()

        raise HTTPException(
            status_code=500,
            detail="AI service temporarily unavailable.",
        )

    # --------------------------------------------------------
    # STORE AI RESPONSE
    # --------------------------------------------------------

    conversation.messages.append(
        ConversationMessage(
            role="assistant",
            content=response_text,
        )
    )

    # --------------------------------------------------------
    # EXTRACT / UPDATE LEAD
    # --------------------------------------------------------

    lead = extract_lead_information()

    # --------------------------------------------------------
    # QUALIFICATION
    # --------------------------------------------------------

    result = calculate_qualification(lead)

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return ChatResponse(
        response=response_text,
        lead=result.lead,
        score=result.score,
        classification=result.classification,
        reasons=result.reasons,
        recommended_action=result.recommended_action,
    )


# ============================================================
# RESET CONVERSATION
# ============================================================

@app.post("/reset")
def reset_conversation():
    """
    Reset the current demo conversation.
    """

    global conversation

    conversation = ConversationState()

    return {
        "status": "reset",
        "message": "Conversation reset successfully.",
    }