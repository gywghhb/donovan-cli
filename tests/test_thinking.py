from __future__ import annotations

from typing import Any

from donovanagent.thinking.manager import ThinkingManager


def _make_config(**kwargs: Any) -> Any:
    from donovanagent.config.schema import DonovanAgentConfig, ThinkingConfig
    config = DonovanAgentConfig()
    for k, v in kwargs.items():
        setattr(config.thinking, k, v)
    return config


def test_thinking_manager_enabled_by_default() -> None:
    config = _make_config()
    tm = ThinkingManager(config)
    assert tm.enabled is True


def test_thinking_manager_disabled() -> None:
    config = _make_config(enabled=False)
    tm = ThinkingManager(config)
    assert tm.enabled is True  # Default is True, configure hasn't been called explicitly
    tm.configure(config)
    assert tm.enabled is False


def test_should_show_summaries() -> None:
    config = _make_config(enabled=True, show_safe_summaries=True)
    tm = ThinkingManager(config)
    tm.configure(config)
    assert tm.should_show_summaries() is True

    config.thinking.show_safe_summaries = False
    tm.configure(config)
    assert tm.should_show_summaries() is False


def test_should_show_provider_reasoning() -> None:
    config = _make_config(enabled=True, show_provider_reasoning_if_available=True)
    tm = ThinkingManager(config)
    tm.configure(config)
    assert tm.should_show_provider_reasoning() is True

    config.thinking.show_provider_reasoning_if_available = False
    tm.configure(config)
    assert tm.should_show_provider_reasoning() is False


def test_get_summary_returns_string() -> None:
    config = _make_config()
    tm = ThinkingManager(config)
    tm.configure(config)
    summary = tm.get_summary("planning")
    assert len(summary) > 0


def test_get_summary_disabled_returns_empty() -> None:
    config = _make_config(enabled=False, show_safe_summaries=False)
    tm = ThinkingManager(config)
    tm.configure(config)
    assert tm.get_summary("planning") == ""


def test_get_summary_phases() -> None:
    config = _make_config()
    tm = ThinkingManager(config)
    tm.configure(config)
    for phase in ("planning", "analyzing", "searching", "reviewing_results", "formulating", "verifying"):
        s = tm.get_summary(phase)
        assert len(s) > 0, f"Empty summary for phase: {phase}"


def test_get_summary_unknown_phase_falls_back() -> None:
    config = _make_config()
    tm = ThinkingManager(config)
    tm.configure(config)
    summary = tm.get_summary("nonexistent_phase")
    assert summary == "Thinking..."


def test_render_status_enabled() -> None:
    config = _make_config()
    tm = ThinkingManager(config)
    text = tm.render_status("planning")
    assert text is not None
    assert "Thinking" in text


def test_render_status_disabled() -> None:
    config = _make_config(enabled=False)
    tm = ThinkingManager(config)
    tm.configure(config)
    text = tm.render_status("planning")
    assert text is None


def test_enabled_setter() -> None:
    config = _make_config()
    tm = ThinkingManager(config)
    tm.enabled = False
    assert tm.enabled is False
    tm.enabled = True
    assert tm.enabled is True
