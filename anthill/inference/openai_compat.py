from __future__ import annotations

import json
from collections.abc import Iterator, Sequence

import httpx

from ..hybrid.scrub import restore, scrub
from .base import BackendError, ChatResult, Message, _mean_logprob, stays_local


def _scrub_messages(messages: Sequence[Message]) -> tuple[list[Message], dict[str, str]]:
    """Redact PII from every message before it crosses the network to a remote OpenAI-compatible
    endpoint (an org's own RunPod/onprem server, or a third-party inference provider like Berget).
    Returns the scrubbed messages plus the local replacement map used to restore the reply.

    Each message is scrubbed independently - `scrub()` numbers placeholders from 1 within a single
    call - so two different messages sharing a PII kind (e.g. an email in the wiki-context message and
    a different email in the question) would otherwise both produce "[EMAIL_1]" and silently clobber
    each other's mapping when merged into one shared `replacements` dict, corrupting which original
    value a later restore() puts back into the reply (a real, reproduced bug - not a rare edge case,
    since a grounded turn routinely combines wiki context with the user's own question). Suffixing
    each message's placeholders with its index before merging keeps them unique across the batch.

    Images are ALWAYS stripped here, never sent - the redaction above only works on text (regex/NER
    pattern matching over `scrub()`), so there is no honest way to "scrub" pixel content out of an
    arbitrary image. Failing closed (drop it) matches this same file's existing local-vision path
    being unaffected (the on-device Ollama backend never reaches this function - `stays_local()` skips
    scrubbing entirely, so nothing changes there) and the codebase's established fail-closed precedent
    for anything that can't be safely protected (see `hybrid/escalate.py`'s consent-gated design). A
    short note replaces the image in the text so the model - and the user, via its reply - knows why
    the image wasn't seen, instead of silently answering as if it never existed.
    """
    replacements: dict[str, str] = {}
    scrubbed: list[Message] = []
    for i, m in enumerate(messages):
        sr = scrub(m.content)
        text = sr.text
        for ph, original in sr.replacements.items():
            unique_ph = f"{ph[:-1]}_m{i}]"  # "[EMAIL_1]" -> "[EMAIL_1_m0]"
            text = text.replace(ph, unique_ph)
            replacements[unique_ph] = original
        if m.images:
            n = len(m.images)
            noun = "image" if n == 1 else "images"
            note = (
                f"[{n} attached {noun} not sent - this connected model does not receive images, to "
                "keep them from leaving this machine. Tell the user to switch to a local model for "
                "image analysis.]"
            )
            text = f"{text}\n\n{note}" if text else note
        scrubbed.append(Message(m.role, text, None))
    return scrubbed, replacements


def _restore_stream(tokens: Iterator[str], replacements: dict[str, str]) -> Iterator[str]:
    """Restore placeholders in a live token stream without ever emitting a broken/partial one -
    holds back text from the last unmatched '[' until it closes, then restores and flushes."""
    if not replacements:
        yield from tokens
        return
    buf = ""
    for tok in tokens:
        buf += tok
        cut = len(buf)
        last_open = buf.rfind("[")
        if last_open != -1 and "]" not in buf[last_open:]:
            cut = last_open
        if cut > 0:
            yield restore(buf[:cut], replacements)
            buf = buf[cut:]
    if buf:
        yield restore(buf, replacements)


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
                            emitted_text = emitted_text or bool(token.strip())
                            yield token
                    if not completed:
                        raise BackendError("The model endpoint stream ended before completion.")
                    if not emitted_text:
                        raise BackendError("The model endpoint returned an empty response.")
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
        try:
            choice = (resp.json().get("choices") or [{}])[0] or {}
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
