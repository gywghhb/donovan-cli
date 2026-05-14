"""Extended tests for DSML parser, tool-name repair, and internal tool call extraction.

These tests cover additional edge cases beyond the basic tests in test_mcp.py:
- DSML parser: string="false" coercion, XML content preservation, arbitrary attributes
- Tool-name repair: camelCase, MCP separators, concatenated MCP names, ambiguity
- extract_internal_tool_calls: registered_names parameter, repaired output
"""

from __future__ import annotations

import pytest


# ===========================================================================
# _coerce_dsml_value unit tests
# ===========================================================================


class TestCoerceDsmlValue:
    """Tests for _coerce_dsml_value() type coercion."""

    def test_string_true_keeps_string(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value("hello", True) == "hello"
        assert _coerce_dsml_value("42", True) == "42"
        assert _coerce_dsml_value("true", True) == "true"

    def test_string_false_coerces_int(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value("42", False) == 42
        assert _coerce_dsml_value("0", False) == 0
        assert _coerce_dsml_value("-5", False) == -5

    def test_string_false_coerces_float(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value("3.14", False) == 3.14
        assert _coerce_dsml_value("0.5", False) == 0.5

    def test_string_false_coerces_bool(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value("true", False) is True
        assert _coerce_dsml_value("false", False) is False

    def test_string_false_coerces_json_array(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value('["a", "b"]', False) == ["a", "b"]

    def test_string_false_coerces_json_object(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value('{"key": "val"}', False) == {"key": "val"}

    def test_string_none_auto_coerces(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        # None is_string means auto-detect
        assert _coerce_dsml_value("42", None) == 42
        assert _coerce_dsml_value("hello", None) == "hello"
        assert _coerce_dsml_value("true", None) is True

    def test_string_false_fallback_to_string(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        # Can't coerce, fallback to raw string
        assert _coerce_dsml_value("not-a-number", False) == "not-a-number"

    def test_empty_string_with_coercion(self) -> None:
        from donovanagent.tools.mcp_tools import _coerce_dsml_value

        assert _coerce_dsml_value("", False) == ""
        assert _coerce_dsml_value("", True) == ""
        assert _coerce_dsml_value("", None) == ""


# ===========================================================================
# DSML parser edge cases
# ===========================================================================


class TestDsmlParserExtended:
    """Additional DSML parser tests beyond the basic ones in test_mcp.py."""

    def test_string_false_coerces_to_int(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="run_shell">\n'
            '    <parameter name="timeout" string="false">30</parameter>\n'
            '    <parameter name="command" string="true">ls -la</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["arguments"]["timeout"] == 30  # int, not "30"
        assert calls[0]["arguments"]["command"] == "ls -la"  # string

    def test_string_false_coerces_to_bool(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="web_search">\n'
            '    <parameter name="include_answer" string="false">true</parameter>\n'
            '    <parameter name="query" string="true">python</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["arguments"]["include_answer"] is True
        assert calls[0]["arguments"]["query"] == "python"

    def test_no_string_attribute_auto_coerces(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="test">\n'
            '    <parameter name="count">42</parameter>\n'
            '    <parameter name="name">hello</parameter>\n'
            '    <parameter name="enabled">true</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["arguments"]["count"] == 42
        assert calls[0]["arguments"]["name"] == "hello"
        assert calls[0]["arguments"]["enabled"] is True

    def test_xml_content_preserved_exactly(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        xml_content = (
            '<Desktop nodeId="abc">\n'
            '  <ComponentInstance position="absolute" width="100%" />\n'
            '</Desktop>'
        )
        text = (
            '<tool_calls>\n'
            '  <invoke name="mcp__framer__updateXmlForNode">\n'
            '    <parameter name="nodeId" string="true">abc</parameter>\n'
            f'    <parameter name="xml" string="true">{xml_content}</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["arguments"]["nodeId"] == "abc"
        assert '<Desktop nodeId="abc">' in calls[0]["arguments"]["xml"]
        assert "<ComponentInstance" in calls[0]["arguments"]["xml"]
        assert 'backgroundImage=' not in calls[0]["arguments"]["xml"]

    def test_multiple_calls_in_one_block(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="web_search">\n'
            '    <parameter name="query" string="true">python</parameter>\n'
            '  </invoke>\n'
            '  <invoke name="read_file">\n'
            '    <parameter name="path" string="true">test.py</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["name"] == "web_search"
        assert calls[1]["name"] == "read_file"

    def test_multiple_separate_invoke_blocks(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<invoke name="web_search">\n'
            '  <parameter name="query" string="true">hello</parameter>\n'
            '</invoke>\n'
            '<invoke name="read_file">\n'
            '  <parameter name="path" string="true">f.txt</parameter>\n'
            '</invoke>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 2

    def test_mcp_tool_name_with_double_underscore(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="mcp__github__list_issues">\n'
            '    <parameter name="repo" string="true">owner/repo</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcp__github__list_issues"

    def test_empty_parameter_value(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="test">\n'
            '    <parameter name="empty" string="true"></parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["arguments"]["empty"] == ""

    def test_no_parameters(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            '  <invoke name="list_tools">\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "list_tools"
        assert calls[0]["arguments"] == {}

    def test_text_before_and_after_dsml(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            "Let me search for that.\n"
            '<tool_calls>\n'
            '  <invoke name="web_search">\n'
            '    <parameter name="query" string="true">python</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>\n'
            "Here are the results."
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "web_search"

    def test_single_quote_attributes(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            "<tool_calls>\n"
            "<invoke name='test_tool'>\n"
            "<parameter name='arg1' string='true'>value1</parameter>\n"
            "</invoke>\n"
            "</tool_calls>"
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "test_tool"
        assert calls[0]["arguments"]["arg1"] == "value1"

    def test_mixed_quote_types(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls

        text = (
            '<tool_calls>\n'
            "<invoke name='test_tool'>\n"
            '    <parameter name="arg1" string="true">value1</parameter>\n'
            "    <parameter name='arg2' string='false'>42</parameter>\n"
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["arguments"]["arg1"] == "value1"
        assert calls[0]["arguments"]["arg2"] == 42


# ===========================================================================
# Tool-name repair tests
# ===========================================================================


class TestRepairToolName:
    """Tests for repair_tool_name() which attempts to fix malformed tool names."""

    def test_exact_match_returns_as_is(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("web_search", {"web_search", "read_file"})
        assert result == "web_search"

    def test_camel_to_snake_web_search(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("webSearch", {"web_search", "read_file"})
        assert result == "web_search"

    def test_camel_to_snake_read_file(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("readFile", {"read_file"})
        assert result == "read_file"

    def test_camel_to_snake_browser_open(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("browserOpen", {"browser_open"})
        assert result == "browser_open"

    def test_camel_to_snake_run_shell(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("runShell", {"run_shell"})
        assert result == "run_shell"

    def test_camel_to_snake_get_system_info(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("getSystemInfo", {"get_system_info"})
        assert result == "get_system_info"

    def test_mcp_single_underscore_to_double(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "mcp_framer_updateXmlForNode",
            {"mcp__framer__updateXmlForNode"},
        )
        assert result == "mcp__framer__updateXmlForNode"

    def test_mcp_hyphen_to_double_underscore(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "mcp-framer-updateXmlForNode",
            {"mcp__framer__updateXmlForNode"},
        )
        assert result == "mcp__framer__updateXmlForNode"

    def test_mcp_dot_to_double_underscore(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "mcp.framer.updateXmlForNode",
            {"mcp__framer__updateXmlForNode"},
        )
        assert result == "mcp__framer__updateXmlForNode"

    def test_concatenated_mcp_basic(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "mcpframerupdateXmlForNode",
            {"mcp__framer__updateXmlForNode"},
        )
        assert result == "mcp__framer__updateXmlForNode"

    def test_concatenated_mcp_with_different_tool(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "mcpframer_getProjectXml",
            {"mcp__framer__getProjectXml"},
        )
        assert result == "mcp__framer__getProjectXml"

    def test_no_match_returns_none(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("unknownTool", {"web_search", "read_file"})
        assert result is None

    def test_ambiguous_mcp_substring_returns_none(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        # Both contain "tool" in the name, so substring match is ambiguous
        result = repair_tool_name(
            "mcp__server__tool",
            {"mcp__server__tool_a", "mcp__server__tool_b"},
        )
        # Can't be sure which — but since it's exact double-underscore already, it either
        # matches exactly (it doesn't), or falls through to substring (multiple matches = None)
        assert result is None

    def test_non_mcp_name_ambiguous_rejection(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("doSomething", {"do_something_else"})
        assert result is None

    def test_mcp_substring_unique_match(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "updateXmlForNode",
            {"mcp__framer__updateXmlForNode", "mcp__framer__getProjectXml"},
        )
        assert result == "mcp__framer__updateXmlForNode"

    def test_already_snake_case_non_mcp(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name("web_search", {"web_search", "read_file"})
        assert result == "web_search"

    def test_mcp_single_underscore_prefix(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        result = repair_tool_name(
            "mcp_framer__updateXmlForNode",
            {"mcp__framer__updateXmlForNode"},
        )
        assert result == "mcp__framer__updateXmlForNode"

    def test_mcp_with_snake_case_tool_part(self) -> None:
        from donovanagent.tools.mcp_tools import repair_tool_name

        # Hyphen-separated MCP segments with snake_case tool
        result = repair_tool_name(
            "mcp-framer-list_issues",
            {"mcp__framer__list_issues"},
        )
        assert result == "mcp__framer__list_issues"


# ===========================================================================
# _repair_camel_to_snake tests
# ===========================================================================


class TestRepairCamelToSnake:
    """Tests for _repair_camel_to_snake() internal helper."""

    def test_simple_camel_case(self) -> None:
        from donovanagent.tools.mcp_tools import _repair_camel_to_snake

        assert _repair_camel_to_snake("webSearch") == "web_search"

    def test_multi_word(self) -> None:
        from donovanagent.tools.mcp_tools import _repair_camel_to_snake

        assert _repair_camel_to_snake("getSystemInfo") == "get_system_info"

    def test_already_snake(self) -> None:
        from donovanagent.tools.mcp_tools import _repair_camel_to_snake

        assert _repair_camel_to_snake("web_search") == "web_search"

    def test_single_word(self) -> None:
        from donovanagent.tools.mcp_tools import _repair_camel_to_snake

        assert _repair_camel_to_snake("help") == "help"

    def test_acronyms(self) -> None:
        from donovanagent.tools.mcp_tools import _repair_camel_to_snake

        assert _repair_camel_to_snake("parseJSON") == "parse_json"
        assert _repair_camel_to_snake("parseJSONString") == "parse_json_string"

    def test_empty_string(self) -> None:
        from donovanagent.tools.mcp_tools import _repair_camel_to_snake

        assert _repair_camel_to_snake("") == ""


# ===========================================================================
# extract_internal_tool_calls with registered_names
# ===========================================================================


class TestExtractInternalToolCallsExtended:
    """Extended tests for extract_internal_tool_calls with registered_names."""

    DSML_REGRESSION_TEXT = (
        'The image was uploaded. Let me center it.\n'
        '\n'
        '<tool_calls>\n'
        '<invoke name="mcpframerupdateXmlForNode">\n'
        '<parameter name="nodeId" string="true">WQLkyLRf1</parameter>\n'
        '<parameter name="xml" string="true"><Desktop nodeId="WQLkyLRf1">\n'
        '  <Image nodeId="yGlfpiD42" width="500px" />\n'
        '</Desktop></parameter>\n'
        '</invoke>\n'
        '</tool_calls>'
    )

    def test_registered_names_repairs_name(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        registered = {"mcp__framer__updateXmlForNode"}
        cleaned, calls = extract_internal_tool_calls(
            self.DSML_REGRESSION_TEXT, registered_names=registered,
        )
        assert len(calls) == 1
        assert calls[0]["name"] == "mcp__framer__updateXmlForNode"

    def test_registered_names_no_change_when_correct(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        registered = {"mcp__framer__updateXmlForNode"}
        text = (
            '<tool_calls>\n'
            '<invoke name="mcp__framer__updateXmlForNode">\n'
            '<parameter name="nodeId" string="true">abc</parameter>\n'
            '</invoke>\n'
            '</tool_calls>'
        )
        cleaned, calls = extract_internal_tool_calls(text, registered_names=registered)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcp__framer__updateXmlForNode"

    def test_registered_names_repairs_camel_to_snake(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        registered = {"web_search", "read_file"}
        text = (
            '<tool_calls>\n'
            '<invoke name="webSearch">\n'
            '<parameter name="query" string="true">python</parameter>\n'
            '</invoke>\n'
            '</tool_calls>'
        )
        cleaned, calls = extract_internal_tool_calls(text, registered_names=registered)
        assert len(calls) == 1
        assert calls[0]["name"] == "web_search"

    def test_cleaned_text_has_no_dsml(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        cleaned, calls = extract_internal_tool_calls(self.DSML_REGRESSION_TEXT)
        assert "<invoke" not in cleaned
        assert "<tool_calls>" not in cleaned
        assert "<parameter" not in cleaned

    def test_leading_text_present_in_cleaned(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        cleaned, calls = extract_internal_tool_calls(self.DSML_REGRESSION_TEXT)
        assert "image was uploaded" in cleaned
        assert "center it" in cleaned

    def test_no_dsml_returns_original(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        text = "Normal response with no markup."
        cleaned, calls = extract_internal_tool_calls(text)
        assert cleaned == text
        assert len(calls) == 0

    def test_empty_text(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        cleaned, calls = extract_internal_tool_calls("")
        assert cleaned == ""
        assert len(calls) == 0

    def test_registered_names_with_unknown_tool(self) -> None:
        """Unknown tool names should remain unrepaired but still be extracted."""
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        registered = {"web_search"}
        text = (
            '<tool_calls>\n'
            '<invoke name="unknownTool">\n'
            '<parameter name="x" string="true">y</parameter>\n'
            '</invoke>\n'
            '</tool_calls>'
        )
        cleaned, calls = extract_internal_tool_calls(text, registered_names=registered)
        assert len(calls) == 1
        # Name should remain unrepaired (no match found)
        assert calls[0]["name"] == "unknownTool"

    def test_registered_names_empty_set(self) -> None:
        """Empty registered names should not cause errors."""
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls

        text = (
            '<tool_calls>\n'
            '<invoke name="web_search">\n'
            '<parameter name="query" string="true">test</parameter>\n'
            '</invoke>\n'
            '</tool_calls>'
        )
        cleaned, calls = extract_internal_tool_calls(text, registered_names=set())
        assert len(calls) == 1
        assert calls[0]["name"] == "web_search"
