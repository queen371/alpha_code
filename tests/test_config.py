"""Tests for configuration system."""

import os

import pytest

from alpha.config import FEATURES, get_available_providers, get_provider_config, load_system_prompt


class TestConfig:
    def test_features_delegate_enabled(self):
        assert FEATURES["delegate_tool_enabled"] is True
        assert FEATURES["multi_agent_enabled"] is True

    def test_subagent_iterations_limit(self):
        assert FEATURES["subagent_max_iterations"] == 15
        assert FEATURES["max_parallel_agents"] == 3

    def test_system_prompt_loads(self):
        prompt = load_system_prompt()
        assert "ALPHA" in prompt
        assert len(prompt) > 100

    def test_available_providers(self):
        providers = get_available_providers()
        names = [p["id"] for p in providers]
        assert "deepseek" in names
        assert "openai" in names
        assert "grok" in names
        assert "ollama" in names

    def test_ollama_always_available(self):
        providers = get_available_providers()
        ollama = next(p for p in providers if p["id"] == "ollama")
        assert ollama["available"] is True


class TestProviderVisionFlag:
    """get_provider_config must expose supports_vision per provider."""

    def test_openai_supports_vision(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        cfg = get_provider_config("openai")
        assert cfg["supports_vision"] is True

    def test_anthropic_supports_vision(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        cfg = get_provider_config("anthropic")
        assert cfg["supports_vision"] is True

    def test_deepseek_does_not_support_vision(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
        cfg = get_provider_config("deepseek")
        assert cfg["supports_vision"] is False

    def test_grok_does_not_support_vision(self, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "test")
        cfg = get_provider_config("grok")
        assert cfg["supports_vision"] is False

    def test_ollama_does_not_support_vision_by_default(self):
        cfg = get_provider_config("ollama")
        assert cfg["supports_vision"] is False
