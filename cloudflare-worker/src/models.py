from typing import Literal, Optional

from pydantic import BaseModel, Field


Experience = Literal[
    "beginner",
    "returning",
    "currently_training",
    "experienced",
    "unknown",
]


Timeline = Literal[
    "immediate",
    "within_7_days",
    "within_30_days",
    "later",
    "researching",
    "unknown",
]


TrainingPreference = Literal[
    "membership",
    "personal_training",
    "hybrid",
    "trial",
    "unknown",
]


NextStepIntent = Literal[
    "accepted",
    "interested",
    "maybe",
    "declined",
    "unknown",
]


class LeadProfile(BaseModel):
    """
    Persistent structured information collected naturally
    during a NextFit conversation.
    """

    # --------------------------------------------------------
    # Basic contact information
    # --------------------------------------------------------

    name: Optional[str] = None
    phone_number: Optional[str] = None

    # --------------------------------------------------------
    # Lead information
    # --------------------------------------------------------

    intent: Optional[str] = None
    goal: Optional[str] = None
    current_situation: Optional[str] = None
    problem: Optional[str] = None
    previous_attempts: Optional[str] = None
    desired_outcome: Optional[str] = None

    # --------------------------------------------------------
    # Qualification information
    # --------------------------------------------------------

    experience: Experience = "unknown"

    location: Optional[str] = None

    timeline: Timeline = "unknown"

    training_preference: TrainingPreference = "unknown"

    availability: Optional[str] = None

    # --------------------------------------------------------
    # Scoring
    # --------------------------------------------------------

    engagement: int = Field(
        default=0,
        ge=0,
        le=10,
    )

    program_fit: int = Field(
        default=0,
        ge=0,
        le=10,
    )

    goal_clarity: int = Field(
        default=0,
        ge=0,
        le=10,
    )

    # --------------------------------------------------------
    # Next step
    # --------------------------------------------------------

    next_step_intent: NextStepIntent = "unknown"

    needs_human: bool = False


class LeadUpdate(BaseModel):
    """
    Partial lead information extracted from the conversation.

    Fields are optional because the extractor should only provide
    information that is actually supported by the conversation.
    """

    # --------------------------------------------------------
    # Basic contact information
    # --------------------------------------------------------

    name: Optional[str] = None
    phone_number: Optional[str] = None

    # --------------------------------------------------------
    # Lead information
    # --------------------------------------------------------

    intent: Optional[str] = None
    goal: Optional[str] = None
    current_situation: Optional[str] = None
    problem: Optional[str] = None
    previous_attempts: Optional[str] = None
    desired_outcome: Optional[str] = None

    # --------------------------------------------------------
    # Qualification information
    # --------------------------------------------------------

    experience: Optional[Experience] = None

    location: Optional[str] = None

    timeline: Optional[Timeline] = None

    training_preference: Optional[TrainingPreference] = None

    availability: Optional[str] = None

    # --------------------------------------------------------
    # Scores
    # --------------------------------------------------------

    engagement: Optional[int] = Field(
        default=None,
        ge=0,
        le=10,
    )

    program_fit: Optional[int] = Field(
        default=None,
        ge=0,
        le=10,
    )

    goal_clarity: Optional[int] = Field(
        default=None,
        ge=0,
        le=10,
    )

    # --------------------------------------------------------
    # Next step
    # --------------------------------------------------------

    next_step_intent: Optional[NextStepIntent] = None

    needs_human: Optional[bool] = None


class QualificationResult(BaseModel):
    score: int = Field(
        ge=0,
        le=100,
    )

    classification: Literal[
        "HOT",
        "QUALIFIED",
        "NURTURE",
        "INFORMATION",
        "LOW",
    ]

    reasons: list[str] = Field(
        default_factory=list
    )

    recommended_action: str

    lead: LeadProfile
