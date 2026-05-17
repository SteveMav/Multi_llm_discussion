"""
Provider clients for MAS-D.

The orchestrator stays dependency-light by calling provider REST APIs with the
standard library. If a provider is unavailable or a key is missing, callers can
fall back to the local simulation path instead of breaking the whole debate.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class LLMClientError(RuntimeError):
    """Base exception for provider call failures."""


class MissingAPIKey(LLMClientError):
    """Raised when a provider call is requested without an API key."""


class UnsupportedProvider(LLMClientError):
    """Raised when no real client exists for the selected provider."""


@dataclass(frozen=True)
class ModelPreset:
    provider: str
    model: str
    label: str


OPENAI_MODEL_PRESETS = [
    ModelPreset("openai", "gpt-5.5", "GPT-5.5"),
    ModelPreset("openai", "gpt-5.4", "GPT-5.4"),
    ModelPreset("openai", "gpt-5.4-mini", "GPT-5.4 Mini"),
]

GEMINI_MODEL_PRESETS = [
    ModelPreset("gemini", "gemini-3-pro-preview", "Gemini 3 Pro"),
    ModelPreset("gemini", "gemini-3-flash-preview", "Gemini 3 Flash"),
    ModelPreset("gemini", "gemini-2.5-flash", "Gemini 2.5 Flash"),
]

MODEL_PRESETS = {
    "openai": OPENAI_MODEL_PRESETS,
    "gemini": GEMINI_MODEL_PRESETS,
}

DEFAULT_MODELS = {
    "openai": "gpt-5.5",
    "gemini": "gemini-3-pro-preview",
}

DEFAULT_MODERATOR = {
    "provider": "gemini",
    "model": "gemini-3-flash-preview",
}


async def generate_text(
    *,
    provider: str,
    model: str,
    api_key: str | None,
    instructions: str,
    prompt: str,
    max_tokens: int = 700,
    timeout: int = 90,
) -> str:
    """Generate text with the selected provider."""
    provider_key = provider.strip().lower()
    model_name = (model or DEFAULT_MODELS.get(provider_key) or "").strip()

    if not api_key:
        raise MissingAPIKey(f"Aucune clé API configurée pour {provider_key}.")

    if provider_key == "openai":
        return await asyncio.to_thread(
            _call_openai,
            api_key,
            model_name,
            instructions,
            prompt,
            max_tokens,
            timeout,
        )

    if provider_key == "gemini":
        return await asyncio.to_thread(
            _call_gemini,
            api_key,
            model_name,
            instructions,
            prompt,
            max_tokens,
            timeout,
        )

    raise UnsupportedProvider(
        f"Le provider {provider_key} n'a pas encore de client réel dans ce MVP."
    )


def _call_openai(
    api_key: str,
    model: str,
    instructions: str,
    prompt: str,
    max_tokens: int,
    timeout: int,
) -> str:
    body = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
        "max_output_tokens": max_tokens,
        "store": False,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    payload = _send_json(request, timeout)
    text = _extract_openai_text(payload)
    if not text:
        raise LLMClientError("OpenAI a répondu sans texte exploitable.")
    return text


def _call_gemini(
    api_key: str,
    model: str,
    instructions: str,
    prompt: str,
    max_tokens: int,
    timeout: int,
) -> str:
    model_id = model.removeprefix("models/")
    encoded_model = urllib.parse.quote(model_id, safe="")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{encoded_model}:generateContent"
    )
    body = {
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    payload = _send_json(request, timeout)
    text = _extract_gemini_text(payload)
    if not text:
        raise LLMClientError("Gemini a répondu sans texte exploitable.")
    return text


def _send_json(request: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMClientError(f"Erreur API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMClientError(f"Erreur réseau API: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMClientError("Réponse API JSON invalide.") from exc


def _extract_openai_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for output in payload.get("output", []):
        for content in output.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_gemini_text(payload: dict) -> str:
    chunks: list[str] = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def provider_model_options() -> dict[str, list[dict[str, str]]]:
    """Return serialisable provider/model presets for templates."""
    return {
        provider: [
            {"provider": preset.provider, "model": preset.model, "label": preset.label}
            for preset in presets
        ]
        for provider, presets in MODEL_PRESETS.items()
    }
