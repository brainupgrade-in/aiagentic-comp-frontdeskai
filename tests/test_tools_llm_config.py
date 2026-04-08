"""Tests for LLM config tools: get_llm_config, change_llm_model, configure_fallback_llm."""

import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def setup_db(env):
    from tools import _get_db
    _get_db().close()


@pytest.fixture()
def as_admin(env):
    """Set current_user_email ContextVar to admin for tool calls."""
    from auth import current_user_email
    token = current_user_email.set(env["admin_email"])
    yield
    current_user_email.reset(token)


class TestGetLlmConfig:
    def test_returns_provider_and_model(self, env):
        from tools import get_llm_config
        result = get_llm_config.invoke({})
        assert "groq" in result.lower()
        assert "llama" in result.lower() or "model" in result.lower()

    def test_shows_fallback_not_configured_by_default(self, env):
        from tools import get_llm_config
        result = get_llm_config.invoke({})
        assert "fallback" in result.lower()
        assert "not configured" in result.lower() or "configure fallback" in result.lower()

    def test_shows_fallback_after_configure(self, env, as_admin):
        from tools import configure_fallback_llm, get_llm_config
        with patch("agents.reload_llm_config"):
            configure_fallback_llm.invoke({
                "model_name": "llama-3.1-8b-instant",
                "provider": "groq",
            })
        result = get_llm_config.invoke({})
        assert "llama-3.1-8b-instant" in result


class TestChangeLlmModel:
    def test_change_to_valid_groq_model(self, env, as_admin):
        from tools import change_llm_model
        with patch("agents.reload_llm_config"):
            result = change_llm_model.invoke({
                "model_name": "llama-3.1-8b-instant",
                "provider": "groq",
            })
        assert "llama-3.1-8b-instant" in result
        assert "success" in result.lower() or "updated" in result.lower()

    def test_invalid_groq_model_rejected(self, env, as_admin):
        from tools import change_llm_model
        result = change_llm_model.invoke({
            "model_name": "gpt-4",   # not a Groq model
            "provider": "groq",
        })
        assert "invalid" in result.lower() or "valid" in result.lower()

    def test_invalid_provider_rejected(self, env, as_admin):
        from tools import change_llm_model
        result = change_llm_model.invoke({
            "model_name": "llama-3.1-8b-instant",
            "provider": "anthropic",
        })
        assert "invalid" in result.lower()

    def test_openrouter_without_api_key_rejected(self, env, as_admin, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from tools import change_llm_model
        result = change_llm_model.invoke({
            "model_name": "google/gemini-2.0-flash-001",
            "provider": "openrouter",
        })
        assert "api key" in result.lower() or "key" in result.lower()

    def test_change_persists_to_db(self, env, as_admin):
        from tools import change_llm_model, _get_system_config
        with patch("agents.reload_llm_config"):
            change_llm_model.invoke({
                "model_name": "llama-3.1-8b-instant",
                "provider": "groq",
            })
        assert _get_system_config("llm_model") == "llama-3.1-8b-instant"


class TestConfigureFallbackLlm:
    def test_configure_valid_fallback(self, env, as_admin):
        from tools import configure_fallback_llm
        with patch("agents.reload_llm_config"):
            result = configure_fallback_llm.invoke({
                "model_name": "llama-3.1-8b-instant",
                "provider": "groq",
            })
        assert "llama-3.1-8b-instant" in result
        assert "fallback" in result.lower()

    def test_disable_fallback_with_none(self, env, as_admin):
        from tools import configure_fallback_llm, _get_system_config
        with patch("agents.reload_llm_config"):
            configure_fallback_llm.invoke({"model_name": "llama-3.1-8b-instant", "provider": "groq"})
            result = configure_fallback_llm.invoke({"model_name": "none"})
        assert "disabled" in result.lower()
        assert _get_system_config("llm_fallback_model") == ""

    def test_invalid_fallback_model_rejected(self, env, as_admin):
        from tools import configure_fallback_llm
        result = configure_fallback_llm.invoke({
            "model_name": "nonexistent-model-xyz",
            "provider": "groq",
        })
        assert "invalid" in result.lower()

    def test_fallback_persists_to_db(self, env, as_admin):
        from tools import configure_fallback_llm, _get_system_config
        with patch("agents.reload_llm_config"):
            configure_fallback_llm.invoke({
                "model_name": "llama-3.1-8b-instant",
                "provider": "groq",
            })
        assert _get_system_config("llm_fallback_model") == "llama-3.1-8b-instant"
        assert _get_system_config("llm_fallback_provider") == "groq"
