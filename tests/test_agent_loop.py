"""Tests for agent tool loop: intermediate planning detection, loop detection,
intent classification, task completion guard, and final sanitizer."""

from __future__ import annotations

import re

import pytest

from donovanagent.providers.base import LLMProvider
from donovanagent.providers.models import ToolCall


# ===========================================================================
# Module-level function tests: _loop_signature, _detect_loop
# ===========================================================================


class TestLoopSignature:
    """Tests for _loop_signature() which creates a stable key for tool calls."""

    def test_signature_no_args(self) -> None:
        from donovanagent.agent.agent import _loop_signature

        sig = _loop_signature(ToolCall(id="1", name="web_search", arguments={}))
        assert sig == "web_search()"

    def test_signature_with_args(self) -> None:
        from donovanagent.agent.agent import _loop_signature

        sig = _loop_signature(
            ToolCall(id="2", name="read_file", arguments={"path": "x.txt"})
        )
        assert sig == "read_file(path)"

    def test_signature_sorted_keys(self) -> None:
        from donovanagent.agent.agent import _loop_signature

        sig = _loop_signature(
            ToolCall(id="3", name="run_shell", arguments={"command": "ls", "timeout": "30"})
        )
        # Keys are sorted alphabetically
        assert sig == "run_shell(command,timeout)"

    def test_signature_none_args(self) -> None:
        from donovanagent.agent.agent import _loop_signature

        sig = _loop_signature(ToolCall(id="4", name="test", arguments=None))
        assert sig == "test()"


class TestDetectLoop:
    """Tests for _detect_loop() which detects repeating tool-call patterns."""

    def test_short_history_no_loop(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        assert _detect_loop(["a", "b", "c"]) is None

    def test_identical_calls_detected(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        history = ["web_search(q)"] * 6
        reason = _detect_loop(history, max_identical=5, max_repeating=3)
        assert reason is not None
        assert "same tool call repeated" in reason

    def test_below_threshold_no_loop(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        history = ["web_search(q)"] * 4  # 4 < max_identical (5)
        assert _detect_loop(history, max_identical=5) is None

    def test_repeating_pattern_detected(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        history = ["a", "b", "a", "b", "a", "b", "a", "b"]
        reason = _detect_loop(history, max_identical=5, max_repeating=3)
        assert reason is not None
        assert "pattern repeated" in reason

    def test_custom_thresholds_tighter(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        history = ["web_search(q)"] * 4
        reason = _detect_loop(history, max_identical=4, max_repeating=2)
        assert reason is not None
        assert "same tool call repeated" in reason

    def test_empty_history(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        assert _detect_loop([]) is None

    def test_mixed_tools_no_loop(self) -> None:
        from donovanagent.agent.agent import _detect_loop

        history = [
            "web_search(q)",
            "read_file(p)",
            "web_search(q)",
            "read_file(p)",
            "run_shell(c)",
        ]
        assert _detect_loop(history) is None

    def test_alternating_pair_below_threshold(self) -> None:
        """Two alternating calls repeated 3 times = 6 entries, max_repeating=3 means 3+ repeats."""
        from donovanagent.agent.agent import _detect_loop

        history = ["a", "b", "a", "b", "a", "b"]
        # pattern [a,b] repeats 3 times — at threshold, should NOT fire
        assert _detect_loop(history, max_identical=5, max_repeating=4) is None


# ===========================================================================
# Regex-level tests for intermediate planning detection
# ===========================================================================


class TestIntermediatePlanningIndicators:
    """Tests for _TASK_INCOMPLETE_INDICATORS_RE regex."""

    @pytest.fixture(autouse=True)
    def _load_re(self) -> None:
        from donovanagent.agent.agent import _TASK_INCOMPLETE_INDICATORS_RE

        self.re = _TASK_INCOMPLETE_INDICATORS_RE

    # --- Should match (incomplete/planning text) ---

    def test_step_prefix(self) -> None:
        assert self.re.search("Step 1: Do something")
        assert self.re.search("Step 6: Finding a direct image URL of Choi San")
        assert self.re.search("Step 10: Let me check")

    def test_let_me_variants(self) -> None:
        assert self.re.search("Let me now check the status")
        assert self.re.search("Let me try a different approach")
        assert self.re.search("Let me search for more information")
        assert self.re.search("Let me verify the results")
        assert self.re.search("Let me look up the documentation")
        assert self.re.search("Let me start by reading")

    def test_ill_next_variants(self) -> None:
        assert self.re.search("I'll now search for")
        assert self.re.search("I'll try using the tool")
        assert self.re.search("I'll look up the details")
        assert self.re.search("I'll check the file")
        assert self.re.search("I'll verify the output")

    def test_next_then_first_transitions(self) -> None:
        assert self.re.search("Next, I'll check")
        assert self.re.search("Then, let me verify")
        assert self.re.search("First, I'll need to")
        assert self.re.search("Second, let me check")
        assert self.re.search("Finally, let me run")

    def test_need_to_patterns(self) -> None:
        assert self.re.search("I need to check the file")
        assert self.re.search("I still need to find the answer")

    def test_should_could_will_patterns(self) -> None:
        assert self.re.search("I should now try searching")
        assert self.re.search("I could check the results")
        assert self.re.search("I will now run the command")
        assert self.re.search("I would now search for")

    def test_my_next_step(self) -> None:
        assert self.re.search("My next step is to search")
        assert self.re.search("My next step will be checking")

    def test_lets_variants(self) -> None:
        assert self.re.search("Let's check the results")
        assert self.re.search("Let's try searching for")
        assert self.re.search("Let's start by reading")
        assert self.re.search("Let's move to the next step")
        assert self.re.search("Let's continue investigating")

    # --- Should NOT match (final answer text) ---

    def test_final_answer_not_matched(self) -> None:
        assert not self.re.search("Here is the result of my search")
        assert not self.re.search("The answer to your question is 42")
        assert not self.re.search("I found the information you requested")
        assert not self.re.search("Based on the search results, here is what I found")
        assert not self.re.search("The file contains the following code")

    def test_providing_results_not_matched(self) -> None:
        assert not self.re.search("I checked and the file exists")
        assert not self.re.search("Search results show that the answer is")
        assert not self.re.search("The command completed successfully")
        assert not self.re.search("Here is the updated code")

    def test_empty_string_not_matched(self) -> None:
        assert not self.re.search("")

    def test_punctuation_only_not_matched(self) -> None:
        assert not self.re.search("...")
        assert not self.re.search("---")


class TestIntermediatePlanningLineRe:
    """Tests for _TASK_INCOMPLETE_LINE_RE regex (standalone action lines)."""

    @pytest.fixture(autouse=True)
    def _load_re(self) -> None:
        from donovanagent.agent.agent import _TASK_INCOMPLETE_LINE_RE

        self.re = _TASK_INCOMPLETE_LINE_RE

    def test_finding_as_action(self) -> None:
        assert self.re.search("Finding a direct image URL")
        assert self.re.search("Finding the right file")

    def test_searching_as_action(self) -> None:
        assert self.re.search("Searching for relevant information")
        assert self.re.search("Searching the web for")

    def test_analyzing_as_action(self) -> None:
        assert self.re.search("Analyzing the code")
        assert self.re.search("Analyzing the results")

    def test_looking_as_action(self) -> None:
        assert self.re.search("Looking for the right file")
        assert self.re.search("Looking into the issue")

    def test_checking_as_action(self) -> None:
        assert self.re.search("Checking the file contents")
        assert self.re.search("Checking for updates")

    def test_investigating_as_action(self) -> None:
        assert self.re.search("Investigating the issue")

    def test_finding_as_noun_not_matched(self) -> None:
        assert not self.re.search("The key finding is")
        assert not self.re.search("This finding suggests that")

    def test_empty_not_matched(self) -> None:
        assert not self.re.search("")


# ===========================================================================
# Intent classification tests (frozenset-based)
# ===========================================================================


class TestIntentClassification:
    """Tests for the intent frozensets used by _classify_intent."""

    def test_modify_intent_words(self) -> None:
        from donovanagent.agent.agent import _INTENT_MODIFY

        assert "write" in _INTENT_MODIFY
        assert "edit" in _INTENT_MODIFY
        assert "update" in _INTENT_MODIFY
        assert "create" in _INTENT_MODIFY
        assert "delete" in _INTENT_MODIFY
        assert "fix" in _INTENT_MODIFY

    def test_search_intent_words(self) -> None:
        from donovanagent.agent.agent import _INTENT_SEARCH

        assert "search" in _INTENT_SEARCH
        assert "find" in _INTENT_SEARCH
        assert "lookup" in _INTENT_SEARCH

    def test_shell_intent_words(self) -> None:
        from donovanagent.agent.agent import _INTENT_SHELL

        assert "run" in _INTENT_SHELL
        assert "build" in _INTENT_SHELL
        assert "test" in _INTENT_SHELL
        assert "deploy" in _INTENT_SHELL

    def test_read_intent_words(self) -> None:
        from donovanagent.agent.agent import _INTENT_READ

        assert "read" in _INTENT_READ
        assert "question" in _INTENT_READ
        assert "explain" in _INTENT_READ
        assert "show" in _INTENT_READ
        assert "what" in _INTENT_READ
        assert "how" in _INTENT_READ

    def test_mcp_intent_words(self) -> None:
        from donovanagent.agent.agent import _INTENT_MCP

        assert "mcp" in _INTENT_MCP
        assert "connect" in _INTENT_MCP


# ===========================================================================
# Final sanitizer regression tests
# ===========================================================================


class TestFinalSanitize:
    """Tests for _final_sanitize() which strips residual DSML markup."""

    def _sanitize(self, text: str) -> str:
        """Replicate _final_sanitize logic for regression testing."""
        text = re.sub(
            r"<tool_calls[^>]*>.*?</tool_calls\s*>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r"<invoke\s+[^>]*>.*?</invoke\s*>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(r"</?invoke[^>]*>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"</?parameter[^>]*>", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"<function\s+[^>]*>.*?</function>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r'\{"type":\s*"(?:tool_call|function)".*?"arguments"\s*:\s*\{.*?\}\s*\}',
            "",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def test_strips_tool_calls_block(self) -> None:
        text = (
            'Some text '
            '<tool_calls><invoke name="test"><parameter name="x">y</parameter></invoke></tool_calls>'
        )
        result = self._sanitize(text)
        assert "Some text" in result
        assert "<invoke" not in result
        assert "<tool_calls>" not in result
        assert "<parameter" not in result

    def test_strips_standalone_invoke(self) -> None:
        text = 'stuff <invoke name="web_search"><parameter name="q">test</parameter></invoke> more'
        result = self._sanitize(text)
        assert "<invoke" not in result
        assert "<parameter" not in result
        assert "stuff" in result
        assert "more" in result

    def test_strips_function_json(self) -> None:
        text = '{"type": "tool_call", "tool": "web_search", "arguments": {"query": "test"}}'
        result = self._sanitize(text)
        assert '{"type": "tool_call"' not in result

    def test_collapses_excessive_newlines(self) -> None:
        text = "a\n\n\n\n\nb"
        result = self._sanitize(text)
        assert result == "a\n\nb"

    def test_empty_text_returns_empty(self) -> None:
        assert self._sanitize("") == ""

    def test_plain_text_unchanged(self) -> None:
        text = "Hello, this is a normal response."
        assert self._sanitize(text) == text

    def test_case_insensitive_tag_stripping(self) -> None:
        text = (
            '<TOOL_CALLS><INVOKE name="test">'
            '<PARAMETER name="x">y</PARAMETER></INVOKE></TOOL_CALLS>'
        )
        result = self._sanitize(text)
        assert "TOOL_CALLS" not in result
        assert "INVOKE" not in result

    def test_only_dsml_block_returns_empty(self) -> None:
        text = '<tool_calls><invoke name="test"><parameter name="x">y</parameter></invoke></tool_calls>'
        result = self._sanitize(text)
        assert result == ""

    def test_multiline_invoke(self) -> None:
        text = (
            "Some text.\n"
            '<tool_calls>\n'
            '  <invoke name="mcp__server__tool">\n'
            '    <parameter name="arg1" string="true">value1</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>\n'
            "Done."
        )
        result = self._sanitize(text)
        assert "Some text" in result
        assert "Done" in result
        assert "<invoke" not in result

    def test_malformed_json_not_stripped(self) -> None:
        """Partial JSON tool_call patterns should not be stripped incorrectly."""
        text = 'Normal text with {"some": "json"} in it'
        result = self._sanitize(text)
        assert "Normal text" in result


# ===========================================================================
# Integration-style tests: instantiate DonovanAgent with mock provider
# ===========================================================================


class _MockProvider(LLMProvider):
    """Minimal mock provider that returns empty responses."""
    name = "mock"

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = "auto",
    ) -> ...:
        from donovanagent.providers.models import ChatResponse

        return ChatResponse(content="", prompt_tokens=10)

    def stream_chat(self, messages: list[dict], tools: list[dict] | None = None):
        return iter([])

    def list_models(self):
        return []

    def validate_connection(self):
        return True, ""


@pytest.fixture
def agent():
    """Create a minimal DonovanAgent instance with a mock provider."""
    from rich.console import Console

    from donovanagent.agent.agent import DonovanAgent
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.memory.database import MemoryDatabase
    from donovanagent.tools.approval import ApprovalManager
    from donovanagent.tools.registry import ToolRegistry

    config = DonovanAgentConfig()
    db = MemoryDatabase(":memory:")
    provider = _MockProvider()
    registry = ToolRegistry(config)
    console = Console()
    approval = ApprovalManager(config)
    return DonovanAgent(config, db, provider, registry, console, approval)


class TestDonovanAgentMethods:
    """Tests for DonovanAgent instance methods using a mock provider."""

    def test_is_intermediate_planning_empty(self, agent):
        assert not agent._is_intermediate_planning("")

    def test_is_intermediate_planning_final_answer(self, agent):
        assert not agent._is_intermediate_planning("Here is the result of my search.")

    def test_is_intermediate_planning_step_prefix(self, agent):
        assert agent._is_intermediate_planning("Step 1: Search for images")

    def test_is_intermediate_planning_let_me(self, agent):
        assert agent._is_intermediate_planning("Let me now check the status")

    def test_is_intermediate_planning_only_first_line(self, agent):
        """Only first 100 chars of first line are checked."""
        assert not agent._is_intermediate_planning(
            "Here are the results.\nStep 1: This is on the second line."
        )

    def test_classify_intent_modify(self, agent):
        assert agent._classify_intent("write a new file") == "modify"
        assert agent._classify_intent("edit the config") == "modify"
        assert agent._classify_intent("update the code") == "modify"

    def test_classify_intent_search(self, agent):
        assert agent._classify_intent("search for python tutorials") == "search"
        assert agent._classify_intent("find information about") == "search"
        assert agent._classify_intent("lookup the documentation") == "search"
        assert agent._classify_intent("research the topic") == "search"
        assert agent._classify_intent("explore the new feature") == "search"

    def test_classify_intent_shell(self, agent):
        assert agent._classify_intent("run the tests") == "shell"
        assert agent._classify_intent("build the project") == "shell"
        assert agent._classify_intent("execute the script") == "shell"
        assert agent._classify_intent("compile the code") == "shell"
        assert agent._classify_intent("deploy to production") == "shell"

    def test_classify_intent_read(self, agent):
        assert agent._classify_intent("read the file") == "read"
        assert agent._classify_intent("what is this project") == "read"
        assert agent._classify_intent("how does this work") == "read"
        assert agent._classify_intent("explain the code") == "read"
        assert agent._classify_intent("show me the implementation") == "read"

    def test_classify_intent_default(self, agent):
        assert agent._classify_intent("hello") == "other"

    def test_classify_intent_prefers_modify_over_read(self, agent):
        """Modify takes priority due to frozenset ordering."""
        assert agent._classify_intent("edit and show me") == "modify"

    def test_is_task_complete_modify_without_mutation(self, agent):
        assert not agent._is_task_complete("modify", [], "I need to check something")

    def test_is_task_complete_modify_with_mutation(self, agent):
        assert agent._is_task_complete("modify", ["write_file"], "Done")

    def test_is_task_complete_search_without_search_tools(self, agent):
        # No tools called but answer is coherent — task is complete
        assert agent._is_task_complete("search", [], "Paris is the capital of France.")

    def test_is_task_complete_search_with_search_tools(self, agent):
        assert agent._is_task_complete("search", ["web_search"], "Results found")

    def test_is_task_complete_intermediate_planning_returns_false(self, agent):
        assert not agent._is_task_complete(
            "modify", ["write_file"], "Step 1: Let me also check"
        )

    def test_is_task_complete_empty_tool_names(self, agent):
        # Model can answer directly without tool calls
        assert agent._is_task_complete("read", [], "Here is the answer")

    def test_is_task_complete_shell_with_shell_tools(self, agent):
        assert agent._is_task_complete("shell", ["run_shell"], "Build succeeded")

    def test_is_task_complete_shell_without_shell_tools(self, agent):
        # Shell without shell tools but coherent answer is still complete
        assert agent._is_task_complete("shell", ["read_file"], "All good")

    def test_final_sanitize_via_agent(self, agent):
        text = (
            'text '
            '<tool_calls><invoke name="test"><parameter name="x">y</parameter></invoke></tool_calls>'
        )
        result = agent._final_sanitize(text)
        assert "<invoke" not in result
        assert "text" in result

    def test_final_sanitize_plain_text(self, agent):
        text = "Normal response."
        assert agent._final_sanitize(text) == text

    def test_final_sanitize_empty(self, agent):
        assert agent._final_sanitize("") == ""

    def test_final_sanitize_only_dsml(self, agent):
        text = '<tool_calls><invoke name="x"><parameter name="y">z</parameter></invoke></tool_calls>'
        assert agent._final_sanitize(text) == ""

    def test_final_sanitize_multiline_dsml(self, agent):
        text = (
            "Result here.\n"
            '<tool_calls>\n'
            '  <invoke name="mcp__x__y">\n'
            '    <parameter name="arg" string="true">val</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        result = agent._final_sanitize(text)
        assert "Result here" in result
        assert "<invoke" not in result
