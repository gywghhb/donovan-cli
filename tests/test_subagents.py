from __future__ import annotations

from donovanagent.subagents.models import Subagent, SubagentRole
from donovanagent.subagents.roles import ROLE_PRESETS


def test_subagent_role_values() -> None:
    assert SubagentRole.RESEARCHER.value == "researcher"
    assert SubagentRole.CODER.value == "coder"
    assert SubagentRole.TESTER.value == "tester"
    assert SubagentRole.REVIEWER.value == "reviewer"
    assert SubagentRole.SAFETY.value == "safety"
    assert SubagentRole.PLANNER.value == "planner"
    assert SubagentRole.CUSTOM.value == "custom"


def test_subagent_defaults() -> None:
    agent = Subagent(name="test-agent", role=SubagentRole.RESEARCHER, prompt="research X")
    assert agent.status == "pending"
    assert agent.id == ""
    assert agent.allowed_tools == []


def test_subagent_with_tools() -> None:
    agent = Subagent(
        name="coder-1",
        role=SubagentRole.CODER,
        prompt="code Y",
        allowed_tools=["bash", "read", "write"],
    )
    assert len(agent.allowed_tools) == 3
    assert agent.result_summary is None
    assert agent.error is None


def test_subagent_lifecycle() -> None:
    agent = Subagent(name="test", role=SubagentRole.TESTER, prompt="test Z")
    assert agent.status == "pending"

    agent.status = "running"
    assert agent.status == "running"

    agent.status = "completed"
    agent.result_summary = "all tests passed"
    assert agent.result_summary == "all tests passed"


def test_role_presets_have_required_keys() -> None:
    required = {"name", "tools", "permissions", "description"}
    for role_key, preset in ROLE_PRESETS.items():
        assert required.issubset(preset.keys()), f"Role {role_key} missing keys: {required - preset.keys()}"
        assert isinstance(preset["tools"], list)
        assert isinstance(preset["description"], str)
        assert len(preset["description"]) > 0


def test_role_preset_researcher_no_write_tools() -> None:
    preset = ROLE_PRESETS[SubagentRole.RESEARCHER]
    write_tools = {"write_file", "patch_file", "delete"}
    assert not (write_tools & set(preset["tools"]))


def test_all_roles_have_unique_names() -> None:
    names = [p["name"] for p in ROLE_PRESETS.values()]
    assert len(names) == len(set(names)), "Role preset names must be unique"


def test_subagent_manager_initialization() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)
    assert manager is not None
    assert manager.can_spawn is True


def test_subagent_manager_create_and_list() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)

    agent = manager.create(
        role=SubagentRole.RESEARCHER,
        goal="find the answer",
        allowed_tools=["web_search"],
    )
    assert agent is not None
    assert agent.role == SubagentRole.RESEARCHER
    assert agent.status == "pending"

    agents = manager.list()
    assert any(a.id == agent.id for a in agents)


def test_subagent_lifecycle_methods() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)

    agent = manager.create(role=SubagentRole.REVIEWER, goal="review code", allowed_tools=["read"])

    # start() modifies in-place, returns None
    manager.start(agent.id)
    assert agent.status == "running"

    # complete() modifies in-place
    manager.complete(agent.id, result="looks good")
    assert agent.status == "completed"


def test_subagent_fail() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)

    agent = manager.create(role=SubagentRole.CODER, goal="write code", allowed_tools=["bash"])
    manager.start(agent.id)
    manager.fail(agent.id, error="syntax error")
    assert agent.status == "failed"
    assert agent.error == "syntax error"


def test_subagent_get() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)
    agent = manager.create(role=SubagentRole.CODER, goal="write", allowed_tools=["bash"])
    assert manager.get(agent.id) is agent
    assert manager.get("nonexistent") is None


def test_running_count() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)
    agent = manager.create(role=SubagentRole.CODER, goal="write", allowed_tools=["bash"])
    assert manager.running_count == 0
    manager.start(agent.id)
    assert manager.running_count == 1
    manager.complete(agent.id, result="done")
    assert manager.running_count == 0


def test_clear() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.subagents.manager import SubagentManager

    config = DonovanAgentConfig()
    manager = SubagentManager(config)
    manager.create(role=SubagentRole.CODER, goal="write", allowed_tools=["bash"])
    assert len(manager.list()) == 1
    manager.clear()
    assert len(manager.list()) == 0
