from __future__ import annotations

from donovanagent.agent.agent import DonovanAgent


def run_agent_turn(agent: DonovanAgent, session_id: str, prompt: str) -> str:
    return agent.run_turn(session_id, prompt)
