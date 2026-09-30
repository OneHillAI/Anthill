# Spec: reliable promoted MLX chat streaming

Status: implemented. Lane: `pillar:model`. Issue: #757.

## Problem

A promoted local MLX fine-tune is exposed through an OpenAI-compatible endpoint with a Hugging Face
repository model ID. Chat constructed the Ollama-only `TaskRouter` for that endpoint, probed Ollama
routes that MLX does not expose, and replaced the configured repository ID with the Ollama fallback
`qwen2.5:3b`. MLX rejected that model. The streaming error handler then accessed an unread HTTPX
response body, hiding the actionable error behind `ResponseNotRead`. Chat displayed the replacement
error twice and persisted an empty assistant message as training data.

## Requirements

### R1 - Backend-specific model routing

- `TaskRouter` SHALL be used only with an Ollama inference backend.
- A promoted local MLX or other OpenAI-compatible backend SHALL receive its configured model ID.
- Normal and agent-mode chat SHALL NOT probe Ollama discovery routes on an OpenAI-compatible endpoint.
- Streaming model overrides SHALL be applied only to `OllamaBackend`.

### R2 - Actionable streaming HTTP errors

- OpenAI-compatible and Ollama streaming backends SHALL read a failed HTTP response before inspecting
  its body.
- The resulting `BackendError` SHALL retain the HTTP status and bounded response detail.
- Failed responses SHALL NOT surface HTTPX `ResponseNotRead` or buffer an unbounded response body.
- A 200 stream that reports a protocol error, completes without response text, or ends without a
  completion marker SHALL fail rather than becoming an empty or partial successful answer.
- OpenAI-compatible refusal text SHALL be surfaced as response text.
- Timeouts and mid-stream transport failures SHALL become actionable `BackendError` messages.
- Successful responses SHALL remain incrementally streamed rather than read eagerly.

### R3 - One visible, durable chat failure

- A failed generation SHALL emit one structured SSE error followed by `[DONE]`.
- Chat SHALL render the error once and wait for `[DONE]` before closing the event stream.
- The assistant history row SHALL contain the actionable warning, with any partial response retained,
  and SHALL persist an explicit generation-failure state.
- Failure filtering and provenance badges SHALL use that state, never warning-text matching.
- Failed output SHALL NOT enter training data, memory distillation, or successful-generation metrics.
- An unavailable plane SHALL likewise emit one error, not duplicate token and error events.

## Acceptance criteria

- A new Solo chat using a promoted local MLX fine-tune streams an answer from its configured repository
  model ID without an Ollama discovery request.
- Agent-mode chat uses the same configured MLX model ID.
- Streaming HTTP 4xx responses from OpenAI-compatible and Ollama endpoints expose status and response
  detail without `ResponseNotRead`.
- A failed turn displays one warning, reaches `[DONE]`, and persists a nonempty assistant warning.
- A failed turn creates no training example.
- Model-free regression tests cover these behaviors.

## Design and scope

The fix stays at existing boundaries: the web chat route decides whether an Ollama router exists, the
streaming shim owns override compatibility, each inference backend reads its own failed stream, and the
SSE client renders the existing structured error shape. One additive, auto-migrated
`ChatMessage.generation_failed` column records durable failure state. No backend abstraction, fallback
model, dependency, versioned migration, or model-name translation is added.

Out of scope: changing `plane_inference()`, MLX promotion or serving lifecycle, TaskRouter selection
policy, and automatic fallback from a promoted fine-tune to Ollama.
