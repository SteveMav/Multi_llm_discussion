"""
MAS-D debate engine.

This module owns the live multi-agent discussion loop. It stays independent
from Django: the view layer supplies agents, API keys, model names, topic and
axes, then this generator yields SSE-ready event dictionaries.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from orchestrator.genetic import get_archetype, get_moderator
from orchestrator.llm_clients import (
    DEFAULT_MODERATOR,
    LLMClientError,
    generate_text,
)
from orchestrator.protocol import DebatePhase


RATIONAL_ADHERENCE_PREAMBLE = (
    "RATIONAL ADHERENCE / Règle d'adhésion rationnelle: réponds au meilleur argument adverse, "
    "concède explicitement un point quand il est plus solide que ta position, "
    "puis propose une version améliorée. Pas de posture creuse, pas de liste "
    "interminable, pas de citation inventée."
)


PHASE_LABELS = {
    DebatePhase.EXPOSITION: "Exposition",
    DebatePhase.CONFRONTATION: "Confrontation",
    DebatePhase.RESOLUTION: "Résolution",
}


class ConcessionDetector:
    """Small public helper kept for tests and future moderator audits."""

    _PHRASES = [
        "tu as marqué un point",
        "je concède",
        "tu as raison",
        "c'est un bon argument",
        "j'admets",
        "point accordé",
        "je reconnais",
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
        lowered = text.lower()
        return any(phrase in lowered for phrase in self._PHRASES)


class ModeratorPromptBuilder:
    """Compatibility prompt builder used by legacy tests and experiments."""

    def build(
        self,
        archetype_key: str,
        history: list[dict],
        previous_speaker_id: str | None,
        phase: DebatePhase,
        history_window: int = 6,
    ) -> str:
        arch = get_archetype(archetype_key)
        parts = [arch["system_prompt"] if arch else f"You are agent '{archetype_key}'."]
        parts.append("\n\n" + RATIONAL_ADHERENCE_PREAMBLE)
        recent = history[-history_window:] if history else []
        if recent:
            lines = [
                f"  [{msg.get('agent_id', 'unknown')}]: {msg.get('content', '')}"
                for msg in recent
            ]
            parts.append(f"\n\nRECENT DEBATE HISTORY ({phase.value}):\n" + "\n".join(lines))
        if previous_speaker_id:
            prev_arch = get_archetype(previous_speaker_id)
            prev_label = prev_arch["label"] if prev_arch else previous_speaker_id
            parts.append(
                f"\n\nYou are responding to: {prev_label} ({previous_speaker_id})."
            )
        return "".join(parts)


def prioritise_speaking_order(
    agents: list[dict],
    history: list[dict],
    current_phase: DebatePhase,
) -> list[dict]:
    """Return the phase-specific speaking order."""
    agents_copy = list(agents)
    if current_phase == DebatePhase.RESOLUTION:
        return sorted(agents_copy, key=lambda a: a.get("slot_number", 0), reverse=True)
    if current_phase == DebatePhase.CONFRONTATION and len(history) >= 2:
        challenged_id = history[-2].get("agent_id")
        challenged = [a for a in agents_copy if a.get("archetype") == challenged_id]
        others = [a for a in agents_copy if a.get("archetype") != challenged_id]
        return challenged + sorted(others, key=lambda a: a.get("slot_number", 0))
    return sorted(agents_copy, key=lambda a: a.get("slot_number", 0))


async def run_debate_engine(
    agents: list[dict],
    topic: str,
    axes: str | int = "",
    moderator: dict | None = None,
    confrontation_rounds: int = 1,
    session_id: int | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Run a compact, useful debate between the selected agents.

    Each agent dict should contain:
    ``provider``, ``model``, ``api_key``, ``archetype`` and ``slot_number``.
    Missing API support never crashes the debate; the engine emits a system
    notice and uses the local simulation answer for that turn.
    """
    if isinstance(axes, int):
        confrontation_rounds = axes
        axes = ""

    ordered_agents = sorted(agents, key=lambda a: a.get("slot_number", 0))
    moderator_config = moderator or {}
    moderator_agent = {
        "provider": moderator_config.get("provider") or DEFAULT_MODERATOR["provider"],
        "model": moderator_config.get("model") or DEFAULT_MODERATOR["model"],
        "api_key": moderator_config.get("api_key"),
        "archetype": "moderator",
        "slot_number": 0,
    }

    from orchestrator.safety import clear_abort_event, get_abort_event

    abort_event = get_abort_event(session_id) if session_id is not None else None
    history: list[dict] = []
    warned_fallbacks: set[tuple[str, str]] = set()

    try:
        yield _system_event(
            "Débat armé: "
            f"{len(ordered_agents)} agent(s), modérateur {moderator_agent['provider']}/"
            f"{moderator_agent['model']}."
        )
        if axes.strip():
            yield _system_event(f"Axes de discussion: {_compact_axes(axes)}")

        await _maybe_sleep()

        yield _system_event("Phase 1: EXPOSITION - chaque modèle pose sa lecture du sujet.")
        async for event in _run_agent_round(
            ordered_agents,
            topic,
            axes,
            DebatePhase.EXPOSITION,
            "Présente ta thèse initiale. Sois clair, concret et borné aux axes.",
            history,
            warned_fallbacks,
            abort_event,
        ):
            yield event
        async for event in _moderator_turn(
            moderator_agent,
            topic,
            axes,
            DebatePhase.EXPOSITION,
            "Résume les positions, nomme les premiers désaccords utiles et annonce la confrontation.",
            history,
            warned_fallbacks,
            abort_event,
        ):
            yield event

        for round_number in range(1, confrontation_rounds + 1):
            yield _system_event(
                f"Phase 2: CONFRONTATION - tour {round_number}/{confrontation_rounds}."
            )
            async for event in _run_agent_round(
                ordered_agents,
                topic,
                axes,
                DebatePhase.CONFRONTATION,
                (
                    "Critique l'argument le plus fragile d'un autre agent, puis propose "
                    "une amélioration praticable. Conserve une phrase de concession si "
                    "un point adverse est valable."
                ),
                history,
                warned_fallbacks,
                abort_event,
            ):
                yield event
            async for event in _moderator_turn(
                moderator_agent,
                topic,
                axes,
                DebatePhase.CONFRONTATION,
                "Identifie le noeud du désaccord, tranche ce qui est faible, garde ce qui est exploitable.",
                history,
                warned_fallbacks,
                abort_event,
            ):
                yield event

        yield _system_event("Phase 3: RÉSOLUTION - synthèse courte et décision exploitable.")
        async for event in _run_agent_round(
            list(reversed(ordered_agents)),
            topic,
            axes,
            DebatePhase.RESOLUTION,
            (
                "Donne ta position finale: ce que tu gardes, ce que tu abandonnes, "
                "et la prochaine action concrète."
            ),
            history,
            warned_fallbacks,
            abort_event,
        ):
            yield event
        async for event in _moderator_turn(
            moderator_agent,
            topic,
            axes,
            DebatePhase.RESOLUTION,
            "Produis le verdict final: consensus, tensions restantes, décision et trois prochaines actions.",
            history,
            warned_fallbacks,
            abort_event,
            final=True,
        ):
            yield event

        yield {
            "type": "done",
            "agent_id": "moderator",
            "content": "Débat terminé. Rapport disponible.",
        }
    finally:
        if session_id is not None:
            clear_abort_event(session_id)


async def _run_agent_round(
    agents: list[dict],
    topic: str,
    axes: str,
    phase: DebatePhase,
    objective: str,
    history: list[dict],
    warned_fallbacks: set[tuple[str, str]],
    abort_event: asyncio.Event | None,
) -> AsyncGenerator[dict, None]:
    for agent in agents:
        if abort_event and abort_event.is_set():
            yield _abort_event()
            return

        archetype = agent.get("archetype", "")
        arch = get_archetype(archetype) or {}
        label = arch.get("label", archetype)
        provider = agent.get("provider", "simulation")
        model = agent.get("model", "local-simulation")

        yield {
            "type": "thought",
            "agent_id": archetype,
            "content": f"{label} prépare une réponse ({provider}/{model}).",
            "phase": phase.value,
            "provider": provider,
            "model": model,
        }
        await _maybe_sleep()

        instructions = _agent_instructions(archetype)
        prompt = _turn_prompt(
            topic=topic,
            axes=axes,
            phase=phase,
            objective=objective,
            history=history,
            speaker_label=label,
        )
        text = await _safe_generate(
            agent,
            instructions,
            prompt,
            topic,
            axes,
            phase,
            history,
            warned_fallbacks,
        )

        event = {
            "type": "speech",
            "agent_id": archetype,
            "content": text,
            "phase": phase.value,
            "provider": provider,
            "model": model,
        }
        history.append(_history_item(archetype, label, provider, model, phase, text))
        yield event
        await _maybe_sleep()


async def _moderator_turn(
    moderator_agent: dict,
    topic: str,
    axes: str,
    phase: DebatePhase,
    objective: str,
    history: list[dict],
    warned_fallbacks: set[tuple[str, str]],
    abort_event: asyncio.Event | None,
    final: bool = False,
) -> AsyncGenerator[dict, None]:
    if abort_event and abort_event.is_set():
        yield _abort_event()
        return

    provider = moderator_agent.get("provider", "gemini")
    model = moderator_agent.get("model", "local-simulation")

    yield {
        "type": "thought",
        "agent_id": "moderator",
        "content": f"Le modérateur organise la phase {PHASE_LABELS[phase].lower()} ({provider}/{model}).",
        "phase": phase.value,
        "provider": provider,
        "model": model,
    }
    await _maybe_sleep()

    text = await _safe_generate(
        moderator_agent,
        _moderator_instructions(),
        _turn_prompt(
            topic=topic,
            axes=axes,
            phase=phase,
            objective=objective,
            history=history,
            speaker_label="Modérateur Architecte",
            final=final,
        ),
        topic,
        axes,
        phase,
        history,
        warned_fallbacks,
        is_moderator=True,
    )
    history.append(_history_item("moderator", "Modérateur Architecte", provider, model, phase, text))
    yield {
        "type": "speech",
        "agent_id": "moderator",
        "content": text,
        "phase": phase.value,
        "provider": provider,
        "model": model,
    }
    await _maybe_sleep()


async def _safe_generate(
    agent: dict,
    instructions: str,
    prompt: str,
    topic: str,
    axes: str,
    phase: DebatePhase,
    history: list[dict],
    warned_fallbacks: set[tuple[str, str]],
    is_moderator: bool = False,
) -> str:
    provider = agent.get("provider", "simulation")
    model = agent.get("model", "local-simulation")
    key = (provider, model)
    try:
        return await generate_text(
            provider=provider,
            model=model,
            api_key=agent.get("api_key"),
            instructions=instructions,
            prompt=prompt,
        )
    except LLMClientError as exc:
        if key not in warned_fallbacks:
            warned_fallbacks.add(key)
            # The caller cannot yield from here, so the fallback note is folded
            # into the simulated text for the first affected turn.
            prefix = f"[Mode simulation: {exc}] "
        else:
            prefix = ""
        if is_moderator:
            return prefix + _simulate_moderator_reply(topic, axes, phase, history)
        return prefix + _simulate_agent_reply(agent, topic, axes, phase, history)


def _agent_instructions(archetype_key: str) -> str:
    arch = get_archetype(archetype_key) or {}
    base = arch.get("system_prompt", f"Tu es l'agent {archetype_key}.")
    return "\n\n".join(
        [
            base,
            RATIONAL_ADHERENCE_PREAMBLE,
            (
                "Réponds en français naturel. Vise 120 à 180 mots. "
                "Ne révèle pas de raisonnement interne caché. Donne seulement "
                "la position publique de l'agent."
            ),
        ]
    )


def _moderator_instructions() -> str:
    return "\n\n".join(
        [
            get_moderator()["system_prompt"],
            (
                "Réponds en français naturel. Tu animes le débat: tu reformules, "
                "forces la clarté, évites les boucles et transformes la discussion "
                "en décision exploitable. Ne produis pas de pensée interne."
            ),
        ]
    )


def _turn_prompt(
    *,
    topic: str,
    axes: str,
    phase: DebatePhase,
    objective: str,
    history: list[dict],
    speaker_label: str,
    final: bool = False,
) -> str:
    recent = "\n".join(
        f"- {item['label']} ({item['provider']}/{item['model']}): {item['content']}"
        for item in history[-8:]
    )
    final_hint = (
        "\nFormat final attendu: Verdict, Décision, Prochaines actions."
        if final
        else ""
    )
    axes_block = axes.strip() or "Aucun axe imposé; choisis les angles les plus utiles."
    return (
        f"Sujet de discussion:\n{topic.strip()}\n\n"
        f"Axes à couvrir:\n{axes_block}\n\n"
        f"Phase: {PHASE_LABELS[phase]}\n"
        f"Intervenant: {speaker_label}\n"
        f"Objectif du tour: {objective}{final_hint}\n\n"
        f"Historique récent:\n{recent if recent else 'Aucun échange précédent.'}\n\n"
        "Contraintes: sois précis, réponds aux autres quand l'historique existe, "
        "évite le jargon gratuit, termine par une idée actionnable."
    )


def _simulate_agent_reply(
    agent: dict,
    topic: str,
    axes: str,
    phase: DebatePhase,
    history: list[dict],
) -> str:
    archetype = agent.get("archetype", "agent")
    arch = get_archetype(archetype) or {"label": archetype}
    label = arch["label"]
    axis = _first_axis(axes)
    phase_name = PHASE_LABELS[phase].lower()

    openings = {
        "skeptic": (
            "Je pars d'une réserve: l'idée n'est solide que si elle survit à "
            "ses contraintes les moins confortables."
        ),
        "optimist": (
            "Je vois une opportunité réelle si l'on transforme le sujet en "
            "expérience progressive plutôt qu'en grand pari abstrait."
        ),
        "pragmatist": (
            "Je ramène la discussion au terrain: délais, coûts, responsabilités "
            "et capacité à vérifier les résultats."
        ),
        "conservative": (
            "Je protège la stabilité du système existant: toute nouveauté doit "
            "prouver qu'elle réduit le risque au lieu de le déplacer."
        ),
        "innovator": (
            "Je veux pousser le cadre: le débat peut produire une option plus "
            "simple et plus audacieuse que les compromis habituels."
        ),
    }
    challenge = ""
    if history and phase != DebatePhase.EXPOSITION:
        last = history[-1]
        challenge = (
            f" Je reconnais le point de {last['label']}, mais je le rendrais "
            "plus testable avant d'en faire une décision."
        )

    return (
        f"{openings.get(archetype, label + ' prend position.')} "
        f"Sur « {topic.strip()} », mon angle principal reste {axis}. "
        f"En phase de {phase_name}, la bonne sortie est une décision courte: "
        "ce que l'on teste, avec qui, et quel signal dira que l'hypothèse tient."
        f"{challenge} Prochaine action: formuler une hypothèse mesurable et "
        "la confronter à une contrainte réelle avant d'élargir le débat."
    )


def _simulate_moderator_reply(
    topic: str,
    axes: str,
    phase: DebatePhase,
    history: list[dict],
) -> str:
    axis = _first_axis(axes)
    if phase == DebatePhase.RESOLUTION:
        return (
            f"Verdict: le débat sur « {topic.strip()} » converge vers un test "
            f"progressif centré sur {axis}. Décision: garder les idées qui "
            "peuvent être vérifiées vite et écarter les promesses trop générales. "
            "Prochaines actions: définir un cas d'usage, nommer deux contraintes "
            "terrain, puis lancer une première itération mesurable."
        )
    speakers = ", ".join(item["label"] for item in history[-3:]) or "les agents"
    return (
        f"Synthèse: {speakers} font apparaître un désaccord utile autour de {axis}. "
        "Je garde les arguments qui indiquent une preuve observable et je bloque "
        "les positions qui restent trop générales. La suite doit opposer faisabilité, "
        "risque et valeur concrète."
    )


def _history_item(
    agent_id: str,
    label: str,
    provider: str,
    model: str,
    phase: DebatePhase,
    content: str,
) -> dict:
    return {
        "agent_id": agent_id,
        "label": label,
        "provider": provider,
        "model": model,
        "phase": phase.value,
        "content": content,
    }


def _system_event(content: str) -> dict:
    return {"type": "system", "agent_id": "moderator", "content": content}


def _abort_event() -> dict:
    return {
        "type": "error",
        "agent_id": "system",
        "content": "Session interrompue par le Kill-Switch.",
    }


def _compact_axes(axes: str) -> str:
    lines = [line.strip(" -\t") for line in axes.splitlines() if line.strip()]
    return " | ".join(lines) if lines else axes.strip()


def _first_axis(axes: str) -> str:
    for line in axes.splitlines():
        cleaned = line.strip(" -\t")
        if cleaned:
            return cleaned.lower()
    return "la faisabilité concrète"


async def _maybe_sleep() -> None:
    await asyncio.sleep(0)
