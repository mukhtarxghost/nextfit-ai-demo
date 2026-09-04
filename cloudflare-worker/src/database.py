"""
D1 database access layer for NextFit AI Receptionist.

All operations are wrapped in try/except — D1 failures must never
crash the voice conversation. Parameterized SQL only.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from models import LeadProfile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ============================================================
# CUSTOMER
# ============================================================


async def find_customer_by_phone(
    db: Any,
    business_id: str,
    phone: str,
) -> Optional[dict]:
    """Find a customer by phone number."""

    try:
        result = await db.prepare(
            "SELECT id, business_id, phone, name, email, created_at, updated_at "
            "FROM customers WHERE business_id = ? AND phone = ?"
        ).bind(business_id, phone).first()
        return result
    except Exception as exc:
        print("D1 FIND_CUSTOMER ERROR:", exc)
        return None


async def create_customer(
    db: Any,
    business_id: str,
    phone: str,
    name: Optional[str] = None,
) -> Optional[dict]:
    """Create a new customer record."""

    customer_id = _uuid()
    now = _now()

    try:
        await db.prepare(
            "INSERT INTO customers (id, business_id, phone, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        ).bind(customer_id, business_id, phone, name, now, now).run()

        return {
            "id": customer_id,
            "business_id": business_id,
            "phone": phone,
            "name": name,
            "created_at": now,
            "updated_at": now,
        }
    except Exception as exc:
        print("D1 CREATE_CUSTOMER ERROR:", exc)
        return None


async def update_customer(
    db: Any,
    customer_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> bool:
    """Update customer fields."""

    sets = []
    params: list[Any] = []

    if name is not None:
        sets.append("name = ?")
        params.append(name)

    if email is not None:
        sets.append("email = ?")
        params.append(email)

    if not sets:
        return True

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(customer_id)

    try:
        await db.prepare(
            f"UPDATE customers SET {', '.join(sets)} WHERE id = ?"
        ).bind(*params).run()
        return True
    except Exception as exc:
        print("D1 UPDATE_CUSTOMER ERROR:", exc)
        return False


# ============================================================
# CALL
# ============================================================


async def create_call(
    db: Any,
    business_id: str,
    customer_id: Optional[str],
    call_sid: Optional[str],
) -> Optional[str]:
    """Create a new call record. Returns call_id."""

    call_id = _uuid()
    now = _now()

    try:
        await db.prepare(
            "INSERT INTO calls (id, business_id, customer_id, call_sid, started_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')"
        ).bind(call_id, business_id, customer_id, call_sid, now).run()
        return call_id
    except Exception as exc:
        print("D1 CREATE_CALL ERROR:", exc)
        return None


async def end_call(
    db: Any,
    call_id: str,
    summary: Optional[str] = None,
) -> bool:
    """Mark a call as completed."""

    now = _now()

    try:
        await db.prepare(
            "UPDATE calls SET ended_at = ?, status = 'completed', "
            "duration_seconds = CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER), "
            "summary = ? WHERE id = ?"
        ).bind(now, now, summary, call_id).run()
        return True
    except Exception as exc:
        print("D1 END_CALL ERROR:", exc)
        return False


# ============================================================
# LEAD
# ============================================================


async def upsert_lead(
    db: Any,
    business_id: str,
    customer_id: Optional[str],
    call_id: Optional[str],
    lead: LeadProfile,
) -> Optional[str]:
    """Insert or update a lead record. Returns lead_id."""

    now = _now()

    try:
        existing = None
        if call_id:
            existing = await db.prepare(
                "SELECT id FROM leads WHERE business_id = ? AND call_id = ?"
            ).bind(business_id, call_id).first()

        if existing:
            lead_id = existing["id"]
            await db.prepare(
                "UPDATE leads SET "
                "intent = COALESCE(?, intent), "
                "goal = COALESCE(?, goal), "
                "current_situation = COALESCE(?, current_situation), "
                "problem = COALESCE(?, problem), "
                "previous_attempts = COALESCE(?, previous_attempts), "
                "desired_outcome = COALESCE(?, desired_outcome), "
                "experience = COALESCE(?, experience), "
                "location = COALESCE(?, location), "
                "timeline = COALESCE(?, timeline), "
                "training_preference = COALESCE(?, training_preference), "
                "availability = COALESCE(?, availability), "
                "engagement = ?, program_fit = ?, goal_clarity = ?, "
                "next_step_intent = COALESCE(?, next_step_intent), "
                "needs_human = ?, updated_at = ? "
                "WHERE id = ?"
            ).bind(
                lead.intent,
                lead.goal,
                lead.current_situation,
                lead.problem,
                lead.previous_attempts,
                lead.desired_outcome,
                lead.experience,
                lead.location,
                lead.timeline,
                lead.training_preference,
                lead.availability,
                lead.engagement,
                lead.program_fit,
                lead.goal_clarity,
                lead.next_step_intent,
                int(lead.needs_human),
                now,
                lead_id,
            ).run()
            return lead_id

        lead_id = _uuid()
        await db.prepare(
            "INSERT INTO leads ("
            "id, business_id, customer_id, call_id, "
            "intent, goal, current_situation, problem, previous_attempts, desired_outcome, "
            "experience, location, timeline, training_preference, availability, "
            "engagement, program_fit, goal_clarity, next_step_intent, needs_human, "
            "status, created_at, updated_at"
            ") VALUES ("
            "?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, "
            "'new', ?, ?"
            ")"
        ).bind(
            lead_id,
            business_id,
            customer_id,
            call_id,
            lead.intent,
            lead.goal,
            lead.current_situation,
            lead.problem,
            lead.previous_attempts,
            lead.desired_outcome,
            lead.experience,
            lead.location,
            lead.timeline,
            lead.training_preference,
            lead.availability,
            lead.engagement,
            lead.program_fit,
            lead.goal_clarity,
            lead.next_step_intent,
            int(lead.needs_human),
            now,
            now,
        ).run()
        return lead_id
    except Exception as exc:
        print("D1 UPSERT_LEAD ERROR:", exc)
        return None


# ============================================================
# MESSAGES
# ============================================================


async def save_message(
    db: Any,
    business_id: str,
    call_id: str,
    role: str,
    content: str,
) -> Optional[str]:
    """Save a conversation message. Returns message_id."""

    msg_id = _uuid()
    now = _now()

    try:
        await db.prepare(
            "INSERT INTO call_messages (id, business_id, call_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        ).bind(msg_id, business_id, call_id, role, content, now).run()
        return msg_id
    except Exception as exc:
        print("D1 SAVE_MESSAGE ERROR:", exc)
        return None


# ============================================================
# CUSTOMER HISTORY
# ============================================================


async def get_customer_history(
    db: Any,
    business_id: str,
    phone: str,
    limit: int = 5,
) -> Optional[dict]:
    """Load customer profile and recent call history.

    Returns dict with 'customer' and 'recent_calls' keys,
    or None if no customer found.
    """

    try:
        customer = await find_customer_by_phone(db, business_id, phone)
        if not customer:
            return None

        calls_result = await db.prepare(
            "SELECT c.id, c.call_sid, c.started_at, c.ended_at, "
            "c.duration_seconds, c.status, c.summary, "
            "l.intent, l.goal, l.experience, l.location, l.timeline, "
            "l.training_preference, l.engagement, l.program_fit, l.goal_clarity "
            "FROM calls c "
            "LEFT JOIN leads l ON l.call_id = c.id "
            "WHERE c.customer_id = ? "
            "ORDER BY c.started_at DESC "
            "LIMIT ?"
        ).bind(customer["id"], limit).all()

        return {
            "customer": customer,
            "recent_calls": calls_result.get("results", []) if calls_result else [],
        }
    except Exception as exc:
        print("D1 GET_CUSTOMER_HISTORY ERROR:", exc)
        return None
