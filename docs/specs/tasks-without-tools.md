# Spec: Tasks and agents answer without tools on a model that cannot call them

Status: implemented. Lane: `pillar:model`.

## Problem

A task run, an agent run and the chat's agent mode always send the model a list of tools. A model that cannot
call tools makes Ollama refuse the request with `400 ... does not support tools`. On a small model without tool
support such as `gemma3:4b` (which the Anthill catalogue offers) every Task therefore failed at once, and the
member saw the raw Ollama error in the result and in the run history. Found on the installed beta (v1.1.0-rc.1)
on real hardware.

## Requirements

- `OllamaBackend.can_use_tools(model)` reads the model's capabilities from `/api/show` (the list includes
  `tools` for a model that can call them). The read is shared with `can_think` (one `/api/show` per base URL and
  model), cached for five minutes, and dropped when a model is deleted. When the capabilities cannot be read
  (Ollama unreachable) it answers True and does not cache, so the old behaviour is kept and the real problem is
  reported by the request itself; an older Ollama that reports none also counts as able.
- A 400 "does not support tools" on a request raises `ToolsUnsupported` (a `BackendError`) with a plain message
  and records the model as unable, so a capability that could not be read up front is learned from the first
  refusal and the next run does not send the tools again.
- `AgentExecutor.run` and `.stream` answer a model that cannot call tools with one plain chat over the goal and
  the context, asked of the same model as the tool steps and bounded the same way (`STEP_MAX_TOKENS`), with a
  standing instruction to the model that says there are no tools and not to claim any action. The answer starts
  with a fixed note: "Note: this model can't use tools, so this run answered without searching the web, reading
  files or using any other tool. For work that needs them, switch to a model that supports tools." `stream`
  sends the note at once and the answer as it is written. A run with no tools to offer is not treated this way.
  The result carries `answered_without_tools`.
- A scheduled task or agent run that answered without tools is not a clean success: the run is marked "needs
  review" with a plain reason and the owner is notified, on every run. A task whose goal is an action (write a
  wiki page, post a message) therefore does not report success each time while doing nothing.
- The council review (on by default for tasks and agents) and the escalation check see the answer without the
  note; the note is put back on top of whatever they return. Auto-memory remembers the first line of the answer,
  never the note.
- In chat, the quiet route into the agent (a deep question, the classifier's deep verdict, a "go deeper"
  follow-up) is not taken when the model cannot call tools: it would give one plain chat with no wiki grounding,
  web or history, which is worse than the normal answer. Explicit agent mode still runs, with the note.
- A model that can call tools, and a backend that cannot report capabilities (the cloud backends and the
  on-device MLX fine-tune), behave as before.
- The Tasks page, when the local model a Solo task would run on cannot call tools, shows a short note in the New
  task dialog (not when editing a task) saying what such a task can and cannot do, and swaps the example text to
  a task that needs no tools. If the capability cannot be read, no note is shown and the page loads as before;
  the page waits at most two seconds for the read.
- Because the executor is shared, an agent run and the chat's agent mode get the same behaviour.

## Offered models

Capability as Ollama lists it for each local model Anthill offers (`anthill/hosting/model_catalog.json`).
Read from the models installed on the build machine (`/api/show`) and from the Ollama library pages on
2026-10-11. The app does not depend on this table: it reads the capability from the installed model at run time.

| Model | Lists tools |
|---|---|
| gemma3 (1b, 4b, 27b) | **no** (`gemma3:4b` confirmed from the installed model) |
| phi4:14b | **no** |
| qwen3.5 (0.8b to 122b) | yes (`qwen3.5:9b` confirmed from the installed model) |
| qwen3.6 | yes |
| gpt-oss (20b, 120b) | yes |
| llama3.3:70b | yes |
| mistral-small:24b | yes |
| deepseek-r1:32b | yes |
| deepseek-v3.1 | yes |
| nemotron-3-nano (4b, 30b), nemotron-3-super | yes |
| glm-4.7-flash | yes |

The cloud-served entries (`deepseek-v4-pro`, `deepseek-v4-flash`, `minimax-m3`, `glm-5.1`, `glm-5.2`,
`kimi-k2.7-code`, `kimi-k3`, `nemotron-3-ultra`) are not run through the local check and were not read.
`mistral:7b` and `granite3.2-vision:2b` (installed on the build machine, not offered) also list tools.

## Acceptance criteria

- On `gemma3:4b` a Task finishes with an answer that starts with the note, and no raw error text.
- On a model that lists tools, a Task runs as before and the answer has no note.
- Both were run for real on the build machine against the installed models, headless.

## Known limits

- A task that needs the web or a file reads only what is in its description and attached context; the note says
  so. It does not stop the model writing a confident answer from its own knowledge.
- The on-device MLX fine-tune is not an Ollama model, so its capability is not read and a tools refusal from it
  is not handled here: it behaves as before (a refusal is an error, with no note).
- A plain answer to an action task is flagged for review but cannot do the action; the owner has to pick a model
  that supports tools for it.
- Other backend errors still reach the run history with their own text (for example "Ollama returned 404");
  only the tools refusal is handled here.
- The built-in fallback model (`qwen2.5:3b` in `anthill/config.py`) was not confirmed either way from the
  library page; the live check decides.

## Tests

`tests/test_tasks_without_tools.py`.
