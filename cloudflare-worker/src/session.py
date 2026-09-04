"""
Active call session management for NextFit AI Receptionist.

Ties in-memory ConversationState to D1 persistence via session tracking.
Each active call gets a Session that holds IDs for D1 lookups and
the live ConversationState.

Session memory is in-memory only. D1 is persistence only.
"""

from typing import Any, Optional

from conversation import ConversationState
from database import (
    create_call,
    create_customer,
    end_call,
    find_customer_by_phone,
    get_customer_history,
    save_message,
    upsert_lead,
)
from models import LeadProfile


BUSINESS_ID = "nextfit"


class Session:
    """Active call session. Created on call start, destroyed on call end."""

    __slots__ = (
        "session_id",
        "call_sid",
        "customer_id",
        "call_id",
        "conversation_state",
        "db",
        "_persisted_message_count",
    )

    def __init__(
        self,
        db: Any,
        call_sid: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        import uuid

        self.session_id = session_id or str(uuid.uuid4())
        self.call_sid = call_sid
        self.customer_id: Optional[str] = None
        self.call_id: Optional[str] = None
        self.conversation_state = ConversationState()
        self.db = db
        self._persisted_message_count = 0

    async def initialize(
        self,
        caller_phone: Optional[str] = None,
    ) -> None:
        """Initialize session: find/create customer, create call record."""

        if not self.db:
            return

        try:
            if caller_phone:
                # Try to find existing customer
                history = await get_customer_history(
                    self.db, BUSINESS_ID, caller_phone
                )
                if history:
                    self.customer_id = history["customer"]["id"]
                    self.conversation_state.customer_history = history
                else:
                    # New customer
                    new_customer = await create_customer(
                        self.db, BUSINESS_ID, caller_phone
                    )
                    if new_customer:
                        self.customer_id = new_customer["id"]

            self.call_id = await create_call(
                self.db, BUSINESS_ID, self.customer_id, self.call_sid
            )
        except Exception as exc:
            print("SESSION INIT ERROR:", exc)

    async def load_history(self) -> Optional[dict]:
        """Load customer history for context injection."""

        if not self.db or not self.customer_id:
            return None

        try:
            from database import find_customer_by_phone
            # History already loaded during init via customer lookup
            return None
        except Exception:
            return None

    def persist_user_message(self, content: str) -> None:
        """Persist a user message (fire-and-forget, non-blocking)."""

        if not self.db or not self.call_id:
            return

        self._persisted_message_count += 1
        # Fire and forget — don't block the voice pipeline
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                save_message(self.db, BUSINESS_ID, self.call_id, "user", content)
            )
        except Exception as exc:
            print("SESSION PERSIST_USER_MSG ERROR:", exc)

    def persist_assistant_message(self, content: str) -> None:
        """Persist an assistant message (fire-and-forget, non-blocking)."""

        if not self.db or not self.call_id:
            return

        self._persisted_message_count += 1
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                save_message(self.db, BUSINESS_ID, self.call_id, "assistant", content)
            )
        except Exception as exc:
            print("SESSION PERSIST_ASSISTANT_MSG ERROR:", exc)

    async def persist_lead(self) -> None:
        """Persist the current lead profile to D1."""

        if not self.db or not self.call_id:
            return

        try:
            lead = self.conversation_state.lead
            # Only persist if there's meaningful data
            if (
                lead.name
                or lead.phone_number
                or lead.intent
                or lead.goal
                or lead.experience != "unknown"
                or lead.next_step_intent != "unknown"
            ):
                await upsert_lead(
                    self.db,
                    BUSINESS_ID,
                    self.customer_id,
                    self.call_id,
                    lead,
                )
        except Exception as exc:
            print("SESSION PERSIST_LEAD ERROR:", exc)

    async def finalize(self) -> None:
        """End the call: persist final lead, mark call completed."""

        if not self.db or not self.call_id:
            return

        try:
            # Persist final lead state
            await self.persist_lead()

            # Build summary from conversation
            summary = self.conversation_state.conversation_summary

            # End the call
            await end_call(self.db, self.call_id, summary)
        except Exception as exc:
            print("SESSION FINALIZE ERROR:", exc)
