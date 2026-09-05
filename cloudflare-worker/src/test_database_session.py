"""
Tests for database.py and session.py D1 operations.

Uses mock D1 bindings to verify SQL generation and error handling
without requiring a real Cloudflare D1 database.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class MockD1Result:
    """Mock D1 query result."""

    def __init__(self, results=None, first=None):
        self.results = results or []
        self.first_row = first

    def first(self):
        return self.first_row

    def get(self, key, default=None):
        return getattr(self, key, default)


class MockD1Statement:
    """Mock D1 prepared statement."""

    def __init__(self, sql, results=None, first=None):
        self.sql = sql
        self.params = []
        self._results = results or []
        self._first = first

    def bind(self, *args):
        self.params = list(args)
        return self

    async def first(self):
        return self._first

    async def all(self):
        return MockD1Result(results=self._results)

    async def run(self):
        return {"success": True}


class MockD1:
    """Mock D1 database binding."""

    def __init__(self, results=None, first=None):
        self._results = results or []
        self._first = first
        self.last_sql = None
        self.last_params = None

    def prepare(self, sql):
        self.last_sql = sql
        return MockD1Statement(sql, self._results, self._first)


# ============================================================
# DATABASE TESTS
# ============================================================


class TestDatabaseCreateCustomer(unittest.IsolatedAsyncioTestCase):

    async def test_create_customer_success(self):
        from database import create_customer

        db = MockD1()
        result = await create_customer(db, "nextfit", "+919876543210", "Test User")

        self.assertIsNotNone(result)
        self.assertEqual(result["business_id"], "nextfit")
        self.assertEqual(result["phone"], "+919876543210")
        self.assertEqual(result["name"], "Test User")
        self.assertIn("id", result)
        self.assertIn("created_at", result)

    async def test_create_customer_db_error(self):
        from database import create_customer

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 connection failed"))

        result = await create_customer(db, "nextfit", "+919876543210")
        self.assertIsNone(result)


class TestDatabaseFindCustomer(unittest.IsolatedAsyncioTestCase):

    async def test_find_customer_found(self):
        from database import find_customer_by_phone

        mock_customer = {"id": "cust-123", "phone": "+919876543210", "name": "Test"}
        db = MockD1(first=mock_customer)

        result = await find_customer_by_phone(db, "nextfit", "+919876543210")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "cust-123")

    async def test_find_customer_not_found(self):
        from database import find_customer_by_phone

        db = MockD1(first=None)
        result = await find_customer_by_phone(db, "nextfit", "+910000000000")
        self.assertIsNone(result)

    async def test_find_customer_db_error(self):
        from database import find_customer_by_phone

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 error"))

        result = await find_customer_by_phone(db, "nextfit", "+919876543210")
        self.assertIsNone(result)


class TestDatabaseCreateCall(unittest.IsolatedAsyncioTestCase):

    async def test_create_call_success(self):
        from database import create_call

        db = MockD1()
        call_id = await create_call(db, "nextfit", "cust-123", "call-sid-abc")

        self.assertIsNotNone(call_id)
        self.assertIsInstance(call_id, str)

    async def test_create_call_db_error(self):
        from database import create_call

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 error"))

        call_id = await create_call(db, "nextfit", None, None)
        self.assertIsNone(call_id)


class TestDatabaseEndCall(unittest.IsolatedAsyncioTestCase):

    async def test_end_call_success(self):
        from database import end_call

        db = MockD1()
        result = await end_call(db, "call-123", "Test summary")
        self.assertTrue(result)

    async def test_end_call_db_error(self):
        from database import end_call

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 error"))

        result = await end_call(db, "call-123")
        self.assertFalse(result)


class TestDatabaseSaveMessage(unittest.IsolatedAsyncioTestCase):

    async def test_save_message_success(self):
        from database import save_message

        db = MockD1()
        msg_id = await save_message(db, "nextfit", "call-123", "user", "Hello")

        self.assertIsNotNone(msg_id)
        self.assertIsInstance(msg_id, str)

    async def test_save_message_db_error(self):
        from database import save_message

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 error"))

        msg_id = await save_message(db, "nextfit", "call-123", "user", "Hello")
        self.assertIsNone(msg_id)


class TestDatabaseUpsertLead(unittest.IsolatedAsyncioTestCase):

    async def test_upsert_lead_insert(self):
        from database import upsert_lead
        from models import LeadProfile

        db = MockD1(first=None)  # No existing lead
        lead = LeadProfile(
            name="Test",
            intent="membership",
            goal="lose weight",
        )

        lead_id = await upsert_lead(db, "nextfit", "cust-123", "call-123", lead)
        self.assertIsNotNone(lead_id)

    async def test_upsert_lead_update(self):
        from database import upsert_lead
        from models import LeadProfile

        existing = {"id": "lead-123"}
        db = MockD1(first=existing)
        lead = LeadProfile(
            name="Test",
            intent="personal_training",
        )

        lead_id = await upsert_lead(db, "nextfit", "cust-123", "call-123", lead)
        self.assertEqual(lead_id, "lead-123")

    async def test_upsert_lead_db_error(self):
        from database import upsert_lead
        from models import LeadProfile

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 error"))
        lead = LeadProfile()

        lead_id = await upsert_lead(db, "nextfit", None, None, lead)
        self.assertIsNone(lead_id)


class TestDatabaseGetCustomerHistory(unittest.IsolatedAsyncioTestCase):

    async def test_get_customer_history_found(self):
        from database import get_customer_history

        mock_customer = {"id": "cust-123", "phone": "+919876543210"}
        mock_calls = [
            {"id": "call-1", "intent": "membership", "summary": "Interested in joining"}
        ]
        db = MockD1()
        # First call returns customer, second returns calls
        call_count = [0]
        original_prepare = db.prepare

        def mock_prepare(sql):
            stmt = MockD1Statement(sql)
            if "FROM customers" in sql:
                stmt._first = mock_customer
            elif "FROM calls" in sql:
                stmt._results = mock_calls
            return stmt

        db.prepare = mock_prepare

        result = await get_customer_history(db, "nextfit", "+919876543210")
        self.assertIsNotNone(result)
        self.assertEqual(result["customer"]["id"], "cust-123")
        self.assertEqual(len(result["recent_calls"]), 1)

    async def test_get_customer_history_not_found(self):
        from database import get_customer_history

        db = MockD1(first=None)
        result = await get_customer_history(db, "nextfit", "+910000000000")
        self.assertIsNone(result)

    async def test_get_customer_history_db_error(self):
        from database import get_customer_history

        db = MockD1()
        db.prepare = MagicMock(side_effect=Exception("D1 error"))

        result = await get_customer_history(db, "nextfit", "+919876543210")
        self.assertIsNone(result)


# ============================================================
# SESSION TESTS
# ============================================================


class TestSession(unittest.IsolatedAsyncioTestCase):

    async def test_session_initialize_new_customer(self):
        from session import Session

        db = MockD1()
        session = Session(db=db, call_sid="call-abc")

        # Mock database operations
        with patch("session.find_customer_by_phone", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = None
            with patch("session.create_customer", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = {"id": "new-cust-123"}
                with patch("session.create_call", new_callable=AsyncMock) as mock_call:
                    mock_call.return_value = "new-call-123"

                    await session.initialize(caller_phone="+919876543210")

                    self.assertEqual(session.customer_id, "new-cust-123")
                    self.assertEqual(session.call_id, "new-call-123")

    async def test_session_initialize_existing_customer(self):
        from session import Session

        db = MockD1()
        session = Session(db=db, call_sid="call-abc")

        mock_history = {
            "customer": {"id": "existing-cust-456", "name": "Existing"},
            "recent_calls": [{"intent": "membership"}],
        }

        with patch("session.get_customer_history", new_callable=AsyncMock) as mock_hist:
            mock_hist.return_value = mock_history
            with patch("session.create_call", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = "new-call-456"

                await session.initialize(caller_phone="+919876543210")

                self.assertEqual(session.customer_id, "existing-cust-456")
                self.assertEqual(session.call_id, "new-call-456")
                self.assertIsNotNone(session.conversation_state.customer_history)

    async def test_session_initialize_no_phone(self):
        from session import Session

        db = MockD1()
        session = Session(db=db, call_sid="call-abc")

        with patch("session.create_call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "call-anon"

            await session.initialize(caller_phone=None)

            self.assertIsNone(session.customer_id)
            self.assertEqual(session.call_id, "call-anon")

    async def test_session_initialize_db_error(self):
        from session import Session

        db = MockD1()
        session = Session(db=db, call_sid="call-abc")

        with patch("session.find_customer_by_phone", new_callable=AsyncMock) as mock_find:
            mock_find.side_effect = Exception("DB failed")
            with patch("session.create_call", new_callable=AsyncMock) as mock_call:
                mock_call.side_effect = Exception("DB failed")

                # Should not raise
                await session.initialize(caller_phone="+919876543210")
                self.assertIsNone(session.call_id)

    async def test_session_finalize(self):
        from session import Session

        db = MockD1()
        session = Session(db=db, call_sid="call-abc")
        session.call_id = "call-123"
        session.customer_id = "cust-123"
        session.conversation_state.lead.name = "Test"
        session.conversation_state.conversation_summary = "Test conversation"

        with patch("session.upsert_lead", new_callable=AsyncMock) as mock_lead:
            with patch("session.end_call", new_callable=AsyncMock) as mock_end:
                await session.finalize()

                mock_lead.assert_called_once()
                mock_end.assert_called_once()

    async def test_session_finalize_no_call_id(self):
        from session import Session

        db = MockD1()
        session = Session(db=db, call_sid="call-abc")

        with patch("session.upsert_lead", new_callable=AsyncMock) as mock_lead:
            with patch("session.end_call", new_callable=AsyncMock) as mock_end:
                await session.finalize()

                mock_lead.assert_not_called()
                mock_end.assert_not_called()


# ============================================================
# CONVERSATION STATE HISTORY TESTS
# ============================================================


class TestConversationStateHistory(unittest.TestCase):

    def test_customer_history_default_none(self):
        from conversation import ConversationState

        state = ConversationState()
        self.assertIsNone(state.customer_history)

    def test_customer_history_settable(self):
        from conversation import ConversationState

        state = ConversationState()
        history = {
            "customer": {"id": "cust-1", "name": "Test"},
            "recent_calls": [],
        }
        state.customer_history = history
        self.assertEqual(state.customer_history["customer"]["name"], "Test")

    def test_customer_history_serializable(self):
        from conversation import ConversationState

        state = ConversationState()
        state.customer_history = {
            "customer": {"id": "cust-1"},
            "recent_calls": [{"intent": "membership"}],
        }

        dumped = state.model_dump()
        self.assertIn("customer_history", dumped)
        self.assertEqual(dumped["customer_history"]["customer"]["id"], "cust-1")


# ============================================================
# BUILD CUSTOMER HISTORY TEXT TESTS
# ============================================================


class TestBuildCustomerHistoryText(unittest.TestCase):

    def test_no_history(self):
        from main import build_customer_history_text, _conversation_context
        from conversation import ConversationState

        token = _conversation_context.set(ConversationState())
        try:
            result = build_customer_history_text()
            self.assertEqual(result, "")
        finally:
            _conversation_context.reset(token)

    def test_with_history(self):
        from main import build_customer_history_text, _conversation_context
        from conversation import ConversationState

        state = ConversationState()
        state.customer_history = {
            "customer": {"id": "cust-1", "name": "Rahul"},
            "recent_calls": [
                {"intent": "membership", "goal": "lose weight", "experience": "beginner"},
            ],
        }

        token = _conversation_context.set(state)
        try:
            result = build_customer_history_text()
            self.assertIn("CUSTOMER HISTORY", result)
            self.assertIn("Rahul", result)
            self.assertIn("membership", result)
        finally:
            _conversation_context.reset(token)


# ============================================================
# SYSTEM PROMPT SIZE REGRESSION TEST
# ============================================================

MAX_FRESH_SYSTEM_PROMPT_CHARS = 4000


class TestSystemPromptSizeRegression(unittest.TestCase):
    """Ensure the system prompt for a fresh call stays compact.

    Before session-memory work, production Groq input was ~3-5k chars.
    A prompt bloat regression expanded NEXTFIT_CHAT_PROMPT from 1.5k to
    13k chars, pushing the first-request baseline to ~14k. This test
    prevents that from recurring.
    """

    def test_fresh_call_system_prompt_is_compact(self):
        from main import build_system_prompt, _conversation_context
        from conversation import ConversationState

        state = ConversationState()
        token = _conversation_context.set(state)
        try:
            prompt = build_system_prompt()
            self.assertLess(
                len(prompt),
                MAX_FRESH_SYSTEM_PROMPT_CHARS,
                f"System prompt for fresh call is {len(prompt)} chars, "
                f"exceeds {MAX_FRESH_SYSTEM_PROMPT_CHARS} limit. "
                f"This likely means NEXTFIT_CHAT_PROMPT was bloated again.",
            )
        finally:
            _conversation_context.reset(token)

    def test_chat_prompt_base_is_compact(self):
        from prompts import NEXTFIT_CHAT_PROMPT

        self.assertLess(
            len(NEXTFIT_CHAT_PROMPT),
            2500,
            f"NEXTFIT_CHAT_PROMPT is {len(NEXTFIT_CHAT_PROMPT)} chars, "
            f"exceeds 2500 limit. Keep it concise — context blocks "
            f"inject behavioral rules each turn.",
        )


if __name__ == "__main__":
    unittest.main()
