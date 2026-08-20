from typing import Optional, Literal

from pydantic import BaseModel, Field


class LeadProfile(BaseModel):
    """
    Structured information collected naturally
    during a NextFit conversation.
    """

    name: Optional[str] = None

    intent: Optional[str] = None
    goal: Optional[str] = None
    current_situation: Optional[str] = None
    problem: Optional[str] = None
    desired_outcome: Optional[str] = None

    experience: Literal[
        "beginner",
        "returning",
        "currently_training",
        "experienced",
        "unknown",
    ] = "unknown"

    location: Optional[str] = None

    timeline: Literal[
        "immediate",
        "within_7_days",
        "within_30_days",
        "later",
        "researching",
        "unknown",
    ] = "unknown"

    training_preference: Literal[
        "membership",
        "personal_training",
        "hybrid",
        "trial",
        "unknown",
    ] = "unknown"

    availability: Optional[str] = None

    engagement: int = Field(default=0, ge=0, le=10)
    program_fit: int = Field(default=0, ge=0, le=10)
    goal_clarity: int = Field(default=0, ge=0, le=10)

    next_step_intent: Literal[
        "accepted",
        "interested",
        "maybe",
        "declined",
        "unknown",
    ] = "unknown"

    needs_human: bool = False


class LeadUpdate(BaseModel):
    """
    Partial lead information extracted from the latest conversation.

    Only fields actually supported by the conversation are populated.
    """

    name: Optional[str] = None

    intent: Optional[str] = None
    goal: Optional[str] = None
    current_situation: Optional[str] = None
    problem: Optional[str] = None
    desired_outcome: Optional[str] = None

    experience: Optional[
        Literal[
            "beginner",
            "returning",
            "currently_training",
            "experienced",
            "unknown",
        ]
    ] = None

    location: Optional[str] = None

    timeline: Optional[
        Literal[
            "immediate",
            "within_7_days",
            "within_30_days",
            "later",
            "researching",
            "unknown",
        ]
    ] = None

    training_preference: Optional[
        Literal[
            "membership",
            "personal_training",
            "hybrid",
            "trial",
            "unknown",
        ]
    ] = None

    availability: Optional[str] = None

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

    next_step_intent: Optional[
        Literal[
            "accepted",
            "interested",
            "maybe",
            "declined",
            "unknown",
        ]
    ] = None

    needs_human: Optional[bool] = None


class QualificationResult(BaseModel):
    """
    Final deterministic qualification result.
    """

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

    reasons: list[str] = []

    recommended_action: str

    lead: LeadProfile