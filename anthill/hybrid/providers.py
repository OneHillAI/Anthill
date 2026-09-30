from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..inference.base import BackendError, Message


@dataclass
class CloudProvider:
    """An OpenAI-compatible cloud endpoint the org can pay to use on escalation.

    NOTE: base URLs and prices are sensible defaults as of this writing but
    change often - verify against the provider's docs and override via the
    dashboard/settings. price_* are USD per 1,000 tokens.
    """

    name: str
    base_url: str
    default_model: str
    api_key_env: str
    price_in_per_1k: float = 0.0  # approximate; configurable
    price_out_per_1k: float = 0.0
    note: str = ""
    retention: str = ""  # data-retention posture, shown in the UI


# OpenAI-compatible providers. OpenRouter is the simplest "pay per token across
# many models (incl. Kimi)" option; the others are direct.
PROVIDERS: dict[str, CloudProvider] = {
    "openrouter": CloudProvider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "moonshotai/kimi-k2",
        "OPENROUTER_API_KEY",
        price_in_per_1k=0.0006,
        price_out_per_1k=0.0025,
        note="Aggregator - one key, many models incl. Kimi/DeepSeek/Qwen/Claude. Prices vary per model.",
        retention="Varies by upstream model; OpenRouter does not train on traffic. Check per-model.",
    ),
    "anthropic": CloudProvider(
        "anthropic",
        "https://api.anthropic.com/v1",
        "claude-sonnet-4-5",
        "ANTHROPIC_API_KEY",
        price_in_per_1k=0.003,
        price_out_per_1k=0.015,
        note="Claude via Anthropic's OpenAI-compatibility layer. Anthropic flags it as "
        "test-oriented; for full features use the native API. Strong privacy posture.",
        retention="API not used for training; ~7-day retention. Zero-Data-Retention available (enterprise).",
    ),
    "moonshot": CloudProvider(
        "moonshot",
        "https://api.moonshot.ai/v1",
        "kimi-k2-0711-preview",
        "MOONSHOT_API_KEY",
        price_in_per_1k=0.0006,
        price_out_per_1k=0.0025,
        note="Moonshot AI (Kimi) direct. Use api.moonshot.cn for the China endpoint.",
        retention="Check Moonshot's current API terms before sending sensitive data.",
    ),
    "deepseek": CloudProvider(
        "deepseek",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
        "DEEPSEEK_API_KEY",
        price_in_per_1k=0.0003,
        price_out_per_1k=0.0011,
        note="DeepSeek direct. deepseek-reasoner for the reasoning model.",
        retention="Check DeepSeek's current API terms before sending sensitive data.",
    ),
    "together": CloudProvider(
        "together",
        "https://api.together.xyz/v1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
        "TOGETHER_API_KEY",
        price_in_per_1k=0.0012,
        price_out_per_1k=0.0012,
        note="Together AI - hosted open-weight models.",
        retention="Together does not train on API data; check current terms.",
    ),
    "huggingface": CloudProvider(
        "huggingface",
        "https://router.huggingface.co/v1",
        "meta-llama/Llama-3.3-70B-Instruct",
        "HF_TOKEN",
        price_in_per_1k=0.0009,
        price_out_per_1k=0.0009,
        note="Hugging Face Inference router - open-weight models, OpenAI-compatible. "
        "Pricing/availability vary by upstream provider.",
        retention="Routes to third-party inference providers; retention varies by route. Check per-model.",
    ),
}


@dataclass
class CloudResult:
    answer: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


def call_cloud(
    provider: CloudProvider,
    messages: list[Message],
    *,
    api_key: str,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> CloudResult:
    """Call an OpenAI-compatible cloud endpoint and capture token usage + cost."""
    if not api_key:
        raise BackendError(
            f"No API key for {provider.name}. Set {provider.api_key_env} "
            "or configure it in the dashboard."
        )
    use_model = model or provider.default_model
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # OpenRouter recommends these identifying headers; harmless elsewhere.
    headers.setdefault("HTTP-Referer", "https://github.com/anthill")
    headers.setdefault("X-Title", "anthill")

    payload = {
        "model": use_model,
        "messages": [m.as_dict() for m in messages],
        "stream": False,
        "temperature": temperature,
    }
    try:
        resp = httpx.post(
            f"{provider.base_url}/chat/completions", json=payload, headers=headers, timeout=timeout
        )
        resp.raise_for_status()
    except httpx.ConnectError as e:
        raise BackendError(f"Can't reach {provider.name} at {provider.base_url}.") from e
    except httpx.HTTPStatusError as e:
        raise BackendError(
            f"{provider.name} returned {e.response.status_code}: {e.response.text[:200]}"
        ) from e

    data = resp.json()
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise BackendError(f"Unexpected response shape from {provider.name}.") from e

    usage = data.get("usage", {}) or {}
    pin = int(usage.get("prompt_tokens", 0))
    pout = int(usage.get("completion_tokens", 0))
    cost = round(pin / 1000 * provider.price_in_per_1k + pout / 1000 * provider.price_out_per_1k, 6)
    return CloudResult(
        answer=answer,
        provider=provider.name,
        model=use_model,
        prompt_tokens=pin,
        completion_tokens=pout,
        cost_usd=cost,
    )


def validate_provider(
    provider: CloudProvider,
    api_key: str,
    *,
    model: str | None = None,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    """Cheaply check that an API key authenticates against the provider.

    Tries a free ``GET /models`` first; if the provider has no such route, falls
    back to a tiny ``chat/completions`` ping (the real escalation path). Never
    raises - returns ``(ok, human_readable_detail)``.
    """
    if not api_key:
        return False, f"No API key. Paste one above or set {provider.api_key_env}."

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.get(f"{provider.base_url}/models", headers=headers, timeout=timeout)
    except httpx.ConnectError:
        return False, f"Can't reach {provider.name} at {provider.base_url}."
    except httpx.HTTPError as e:
        return False, f"Network error reaching {provider.name}: {e}"
    else:
        if resp.status_code in (200, 201):
            return True, f"Authenticated with {provider.name}."
        if resp.status_code in (401, 403):
            return False, f"{provider.name} rejected the key (HTTP {resp.status_code})."
        if resp.status_code not in (404, 405):
            return False, f"{provider.name} returned HTTP {resp.status_code}: {resp.text[:120]}"

    # No /models route - validate via a minimal chat completion instead.
    try:
        call_cloud(
            provider, [Message("user", "ping")], api_key=api_key, model=model, timeout=timeout
        )
    except BackendError as e:
        return False, str(e)
    return True, f"Authenticated with {provider.name}."
