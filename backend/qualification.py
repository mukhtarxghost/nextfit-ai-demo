from .models import LeadProfile, QualificationResult


def calculate_qualification(
    lead: LeadProfile,
) -> QualificationResult:
    """
    Deterministic qualification engine for NextFit.

    The AI extracts information.
    This function decides how strong the lead is.
    """

    score = 0
    reasons = []

    # ==================================================
    # 1. INTENT / NEED — 20 POINTS
    # ==================================================

    intent = (
        lead.intent or ""
    ).lower().strip()

    training_preference = (
        lead.training_preference or ""
    ).lower().strip()

    strong_intent = (
        "personal" in intent
        or "personal" in training_preference
        or intent in {
            "pt",
            "fitness studio",
            "fitness_studio",
        }
    )

    good_intent = (
        "membership" in intent
        or "trial" in intent
        or "hybrid" in intent
        or training_preference in {
            "membership",
            "trial",
            "hybrid",
        }
    )

    if strong_intent:

        score += 20

        reasons.append(
            "Clear requirement for a higher-support fitness service."
        )

    elif good_intent:

        score += 15

        reasons.append(
            "Clear interest in a NextFit service."
        )

    elif intent:

        score += 8

        reasons.append(
            "The caller has expressed a fitness-related requirement."
        )

    elif training_preference != "unknown":

        score += 8

        reasons.append(
            "The caller has expressed interest in a NextFit service."
        )

    # ==================================================
    # 2. GOAL CLARITY — 15 POINTS
    # ==================================================

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

    # ==================================================
    # 3. PROGRAM FIT — 15 POINTS
    # ==================================================

    if lead.program_fit >= 8:

        score += 15

        reasons.append(
            "The caller's requirements strongly match the service."
        )

    elif lead.program_fit >= 5:

        score += 10

        reasons.append(
            "The caller appears to be a reasonable fit."
        )

    elif lead.program_fit > 0:

        score += 5

    # ==================================================
    # 4. READINESS / TIMELINE — 15 POINTS
    # ==================================================

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
            "The caller is considering starting within the near future."
        )

    elif lead.timeline == "researching":

        reasons.append(
            "The caller appears to be researching rather than ready to act."
        )

    # ==================================================
    # 5. ENGAGEMENT — 10 POINTS
    # ==================================================

    engagement_score = round(
        lead.engagement
    )

    score += engagement_score

    if engagement_score >= 8:

        reasons.append(
            "The caller is highly engaged in the conversation."
        )

    elif engagement_score >= 5:

        reasons.append(
            "The caller is reasonably engaged."
        )

    # ==================================================
    # 6. EXPERIENCE / CONTEXT — 5 POINTS
    # ==================================================

    experience_scores = {
        "experienced": 5,
        "currently_training": 5,
        "returning": 4,
        "beginner": 3,
        "unknown": 0,
    }

    experience_score = experience_scores.get(
        lead.experience,
        0,
    )

    score += experience_score

    # ==================================================
    # 7. NEXT STEP WILLINGNESS — 10 POINTS
    # ==================================================

    next_step_scores = {
        "accepted": 10,
        "interested": 7,
        "maybe": 4,
        "declined": 0,
        "unknown": 0,
    }

    next_step_score = next_step_scores.get(
        lead.next_step_intent,
        0,
    )

    score += next_step_score

    if lead.next_step_intent == "accepted":

        reasons.append(
            "The caller agreed to speak with the NextFit team."
        )

    elif lead.next_step_intent == "interested":

        reasons.append(
            "The caller showed interest in taking the next step."
        )

    # ==================================================
    # 8. HUMAN HANDOFF
    # ==================================================

    if lead.needs_human:

        reasons.append(
            "Human follow-up is recommended for this conversation."
        )

    # ==================================================
    # FINAL CLASSIFICATION
    # ==================================================

    score = min(
        score,
        100,
    )

    if score >= 85:

        classification = "HOT"

        recommended_action = (
            "Prioritize for human follow-up and offer "
            "a 15–20 minute consultation."
        )

    elif score >= 70:

        classification = "QUALIFIED"

        recommended_action = (
            "Follow up with the lead and assess "
            "availability for a consultation."
        )

    elif score >= 50:

        classification = "NURTURE"

        recommended_action = (
            "Keep the lead in follow-up and provide "
            "relevant information."
        )

    elif score >= 30:

        classification = "INFORMATION"

        recommended_action = (
            "The caller appears to be mainly gathering "
            "information. No priority follow-up required."
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