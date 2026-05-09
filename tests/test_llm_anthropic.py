"""Tests for the Anthropic API adapter (message + tool conversion)."""

import json

import pytest

from alpha.llm_anthropic import _convert_messages, _convert_tools


# ── Tool schema conversion ──


class TestConvertTools:
    def test_function_wrapped_form(self):
        openai = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]
        out = _convert_tools(openai)
        assert out == [
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]

    def test_skips_invalid(self):
        assert _convert_tools([{"type": "weird"}, {}, "string"]) == []

    def test_missing_parameters_uses_empty_schema(self):
        openai = [{"type": "function", "function": {"name": "x", "description": "y"}}]
        out = _convert_tools(openai)
        assert out[0]["input_schema"] == {"type": "object", "properties": {}}


# ── Message conversion ──


class TestConvertMessages:
    def test_system_extracted(self):
        sys, msgs = _convert_messages([
            {"role": "system", "content": "You are X"},
            {"role": "user", "content": "hi"},
        ])
        assert sys == "You are X"
        assert msgs == [{"role": "user", "content": "hi"}]

    def test_multiple_system_messages_joined(self):
        sys, _ = _convert_messages([
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "hi"},
        ])
        assert sys == "A\n\nB"

    def test_assistant_text_only(self):
        _, msgs = _convert_messages([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])
        assert msgs[1] == {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        }

    def test_assistant_with_tool_calls(self):
        _, msgs = _convert_messages([
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "I'll check.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"/x"}'},
                    }
                ],
            },
        ])
        blocks = msgs[1]["content"]
        assert blocks[0] == {"type": "text", "text": "I'll check."}
        assert blocks[1] == {
            "type": "tool_use",
            "id": "call_1",
            "name": "read_file",
            "input": {"path": "/x"},
        }

    def test_assistant_with_only_tool_calls_no_text(self):
        _, msgs = _convert_messages([
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
                ],
            },
        ])
        blocks = msgs[1]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"

    def test_tool_results_coalesced_into_user_turn(self):
        # Anthropic requires consecutive tool_results to share one user message.
        _, msgs = _convert_messages([
            {"role": "user", "content": "do two things"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result-a"},
            {"role": "tool", "tool_call_id": "c2", "content": "result-b"},
            {"role": "user", "content": "next"},
        ])
        # Index 2 should be the merged user turn with both tool_results
        merged = msgs[2]
        assert merged["role"] == "user"
        assert isinstance(merged["content"], list)
        assert len(merged["content"]) == 2
        assert merged["content"][0]["tool_use_id"] == "c1"
        assert merged["content"][0]["content"] == "result-a"
        assert merged["content"][1]["tool_use_id"] == "c2"
        # Real user message follows
        assert msgs[3] == {"role": "user", "content": "next"}

    def test_malformed_tool_args_default_to_empty_dict(self):
        _, msgs = _convert_messages([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "not json"}}
                ],
            },
        ])
        assert msgs[0]["content"][0]["input"] == {}

    def test_non_string_tool_content_serialized(self):
        _, msgs = _convert_messages([
            {"role": "tool", "tool_call_id": "c1", "content": {"k": "v"}},
        ])
        assert msgs[0]["content"][0]["content"] == json.dumps({"k": "v"}, ensure_ascii=False)
