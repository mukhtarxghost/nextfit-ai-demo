from .models import LeadProfile, QualificationResult


def _has_text(value: str | None) -> bool:
    return bool(
        value
        and value.strip()
        and value.strip().lower()
        not in {
            "unknown",
            "none",
            "null",
            "n/a",
            "na",
            "-",
        }
    )


def get_qualification_status(
    lead: LeadProfile,
) -> dict[str, bool]:
    return {
        "goal": _has_text(lead.goal),
        "current_situation": _has_text(
            lead.current_situation
        ),
        "experience": lead.experience != "unknown",
        "problem": _has_text(lead.problem),
        "previous_attempts": _has_text(
            lead.previous_attempts
        ),
        "support_need": (
            lead.training_preference != "unknown"
            or _has_text(lead.desired_outcome)
        ),
        "location": _has_text(lead.location),
        "timeline": lead.timeline != "unknown",
        "availability": _has_text(
            lead.availability
        ),
    }


def get_missing_qualification_fields(
    lead: LeadProfile,
) -> list[str]:
    status = get_qualification_status(lead)

    ordered_fields = [
        "goal",
        "current_situation",
        "experience",
        "problem",
        "previous_attempts",
        "support_need",
        "location",
        "timeline",
        "availability",
    ]

    return [
        field
        for field in ordered_fields
        if not status[field]
    ]


def is_fully_qualified(
    lead: LeadProfile,
) -> bool:
    status = get_qualification_status(lead)

    core_fields = [
        "goal",
        "current_situation",
        "problem",
        "support_need",
        "location",
        "timeline",
        "availability",
    ]

    return all(
        status[field]
        for field in core_fields
    )


def calculate_qualification(
    lead: LeadProfile,
) -> QualificationResult:

    score = 0
    reasons: list[str] = []

    status = get_qualification_status(lead)

    # ========================================================
    # 1. SERVICE INTENT — 15
    # ========================================================

    preference = (
        lead.training_preference
        or ""
    ).lower().strip()

    intent = (
        lead.intent
        or ""
    ).lower().strip()

    if preference == "personal_training":
        score += 15
        reasons.append(
            "The caller appears to need higher-support guidance."
        )

    elif preference in {
        "hybrid",
        "trial",
    }:
        score += 12
        reasons.append(
            "The caller has expressed interest in a guided NextFit service."
        )

    elif preference == "membership":
        score += 8
        reasons.append(
            "The caller has expressed interest in a gym membership."
        )

    elif intent:
        score += 5
        reasons.append(
            "The caller has expressed a clear fitness-related need."
        )

    # ========================================================
    # 2. GOAL CLARITY — 15
    # ========================================================

    if lead.goal_clarity >= 8:
        score += 15
        reasons.append(
            "The caller has a clear and specific fitness goal."
        )

    elif lead.goal_clarity >= 5:
        score += 10
        reasons.append(
            "The caller has a reasonably defined fitness goal."
        )

    elif lead.goal_clarity > 0:
        score += 5

    # ========================================================
    # 3. PROBLEM — 15
    # ========================================================

    if status["problem"]:
        score += 15
        reasons.append(
            "The caller has identified a specific obstacle or frustration."
        )

    # ========================================================
    # 4. PREVIOUS ATTEMPTS — 10
    # ========================================================

    if status["previous_attempts"]:
        score += 10
        reasons.append(
            "The caller has shared previous attempts to solve the problem."
        )

    # ========================================================
    # 5. PROGRAM FIT — 10
    # ========================================================

    if lead.program_fit >= 8:
        score += 10
        reasons.append(
            "The caller's needs strongly match NextFit's services."
        )

    elif lead.program_fit >= 5:
        score += 7
        reasons.append(
            "The caller appears to be a reasonable fit for NextFit."
        )

    elif lead.program_fit > 0:
        score += 4

    # ========================================================
    # 6. TIMELINE — 15
    # ========================================================

    timeline_scores = {
        "immediate": 15,
        "within_7_days": 15,
        "within_30_days": 10,
        "later": 5,
        "researching": 2,
        "unknown": 0,
    }

    timeline_score = timeline_scores.get(
        lead.timeline,
        0,
    )

    score += timeline_score

    if timeline_score >= 15:
        reasons.append(
            "The caller is ready to take action soon."
        )

    elif timeline_score >= 10:
        reasons.append(
            "The caller is considering starting in the near future."
        )

    # ========================================================
    # 7. ENGAGEMENT — 10
    # ========================================================

    engagement_score = max(
        0,
        min(
            10,
            round(lead.engagement),
        ),
    )

    score += engagement_score

    if engagement_score >= 8:
        reasons.append(
            "The caller is highly engaged."
        )

    elif engagement_score >= 5:
        reasons.append(
            "The caller is reasonably engaged."
        )

    # ========================================================
    # 8. EXPERIENCE — 5
    # ========================================================

    experience_scores = {
        "experienced": 5,
        "currently_training": 5,
        "returning": 4,
        "beginner": 3,
        "unknown": 0,
    }

    score += experience_scores.get(
        lead.experience,
        0,
    )

    # ========================================================
    # 9. LOCATION — 5
    # ========================================================

    if status["location"]:
        score += 5
        reasons.append(
            "The caller's location has been established."
        )

    # ========================================================
    # 10. AVAILABILITY — 5
    # ========================================================

    if status["availability"]:
        score += 5
        reasons.append(
            "The caller's availability has been established."
        )

    # ========================================================
    # 11. NEXT STEP — 5
    # ========================================================

    next_step_scores = {
        "accepted": 5,
        "interested": 3,
        "maybe": 1,
        "declined": 0,
        "unknown": 0,
    }

    score += next_step_scores.get(
        lead.next_step_intent,
        0,
    )

    if lead.next_step_intent == "accepted":
        reasons.append(
            "The caller agreed to continue with the NextFit team."
        )

    elif lead.next_step_intent == "interested":
        reasons.append(
            "The caller showed interest in continuing."
        )

    # ========================================================
    # QUALIFICATION GATE
    # ========================================================

    fully_qualified = is_fully_qualified(lead)

    missing_fields = get_missing_qualification_fields(
        lead
    )

    valid_handoff = (
        fully_qualified
        and lead.next_step_intent
        in {
            "accepted",
            "interested",
        }
    )

    if valid_handoff:
        reasons.append(
            "The lead has completed core qualification and indicated willingness to continue."
        )

    score = min(score, 100)

    # ========================================================
    # CLASSIFICATION
    # ========================================================

    if valid_handoff and score >= 80:
        classification = "HOT"

        recommended_action = (
            "Lead is fully qualified and has agreed to continue. "
            "Prioritize human follow-up."
        )

    elif fully_qualified and score >= 65:
        classification = "QUALIFIED"

        recommended_action = (
            "Lead has completed core qualification. "
            "Confirm interest and move toward the appropriate NextFit next step."
        )

    elif score >= 45:
        classification = "NURTURE"

        recommended_action = (
            "Continue the conversation and naturally collect the remaining "
            "qualification information."
        )

    elif score >= 20:
        classification = "INFORMATION"

        if missing_fields:
            recommended_action = (
                "Continue naturally. Remaining qualification: "
                + ", ".join(missing_fields)
                + "."
            )
        else:
            recommended_action = (
                "The caller is engaged but not yet ready for priority follow-up."
            )

    else:
        classification = "LOW"

        recommended_action = (
            "No priority sales follow-up required."
        )

    return QualificationResult(
        score=score,
        classification=classification,
        reasons=reasons,
        recommended_action=recommended_action,
        lead=lead,
    )