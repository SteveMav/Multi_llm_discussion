"""
orchestrator.engine
===================
The Moderator Architect — Dynamic Prompt-Building & Rational Adherence Engine.

Wraps the 3-phase debate protocol (orchestrator.protocol) with Moderator
Architect logic:
  - ConcessionDetector    : bilingual detection of rational concessions in
                            agent speech
  - ModeratorPromptBuilder: builds the "Master System Prompt" injected by the
                            Moderator before each agent turn
  - prioritise_speaking_order: re-orders agents per phase and history
  - run_debate_engine     : high-level async generator façade over run_protocol

Architecture Note: This module is PURE domain logic.  It must NOT import
anything from ``dashboard`` or Django's HTTP/ORM layer (no models, no views,
no request objects).  The ``dashboard`` app imports *this* module — never the
reverse.  Only stdlib + ``orchestrator.*`` are permitted.
"""

import asyncio
import inspect
import re
from typing import AsyncGenerator

from orchestrator.genetic import get_archetype, get_moderator, get_system_prompt
from orchestrator.protocol import DebatePhase, run_protocol


# ────────────────────────────────────────────────────────────
#  Rational Adherence Preamble (hardcoded constant)
# ────────────────────────────────────────────────────────────

RATIONAL_ADHERENCE_PREAMBLE = (
    "RATIONAL ADHERENCE RULE (mandatory): You must engage with the intellectual "
    "content of arguments, not their rhetorical presentation. If an opponent has "
    "presented a logically superior argument that directly refutes your position, "
    "you MUST concede that specific point explicitly before presenting a counter-argument "
    "or shifting your position. Refusing to concede a clearly defeated argument is "
    "intellectual dishonesty and a protocol violation. Signal concession with phrases "
    "such as 'you have made a point', 'I concede', 'you are right on this', "
    "'I acknowledge', or 'point granted'."
)


# ────────────────────────────────────────────────────────────
#  ConcessionDetector
# ────────────────────────────────────────────────────────────

class ConcessionDetector:
    """
    Parses agent speech text to detect rational concessions.

    Concession phrases detected (case-insensitive, partial match):
      - "tu as marqué un point" / "you've made a point" / "you have a point"
      - "je concède" / "i concede"
      - "tu as raison" / "you're right" / "you are right"
      - "c'est un bon argument" / "that's a good argument" / "that is a good argument"
      - "j'admets" / "i admit"
      - "point accordé" / "point granted"
      - "je reconnais" / "i acknowledge"
    """

    # All phrases are lowercased for case-insensitive partial matching
    _CONCESSION_PHRASES: list[str] = [
        # French
        "tu as marqué un point",
        "je concède",
        "tu as raison",
        "c'est un bon argument",
        "j'admets",
        "point accordé",
        "je reconnais",
        # English
        "you've made a point",
        "you have made a point",
        "you have a point",
        "i concede",
        "you're right",
        "you are right",
        "that's a good argument",
        "that is a good argument",
        "i admit",
        "point granted",
        "i acknowledge",
    ]

    def detect(self, text: str) -> bool:
        """Return True if *text* contains any concession phrase (case-insensitive)."""
        lowered = text.lower()
        return any(phrase in lowered for phrase in self._CONCESSION_PHRASES)


# ────────────────────────────────────────────────────────────
#  ModeratorPromptBuilder
# ────────────────────────────────────────────────────────────

class ModeratorPromptBuilder:
    """
    Builds the "Master System Prompt" injected by the Moderator Architect
    before each agent's turn during CONFRONTATION or RESOLUTION phases.

    The Master Prompt:
    1. Includes the agent's own archetype system_prompt (from genetic.py)
    2. Appends the Rational Adherence rule preamble (hardcoded constant)
    3. Appends a summary of recent history (last N messages, configurable)
    4. Appends speaking order context ("You are responding to: <label>")
    """

    def build(
        self,
        archetype_key: str,
        history: list[dict],
        previous_speaker_id: str | None,
        phase: DebatePhase,
        history_window: int = 6,
    ) -> str:
        """
        Build and return the Master System Prompt string.

        Parameters
        ----------
        archetype_key:
            The agent's archetype key (e.g. "skeptic").
        history:
            Full conversation history list — each entry is a message dict.
        previous_speaker_id:
            Archetype key of the previous speaker, or None.
        phase:
            Current DebatePhase enum value.
        history_window:
            Number of most-recent history messages to include (default 6).
        """
        parts: list[str] = []

        # 1. Archetype's own system prompt
        arch = get_archetype(archetype_key)
        if arch:
            parts.append(arch["system_prompt"])
        else:
            parts.append(f"You are agent '{archetype_key}'.")

        # 2. Rational Adherence preamble (always injected)
        parts.append("\n\n" + RATIONAL_ADHERENCE_PREAMBLE)

        # 3. Summary of recent history
        recent = history[-history_window:] if history else []
        if recent:
            history_lines = []
            for msg in recent:
                agent_id = msg.get("agent_id", "unknown")
                content = msg.get("content", "")
                history_lines.append(f"  [{agent_id}]: {content}")
            parts.append(
                "\n\nRECENT DEBATE HISTORY (last {} messages):\n{}".format(
                    len(recent), "\n".join(history_lines)
                )
            )

        # 4. Speaking order / challenger context
        if previous_speaker_id:
            prev_arch = get_archetype(previous_speaker_id)
            prev_label = prev_arch["label"] if prev_arch else previous_speaker_id
            parts.append(
                f"\n\nYou are responding to: {prev_label} ({previous_speaker_id}). "
                "Address their most recent argument directly."
            )

        return "".join(parts)


# ────────────────────────────────────────────────────────────
#  SpeakingOrderPrioritiser
# ────────────────────────────────────────────────────────────

def prioritise_speaking_order(
    agents: list[dict],
    history: list[dict],
    current_phase: DebatePhase,
) -> list[dict]:
    """
    Determine the speaking order for the current turn.

    Rules
    -----
    EXPOSITION:
        Ascending slot_number (unchanged from protocol.py default).
    CONFRONTATION:
        The agent most recently *challenged* — i.e. the agent who spoke
        immediately BEFORE the last speaker in history — moves to front.
        If history has fewer than 2 entries, fall back to slot_number ascending.
    RESOLUTION:
        Reverse slot_number order (mirrors protocol.py resolution phase).

    Returns a re-ordered copy of *agents* (original list is not mutated).
    """
    agents_copy = list(agents)

    if current_phase == DebatePhase.EXPOSITION:
        return sorted(agents_copy, key=lambda a: a.get("slot_number", 0))

    if current_phase == DebatePhase.RESOLUTION:
        return sorted(agents_copy, key=lambda a: a.get("slot_number", 0), reverse=True)

    # CONFRONTATION — prioritise the challenged agent
    if current_phase == DebatePhase.CONFRONTATION:
        if len(history) < 2:
            # Not enough history — fall back to slot ascending
            return sorted(agents_copy, key=lambda a: a.get("slot_number", 0))

        # The agent who was challenged = the agent who spoke second-to-last
        challenged_id = history[-2].get("agent_id")
        # Move the challenged agent to the front
        challenged = [a for a in agents_copy if a.get("archetype") == challenged_id]
        others = [a for a in agents_copy if a.get("archetype") != challenged_id]
        others_sorted = sorted(others, key=lambda a: a.get("slot_number", 0))
        return challenged + others_sorted

    # Fallback (should not reach here)
    return sorted(agents_copy, key=lambda a: a.get("slot_number", 0))


# ────────────────────────────────────────────────────────────
#  Main Engine Façade — run_debate_engine
# ────────────────────────────────────────────────────────────

async def run_debate_engine(
    agents: list[dict],
    topic: str,
    confrontation_rounds: int = 2,
    session_id: int | None = None,
) -> AsyncGenerator[dict, None]:
    """
    High-level async generator that wraps ``run_protocol()`` and applies
    Moderator Architect logic at each agent turn.

    For each event yielded by ``run_protocol()``:
    - If type is ``"thought"`` or ``"speech"`` (an agent turn):
        1. Build the Master Prompt via ``ModeratorPromptBuilder``
        2. Yield a ``"system"`` event with a 1-line Master Prompt summary
        3. Yield the original placeholder thought/speech event
        4. For speech events: detect concession via ``ConcessionDetector``
        5. If concession detected: yield a ``"system"`` event announcing it
        6. For speech events: append to conversation history
    - If type is ``"system"`` or ``"done"``: yield as-is
    - Every yield is followed by ``await asyncio.sleep(0)``

    Parameters
    ----------
    agents:
        List of agent dicts — each must contain at minimum
        ``{"provider": str, "archetype": str, "slot_number": int}``.
    topic:
        The debate topic string.
    confrontation_rounds:
        Number of confrontation rounds (default 2).
    """
    detector = ConcessionDetector()
    builder = ModeratorPromptBuilder()

    # Conversation history — accumulated as the debate progresses
    history: list[dict] = []
    previous_speaker_id: str | None = None

    from orchestrator.safety import get_abort_event, clear_abort_event
    abort_event = get_abort_event(session_id) if session_id is not None else None

    try:
        async for event in run_protocol(agents, topic, confrontation_rounds):
            # Always yield an ABORTED error event if the kill switch was triggered
            if abort_event and abort_event.is_set():
                yield {
                    "type": "error",
                    "agent_id": "system",
                    "content": "Session ABORTED due to Kill-Switch trigger."
                }
                break

            event_type = event.get("type")
            agent_id = event.get("agent_id", "")

            if event_type in ("thought", "speech"):
                # Determine current phase from the history of system events
                # (we infer phase from the last system event hint)
                current_phase = _infer_phase(history)

                # 1. Build Master Prompt summary (1-line for SSE readability)
                master_prompt = builder.build(
                    archetype_key=agent_id,
                    history=history,
                    previous_speaker_id=previous_speaker_id,
                    phase=current_phase,
                )
                prompt_summary = (
                    f"[Moderator] Master Prompt active for {agent_id} "
                    f"({current_phase.value}) — {len(master_prompt)} chars. "
                    "Rational Adherence rule injected."
                )

                # 2. Yield system event with prompt summary
                yield {
                    "type": "system",
                    "agent_id": "moderator",
                    "content": prompt_summary,
                }
                await asyncio.sleep(0)

                # 3. Yield original event
                yield event
                await asyncio.sleep(0)

                # 4–6. Only for speech events
                if event_type == "speech":
                    content = event.get("content", "")

                    # 4. Detect concession
                    if detector.detect(content):
                        # 5. Yield concession notice
                        yield {
                            "type": "system",
                            "agent_id": "moderator",
                            "content": (
                                f"[Moderator] Rational Adherence detected: "
                                f"agent '{agent_id}' has issued a concession."
                            ),
                        }
                        await asyncio.sleep(0)

                    # 6. Append speech to history
                    history.append({
                        "agent_id": agent_id,
                        "role": "agent",
                        "content": content,
                        "phase": current_phase.value,
                        "round_num": _count_phase_rounds(history, current_phase),
                    })
                    previous_speaker_id = agent_id

            else:
                # "system", "phase_transition", "done", "error" — pass through
                yield event
                await asyncio.sleep(0)
    finally:
        if session_id is not None:
            clear_abort_event(session_id)


# ────────────────────────────────────────────────────────────
#  Internal helpers (not part of public API)
# ────────────────────────────────────────────────────────────

def _infer_phase(history: list[dict]) -> DebatePhase:
    """
    Infer the current DebatePhase from the accumulated conversation history.

    Uses the ``phase`` field of the most recent history entry as a proxy.
    Falls back to EXPOSITION when history is empty.
    """
    if not history:
        return DebatePhase.EXPOSITION
    last_phase_str = history[-1].get("phase", DebatePhase.EXPOSITION.value)
    try:
        return DebatePhase(last_phase_str)
    except ValueError:
        return DebatePhase.EXPOSITION


def _count_phase_rounds(history: list[dict], phase: DebatePhase) -> int:
    """Return how many speech events have been recorded for *phase* so far."""
    return sum(1 for m in history if m.get("phase") == phase.value) + 1
