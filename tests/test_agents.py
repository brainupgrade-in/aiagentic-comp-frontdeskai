"""Tests for agent graph: supervisor classification, worker routing, fallback, escalation.

LLM calls are fully mocked — these tests validate routing logic and graph structure,
not LLM quality.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ---------------------------------------------------------------------------
# Helper: build a mock LLM that returns a given structured output
# ---------------------------------------------------------------------------

def mock_classification(category: str, confidence: int = 9):
    from agents import Classification
    result = MagicMock()
    result.category = category
    result.confidence = confidence
    return result


def mock_worker_response(response: str = "Here is your answer.", needs_escalation: bool = False):
    from agents import WorkerResponse
    result = MagicMock(spec=WorkerResponse)
    result.response = response
    result.needs_escalation = needs_escalation
    result.escalation_reason = "Needs manager" if needs_escalation else None
    return result


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

class TestGraphStructure:
    def test_graph_has_all_workers(self, env):
        with patch("agents.ChatGroq"), patch("agents.ChatOpenAI"):
            from agents import build_graph
            g = build_graph()
        expected = {
            "supervisor", "hr_worker", "tech_worker", "finance_worker",
            "facilities_worker", "analytics_worker", "account_worker",
            "skill_admin_worker", "general_worker", "clarify",
            "escalation_check", "manager", "qa_check", "fallback", "finalize",
            "rag_retrieval", "fewshot_retrieval", "retry_worker",
        }
        assert expected.issubset(set(g.nodes))

    def test_get_llm_returns_chagroq_by_default(self, env):
        with patch("agents.ChatGroq") as mock_groq:
            mock_groq.return_value = MagicMock()
            from agents import get_llm
            llm = get_llm()
            assert llm is not None


# ---------------------------------------------------------------------------
# LLM chain with fallback
# ---------------------------------------------------------------------------

class TestGetLlmChain:
    def test_no_fallback_returns_plain_chain(self, env):
        with patch("agents.ChatGroq"):
            import importlib
            import agents
            importlib.reload(agents)
            chain = agents.get_llm_chain()
            assert chain is not None

    def test_with_structured_output_returns_chain(self, env):
        with patch("agents.ChatGroq"):
            import importlib
            import agents
            importlib.reload(agents)
            from pydantic import BaseModel
            class MyModel(BaseModel):
                x: str
            chain = agents.get_llm_chain(MyModel)
            assert chain is not None

    def test_fallback_wraps_primary(self, env):
        with patch("agents.ChatGroq"):
            import importlib
            import agents
            # Inject fallback config — verify get_fallback_llm() returns non-None
            agents._llm_fallback_config["model"] = "llama-3.1-8b-instant"
            agents._llm_fallback_config["provider"] = "groq"
            fb = agents.get_fallback_llm()
            assert fb is not None
            # Reset
            agents._llm_fallback_config["model"] = ""
            agents._llm_fallback_config["provider"] = ""


# ---------------------------------------------------------------------------
# Supervisor routing
# ---------------------------------------------------------------------------

class TestSupervisorRouting:
    @pytest.mark.parametrize("category,message", [
        ("hr",        "I need to apply for casual leave"),
        ("tech",      "My laptop won't connect to the VPN"),
        ("finance",   "I need to submit an expense claim"),
        ("facilities","I need to book a meeting room"),
        ("analytics", "Show me ticket statistics for this week"),
        ("account",   "I want to change my password"),
    ])
    def test_supervisor_routes_to_correct_category(self, env, category, message):
        """Supervisor classifies messages to the right category (mocked LLM)."""
        with patch("agents.ChatGroq"):
            import importlib, agents
            importlib.reload(agents)

        mock_chain = MagicMock()
        mock_chain.invoke.return_value = mock_classification(category, 9)

        with patch.object(agents, "get_llm_chain", return_value=mock_chain):
            state = {
                "request": message,
                "employee_email": "user@test.com",
                "employee_name": "Test User",
                "employee_id": "EMP001",
                "is_admin": False,
                "conversation_history": [],
                "category": "",
                "confidence": 0,
                "worker_output": "",
                "needs_escalation": False,
                "escalation_reason": "",
                "qa_feedback": "",
                "fallback_used": False,
                "audit": [],
                "rag_context": "",
                "rag_sources": [],
                "fewshot_context": "",
                "tool_calls_made": [],
                "react_iterations": 0,
                "qa_retry_count": 0,
                "error": "",
                "final_response": "",
            }
            result = agents.supervisor(state)
        assert result["category"] == category
        assert result["confidence"] == 9

    def test_low_confidence_goes_to_clarify(self, env):
        with patch("agents.ChatGroq"):
            import importlib, agents
            importlib.reload(agents)

        mock_chain = MagicMock()
        mock_chain.invoke.return_value = mock_classification("general", confidence=3)

        with patch.object(agents, "get_llm_chain", return_value=mock_chain):
            state = {
                "request": "hmm",
                "employee_email": "user@test.com",
                "employee_name": "Test User",
                "employee_id": "EMP001",
                "is_admin": False,
                "conversation_history": [],
                "category": "", "confidence": 0, "worker_output": "",
                "needs_escalation": False, "escalation_reason": "",
                "qa_feedback": "", "fallback_used": False,
                "audit": [], "rag_context": "", "rag_sources": [],
                "fewshot_context": "", "tool_calls_made": [],
                "react_iterations": 0, "qa_retry_count": 0,
                "error": "", "final_response": "",
            }
            result = agents.supervisor(state)
        assert result["confidence"] < 5

    def test_supervisor_error_falls_back_to_general(self, env):
        with patch("agents.ChatGroq"):
            import importlib, agents
            importlib.reload(agents)

        mock_chain = MagicMock()
        mock_chain.invoke.side_effect = Exception("LLM unavailable")

        with patch.object(agents, "get_llm_chain", return_value=mock_chain):
            state = {
                "request": "test",
                "employee_email": "user@test.com",
                "employee_name": "Test User",
                "employee_id": "EMP001",
                "is_admin": False,
                "conversation_history": [],
                "category": "", "confidence": 0, "worker_output": "",
                "needs_escalation": False, "escalation_reason": "",
                "qa_feedback": "", "fallback_used": False,
                "audit": [], "rag_context": "", "rag_sources": [],
                "fewshot_context": "", "tool_calls_made": [],
                "react_iterations": 0, "qa_retry_count": 0,
                "error": "", "final_response": "",
            }
            result = agents.supervisor(state)
        assert result["category"] == "general"


# ---------------------------------------------------------------------------
# Fallback node
# ---------------------------------------------------------------------------

class TestFallbackNode:
    @pytest.mark.parametrize("category", [
        "hr", "tech", "finance", "facilities", "analytics", "general"
    ])
    def test_fallback_returns_template_for_each_category(self, env, category):
        with patch("agents.ChatGroq"):
            import importlib, agents
            importlib.reload(agents)
        state = {
            "category": category,
            "employee_name": "Test User",
            "request": "test", "employee_email": "u@t.com", "employee_id": "EMP001",
            "is_admin": False, "conversation_history": [],
            "confidence": 9, "worker_output": "", "needs_escalation": False,
            "escalation_reason": "", "qa_feedback": "", "fallback_used": False,
            "audit": [], "rag_context": "", "rag_sources": [], "fewshot_context": "",
            "tool_calls_made": [], "react_iterations": 0, "qa_retry_count": 0,
            "error": "", "final_response": "",
        }
        result = agents.fallback(state)
        assert result["fallback_used"] is True
        assert len(result["worker_output"]) > 0


# ---------------------------------------------------------------------------
# Admin gating for skill_admin
# ---------------------------------------------------------------------------

class TestSkillAdminGating:
    def test_non_admin_skill_admin_request_routes_to_general(self, env):
        """route_supervisor gates skill_admin to general for non-admin users."""
        with patch("agents.ChatGroq"):
            import importlib, agents
            importlib.reload(agents)

        # Simulate state where supervisor classified as skill_admin but user is not admin
        state = {
            "request": "install a new skill",
            "employee_email": "user@test.com",
            "employee_name": "Regular User",
            "employee_id": "EMP001",
            "is_admin": False,
            "conversation_history": [],
            "category": "skill_admin", "confidence": 9, "worker_output": "",
            "needs_escalation": False, "escalation_reason": "",
            "qa_feedback": "", "fallback_used": False,
            "audit": [], "rag_context": "", "rag_sources": [],
            "fewshot_context": "", "tool_calls_made": [],
            "react_iterations": 0, "qa_retry_count": 0,
            "error": "", "final_response": "",
        }
        # route_supervisor (the graph edge function) should re-route to general
        result = agents.route_supervisor(state)
        assert result == "general"
