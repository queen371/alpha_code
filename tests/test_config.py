"""Tests for configuration system."""

import os

from alpha.config import FEATURES, get_available_providers, load_system_prompt


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
