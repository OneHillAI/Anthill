from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from time import monotonic

import httpx

from ..hybrid.scrub import restore
from .base import BackendError, ChatResult, Message, _mean_logprob, stays_local
from .egress import restore_stream as _restore_stream
from .egress import scrub_messages as _scrub_messages
from .stats import TurnStats, from_openai


def _confidence_from_openai_response(data: dict) -> float | None:
    """Parse the standard OpenAI Chat Completions logprobs shape (#278): choices[0].logprobs.content,
    a list of {"token", "logprob", "bytes", "top_logprobs"} per generated token. None on anything
    unexpected (the field absent/null entirely - a provider that ignored the request, e.g. Groq - or
    any other shape mismatch), never raises."""
    try:
        choice = data["choices"][0]
        content = choice.get("logprobs", {}).get("content")
    except (KeyError, IndexError, AttributeError):
        return None
    if not isinstance(content, list):
        return None
    return _mean_logprob(content)


class OpenAICompatBackend:
    """Talks to any OpenAI-compatible /chat/completions server: vLLM, llama.cpp,
    LM Studio, or a privately hosted endpoint. base_url should include the /v1 suffix."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 300.0,
        max_tokens: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._scrub_egress = not stays_local("openai", self.base_url)
        # The last call's TurnStats, or None while it runs, after a failure, or when the endpoint sent no
        # usage and the call was not streamed (#219).
        self.last_turn: TurnStats | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def chat(self, messages: Sequence[Message], *, temperature: float = 0.2) -> str:
        content, _data, replacements = self._chat_raw(
            messages, temperature=temperature, logprobs=False
        )
        return restore(content, replacements) if replacements else content

    def chat_with_confidence(
        self, messages: Sequence[Message], *, temperature: float = 0.2
    ) -> ChatResult:
        """Like ``chat``, plus a confidence signal (#278) when the endpoint returns per-token
        logprobs for the standard OpenAI ``logprobs``/``top_logprobs`` request fields - confirmed
        supported by vLLM (RunPod/Lambda self-provisioned) and Infercom; Groq documents these fields
        as accepted-but-not-implemented (always ``confidence=None`` there); Berget's support is
        unconfirmed. Parsed defensively either way - an unsupported/null response degrades to
        ``confidence=None``, never raises."""
        content, data, replacements = self._chat_raw(
            messages, temperature=temperature, logprobs=True
        )
        text = restore(content, replacements) if replacements else content
        return ChatResult(text=text, confidence=_confidence_from_openai_response(data))

    def _chat_raw(
        self, messages: Sequence[Message], *, temperature: float, logprobs: bool
    ) -> tuple[str, dict, dict[str, str]]:
        replacements: dict[str, str] = {}
        if self._scrub_egress:
            messages, replacements = _scrub_messages(messages)
        payload = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": False,
            "temperature": temperature,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if logprobs:
            # #278 stronger-escalation work: a provider that doesn't support these fields (Groq,
            # documented as accepted-but-unimplemented) just ignores them - never errors - so this is
            # safe to always request when a confidence signal is wanted, no capability probe needed.
            payload["logprobs"] = True
            payload["top_logprobs"] = 1
        self.last_turn = None
        started = monotonic()
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            raise BackendError(
                "The model endpoint did not respond in time. If it is a serverless endpoint "
                "(e.g. RunPod), it may be cold-starting or scaled to zero - try again in a minute, "
                "or keep a warm worker."
            ) from e
        except httpx.ConnectError as e:
            raise BackendError(
                f"Can't reach the model endpoint at {self.base_url}. Check that it is running."
            ) from e
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:200]
            if e.response.status_code in (401, 403):
                detail = "unauthorized - check the endpoint's API key."
            raise BackendError(
                f"The model endpoint returned {e.response.status_code}: {detail}"
            ) from e

        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            refusal = message.get("refusal")
            if content is None and isinstance(refusal, str) and refusal.strip():
                content = refusal
            if not isinstance(content, str):
                raise TypeError("message content is not text")
        except (KeyError, IndexError, TypeError) as e:
            raise BackendError(f"Unexpected response shape from {self.base_url}.") from e
        self.last_turn = from_openai(
            data, model=str(payload["model"]), total_ms=round((monotonic() - started) * 1000)
        )
        return content, data, replacements

    def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> Iterator[str]:
        """Yield tokens as they arrive from an OpenAI-compatible /chat/completions SSE stream."""
        replacements: dict[str, str] = {}
        if self._scrub_egress:
            messages, replacements = _scrub_messages(messages)
        payload = {
            "model": model or self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": True,
            "temperature": temperature,
        }

        def _tokens() -> Iterator[str]:
            self.last_turn = None
            started = monotonic()
            first_token_ms: int | None = None
            final: dict = {}
            try:
                with httpx.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                ) as resp:
                    if not resp.is_success:
                        body = bytearray()
                        for chunk in resp.iter_bytes(chunk_size=200):
                            body.extend(chunk[: 200 - len(body)])
                            if len(body) >= 200:
                                break
                        detail = body.decode("utf-8", errors="replace")
                        if resp.status_code in (401, 403):
                            detail = "unauthorized - check the endpoint's API key."
                        raise BackendError(
                            f"The model endpoint returned {resp.status_code}: {detail}"
                        )
                    completed = False
                    emitted_text = False
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if data == "[DONE]":
                            completed = True
                            break
                        try:
                            event = json.loads(data)
                            if event.get("error"):
                                error = event["error"]
                                detail = (
                                    error.get("message", str(error))
                                    if isinstance(error, dict)
                                    else str(error)
                                )
                                raise BackendError(
                                    f"The model endpoint stream failed: {detail[:200]}"
                                )
                            if event.get("usage") or event.get("timings"):
                                final = event  # the usage chunk may have no choices
                            choice = event["choices"][0]
                            delta = choice.get("delta") or {}
                        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                            continue
                        if choice.get("finish_reason") is not None:
                            completed = True
                        content = delta.get("content")
                        refusal = delta.get("refusal")
                        token = content if isinstance(content, str) else ""
                        if isinstance(refusal, str) and refusal.strip():
                            token += refusal
                        if token:
                            if first_token_ms is None:
                                first_token_ms = round((monotonic() - started) * 1000)
                            emitted_text = emitted_text or bool(token.strip())
                            yield token
                    if not completed:
                        raise BackendError("The model endpoint stream ended before completion.")
                    if not emitted_text:
                        raise BackendError("The model endpoint returned an empty response.")
                    self.last_turn = from_openai(
                        final,
                        model=str(payload["model"]),
                        first_token_ms=first_token_ms,
                        total_ms=round((monotonic() - started) * 1000),
                    )
            except httpx.TimeoutException as e:
                raise BackendError(
                    "The model endpoint did not respond in time. If it is a serverless endpoint "
                    "(e.g. RunPod), it may be cold-starting or scaled to zero - try again in a minute, "
                    "or keep a warm worker."
                ) from e
            except httpx.ConnectError as e:
                raise BackendError(
                    f"Can't reach the model endpoint at {self.base_url}. Check that it is running."
                ) from e
            except httpx.TransportError as e:
                raise BackendError(
                    f"The connection to the model endpoint at {self.base_url} failed while streaming. "
                    "Check that it is running and try again."
                ) from e

        yield from _restore_stream(_tokens(), replacements)

    def chat_with_tools(
        self,
        messages: Sequence[Message],
        tools: list[dict],
        *,
        temperature: float = 0.1,
        model: str | None = None,
    ) -> dict:
        """Call ``/chat/completions`` with tool specs and NORMALISE the reply to the Ollama shape
        (``{"message": {"content", "tool_calls"}}``) so the agent loop parses it the same way.
        Without this, an org/cloud (OpenAI-compatible) chat would silently lose tool-calling."""
        replacements: dict[str, str] = {}
        if self._scrub_egress:
            messages, replacements = _scrub_messages(messages)
        payload = {
            "model": model or self.model,
            "messages": [m.as_dict() for m in messages],
            "tools": tools,
            "stream": False,
            "temperature": temperature,
        }
        self.last_turn = None
        started = monotonic()
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.ConnectError as e:
            raise BackendError(
                f"Can't reach an OpenAI-compatible server at {self.base_url}."
            ) from e
        except httpx.HTTPStatusError as e:
            raise BackendError(
                f"Server returned {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        data = resp.json()
        try:
            choice = (data.get("choices") or [{}])[0] or {}
            msg = choice.get("message", {}) or {}
        except (KeyError, IndexError, TypeError) as e:
            raise BackendError(f"Unexpected response shape from {self.base_url}.") from e
        raw_tool_calls = msg.get("tool_calls")
        tool_calls = [] if raw_tool_calls is None else raw_tool_calls
        refusal = msg.get("refusal")
        refusal_text = refusal.strip() if isinstance(refusal, str) else ""
        if refusal_text and tool_calls:
            raise BackendError(
                f"Model endpoint returned both a refusal and tool calls at {self.base_url}."
            )
        incomplete = not isinstance(tool_calls, list) or (
            choice.get("finish_reason") == "tool_calls" and not tool_calls
        )
        if choice.get("finish_reason") == "length" and tool_calls:
            incomplete = True
        for call in tool_calls if isinstance(tool_calls, list) else []:
            fn = call.get("function") if isinstance(call, dict) else None
            if (
                not isinstance(fn, dict)
                or not isinstance(fn.get("name"), str)
                or not fn["name"].strip()
                or "arguments" not in fn
            ):
                incomplete = True
                break
            try:
                arguments = (
                    json.loads(fn["arguments"])
                    if isinstance(fn["arguments"], str)
                    else fn["arguments"]
                )
            except (json.JSONDecodeError, TypeError):
                incomplete = True
                break
            if not isinstance(arguments, dict):
                incomplete = True
                break
        content = refusal_text or msg.get("content") or ""
        if not isinstance(content, str) or (not tool_calls and not content.strip()):
            incomplete = True
        if incomplete:
            raise BackendError(
                f"Model endpoint returned an incomplete tool call or empty response at {self.base_url}."
            )
        self.last_turn = from_openai(
            data, model=str(payload["model"]), total_ms=round((monotonic() - started) * 1000)
        )
        return {
            "message": {
                # tool_calls arguments are NOT restored: the model only ever saw scrubbed text, so
                # any PII-shaped value in an argument is itself a placeholder, not a real leak - and
                # restoring inside arbitrary tool-call JSON risks corrupting a real argument value.
                "content": restore(content, replacements) if replacements else content,
                "tool_calls": tool_calls,
            }
        }

    def health(self) -> str | None:
        try:
            resp = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError:
            return f"Server not reachable at {self.base_url}."
        return None
