from backend.models import LeadProfile
from backend.qualification import calculate_qualification


lead = LeadProfile(
    name="Rahul",
    intent="personal_training",
    goal="strength and physique",
    current_situation="Currently training but not progressing",
    problem="Training plateau",
    desired_outcome="Improve strength and physique",
    experience="experienced",
    location="Pune",
    timeline="within_7_days",
    training_preference="personal_training",
    availability="Evenings",
    engagement=9,
    program_fit=9,
    goal_clarity=9,
    next_step_intent="accepted",
    needs_human=True,
)

result = calculate_qualification(lead)

print("\nSCORE:", result.score)
print("CLASSIFICATION:", result.classification)
print("\nREASONS:")

for reason in result.reasons:
    print("-", reason)

print("\nACTION:")
print(result.recommended_action)