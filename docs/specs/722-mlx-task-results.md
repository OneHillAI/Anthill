# Spec: reliable local MLX task tool calls and one-time runs

Status: implemented. Lane: `pillar:model`. Issue: #722.

## Problem

The local MLX server can render Qwen's tool-call example with doubled JSON braces. MLX-LM then
fails to parse the generated call but still returns `finish_reason: "tool_calls"`. Anthill received
that response as a successful empty answer. The same task could also remain due after a `once` run,
and verifier state from older runs could remain visible on later results.

## Requirements

### R1 - Local MLX tool calls remain parseable

- The local MLX serving path SHALL correct the known malformed Qwen tool-call example before starting
  the existing `mlx_lm server` process.
- The correction SHALL use the server's existing `--chat-template` option and SHALL NOT add a custom
  MLX fork or a new runtime dependency. The `train-mac` extra SHALL require `mlx-lm>=0.26`, whose
  OpenAI-compatible server supports the native tool-call response shape used by Anthill.
- If the tokenizer cannot be loaded or does not contain the known example, startup SHALL remain
  best-effort; the inference client invariant in R2 still prevents an empty successful result.

### R2 - Incomplete tool calls fail closed

- When an OpenAI-compatible response has `finish_reason: "tool_calls"` but no parsed tool calls,
  the backend SHALL raise `BackendError`.
- Anthill SHALL NOT normalize that response into a successful empty answer.
- A non-empty OpenAI `refusal` SHALL be surfaced as the assistant content when no tool call is present.
- A response containing both a refusal and tool calls SHALL fail closed rather than execute tools.
- Valid tool calls and ordinary text responses SHALL retain their existing behavior.

### R3 - One-time schedules stop after their terminal run

- A task whose schedule is `once` SHALL clear `next_run_at` after the run completes, whether the run
  succeeds or fails.
- A completed one-time task SHALL NOT be selected by a later scheduler tick without an explicit
  run-again action.

### R4 - Verifier state belongs to the current run

- A scheduled task run SHALL clear the task's prior verifier flag, reason, and confidence before
  execution.
- Verifier signals raised during the current run SHALL still be preserved and surfaced.
- Reasons from older runs SHALL NOT accumulate in the current task result.

### R5 - Task execution leases remain coherent

- Manual reruns and queued follow-ups SHALL preserve `running` only while a live `TaskRun` exists.
- A stale `running` task SHALL be repaired to `pending` rather than left unclaimable.
- The scheduler SHALL not claim a task that already has a live `TaskRun`.
- Repeated scheduler startup calls in one process SHALL not sweep or re-arm live runs.
- Only one scheduler process SHALL execute tasks for a file-backed database; another process SHALL wait for
  the OS lock instead of sweeping or running tasks concurrently.

## Acceptance criteria

- A live local MLX Qwen task that needs a tool receives a parsed tool call and produces a nonempty
result.
- An incomplete OpenAI-compatible tool-call response raises `BackendError`.
- A `once` task has `next_run_at = NULL` after its run and its run count does not increase on the
next scheduler tick.
- A new run does not inherit verifier state from the previous run, while a current-run verdict is
stored normally.
- Manual reruns and queued follow-ups repair stale execution state without losing a live lease.
- Startup restores local MLX and configured LLM tunnels before releasing interrupted task runs.
- Model-free regression tests cover all five requirements.

## Design and scope

The fix stays at the existing boundaries: `mlx_serve` prepares the server command, the
OpenAI-compatible backend validates response shape, and the scheduler delegates task lifecycle state
to the durable occurrence ledger described in
[`778-task-schedule-timezones.md`](778-task-schedule-timezones.md).
No client-side recovery parser is added because MLX discards the malformed call before Anthill can
recover it. The scheduler uses a standard-library OS lock beside the SQLite database to elect one
worker process; no schema migration or new dependency is required.

Out of scope: changing MLX-LM internals, repairing arbitrary model templates, or changing the
independent-verifier policy.
