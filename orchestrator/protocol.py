"""
orchestrator.protocol
=====================
The 3-Phase State Machine — MAS-D Debate Protocol Engine.

Enforces the structured debate sequence:
  Phase 1: EXPOSITION    — Each agent presents their thesis (1 round)
  Phase 2: CONFRONTATION — Agents challenge weak points (configurable rounds)
  Phase 3: RESOLUTION    — Final synthesis, reverse speaking order (1 round)

Architecture Note: This module is PURE domain logic.  It must NOT import
anything from ``dashboard`` or Django's HTTP layer (no models, no views,
no request objects, no ORM).  The ``dashboard`` app imports *this* module —
never the reverse.  Only stdlib + ``orchestrator.genetic`` are permitted.
"""

import asyncio
import enum
from dataclasses import dataclass, field
from typing import AsyncGenerator

from orchestrator.genetic import get_archetype, get_moderator


# ────────────────────────────────────────────────────────────
#  Phase Enum
# ────────────────────────────────────────────────────────────

class DebatePhase(str, enum.Enum):
    """Debate phases as strings for serialisability."""
    EXPOSITION = "EXPOSITION"
    CONFRONTATION = "CONFRONTATION"
    RESOLUTION = "RESOLUTION"


# ────────────────────────────────────────────────────────────
#  State Dataclass
# ────────────────────────────────────────────────────────────

@dataclass
class ProtocolState:
    """Tracks the current state of the debate protocol state machine."""
    current_phase: DebatePhase
    round_num: int
    agents: list[dict]
    topic: str
    confrontation_rounds: int = 2

    # Internal tracking (not part of constructor signature externally)
    turn_index: int = field(default=0, init=False)
    speaking_order: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        # Initial speaking order: EXPOSITION uses slot_number ascending
        self.speaking_order = sorted(self.agents, key=lambda a: a.get("slot_number", 0))


# ────────────────────────────────────────────────────────────
#  Event Constructors (helpers for type-safe event creation)
# ────────────────────────────────────────────────────────────

def _system_event(content: str) -> dict:
    """Create a system-type event emitted by the moderator."""
    return {"type": "system", "agent_id": "moderator", "content": content}


def _thought_event(agent_id: str, label: str) -> dict:
    return {
        "type": "thought",
        "agent_id": agent_id,
        "content": f"[PLACEHOLDER] Agent {label} is thinking...",
    }


def _speech_event(agent_id: str, label: str, topic: str) -> dict:
    return {
        "type": "speech",
        "agent_id": agent_id,
        "content": f"[PLACEHOLDER] Agent {label} speaks on: {topic}",
    }


def _done_event() -> dict:
    return {
        "type": "done",
        "agent_id": "moderator",
        "content": "Debate protocol complete. Session concluded.",
    }


def _get_label(archetype_key: str) -> str:
    """Resolve human-readable label from archetype key; fall back gracefully."""
    arch = get_archetype(archetype_key)
    if arch:
        return arch.get("label", archetype_key)
    return archetype_key


# ────────────────────────────────────────────────────────────
#  Async Generator — Public API
# ────────────────────────────────────────────────────────────

async def run_protocol(
    agents: list[dict],
    topic: str,
    confrontation_rounds: int = 2,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that enforces the 3-phase MAS-D debate protocol.

    Yields SSE-ready event dicts matching the contract::

        {
            "type":     "system" | "thought" | "speech" |
                        "phase_transition" | "done" | "error",
            "agent_id": str,   # archetype key, e.g. "skeptic", or "moderator"
            "content":  str    # human-readable message
        }

    Parameters
    ----------
    agents:
        List of agent dicts each containing at minimum:
        ``{"provider": str, "archetype": str, "slot_number": int}``
    topic:
        The debate topic string.
    confrontation_rounds:
        Number of confrontation rounds (default 2).
    """
    # Sort agents by slot_number for canonical order
    ordered_agents = sorted(agents, key=lambda a: a.get("slot_number", 0))

    # ── PHASE 1: EXPOSITION ──────────────────────────────────
    yield _system_event("Phase 1: EXPOSITION — Each agent will present their thesis.")
    await asyncio.sleep(0)

    for agent in ordered_agents:
        archetype_key = agent.get("archetype", "")
        label = _get_label(archetype_key)

        yield _thought_event(archetype_key, label)
        await asyncio.sleep(0)

        yield _speech_event(archetype_key, label, topic)
        await asyncio.sleep(0)

    # ── PHASE 2: CONFRONTATION ───────────────────────────────
    for round_n in range(1, confrontation_rounds + 1):
        yield _system_event(
            f"Phase 2: CONFRONTATION — Round {round_n} of {confrontation_rounds}."
            " Challenge weak points."
        )
        await asyncio.sleep(0)

        # Simple rotation: each agent speaks once per confrontation round
        for agent in ordered_agents:
            archetype_key = agent.get("archetype", "")
            label = _get_label(archetype_key)

            yield _thought_event(archetype_key, label)
            await asyncio.sleep(0)

            yield _speech_event(archetype_key, label, topic)
            await asyncio.sleep(0)

    # ── PHASE 3: RESOLUTION ──────────────────────────────────
    yield _system_event(
        "Phase 3: RESOLUTION — Final synthesis from each agent."
    )
    await asyncio.sleep(0)

    # Reverse slot_number order: last to speak gets the final word first
    resolution_order = sorted(ordered_agents, key=lambda a: a.get("slot_number", 0), reverse=True)

    for agent in resolution_order:
        archetype_key = agent.get("archetype", "")
        label = _get_label(archetype_key)

        yield _thought_event(archetype_key, label)
        await asyncio.sleep(0)

        yield _speech_event(archetype_key, label, topic)
        await asyncio.sleep(0)

    # ── COMPLETION ───────────────────────────────────────────
    yield _done_event()
    await asyncio.sleep(0)
