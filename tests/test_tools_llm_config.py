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


@pytest.fixture(autouse=True)
def stub_llm_probe(request):
    """Stub the live probe. change_llm_model now sends a real call before it
    persists, and the test env's GROQ_API_KEY is fake -- these tests assert on
    persistence and validation, so the probe is stubbed to 'answered' here.
    Tests marked live_probe exercise the real one."""
    if request.node.get_closest_marker("live_probe"):
        yield
        return
    with patch("tools._llm_probe_error", return_value=""):
        yield


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


class TestLlmConfigValidation:
    """A config that cannot be used must be rejected, never persisted: the
    primary has nothing to degrade to, and a broken one takes the supervisor
    down with it -- which is the only route back to these tools."""

    def test_model_that_does_not_answer_is_not_persisted(self, env, as_admin, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # construction precedes the probe
        from tools import change_llm_model, _get_system_config
        before = _get_system_config("llm_model")
        with patch("tools._llm_probe_error", return_value="NotFoundError: unknown model"):
            result = change_llm_model.invoke({
                "model_name": "qwen3-next:80b", "provider": "litellm",
            })
        assert "did not answer" in result
        assert "unknown model" in result
        assert _get_system_config("llm_model") == before

    def test_fallback_that_does_not_answer_is_not_persisted(self, env, as_admin):
        from tools import configure_fallback_llm, _get_system_config
        before = _get_system_config("llm_fallback_model")
        with patch("tools._llm_probe_error", return_value="AuthenticationError: 401"):
            result = configure_fallback_llm.invoke({
                "model_name": "llama-3.1-8b-instant", "provider": "groq",
            })
        assert "did not answer" in result
        assert _get_system_config("llm_fallback_model") == before

    def test_groq_without_a_key_is_rejected(self, env, as_admin, monkeypatch):
        """Groq was the one provider with no key check; it was assumed to have
        GROQ_API_KEY. No deployment of ours sets one."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        from tools import change_llm_model, _get_system_config
        before = _get_system_config("llm_provider")
        result = change_llm_model.invoke({
            "model_name": "llama-3.1-8b-instant", "provider": "groq",
        })
        assert "requires an API key" in result
        assert "GROQ_API_KEY" in result
        assert _get_system_config("llm_provider") == before

    @pytest.mark.live_probe
    def test_probe_reports_a_timeout_rather_than_hanging(self):
        """The OpenAI client defaults to a 600s timeout; the probe is bounded."""
        from tools import _llm_probe_error

        class Slow:
            def invoke(self, _):
                import time; time.sleep(5)

        assert "no response within" in _llm_probe_error(Slow(), timeout=0.5)

    @pytest.mark.live_probe
    def test_probe_returns_empty_when_the_model_answers(self):
        from tools import _llm_probe_error

        class Fine:
            def invoke(self, _):
                return "pong"

        assert _llm_probe_error(Fine()) == ""
