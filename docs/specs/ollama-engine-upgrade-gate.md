# Spec: a written diff and a contract test before the bundled Ollama changes

Status: implemented. Lane: `pillar:model`. Issue: #227.

## Problem

Anthill bundles Ollama 0.30.10 (`_OLLAMA_VERSION` in `anthill/inference/ollama.py`, `OLLAMA_VERSION` in
`scripts/build-app.sh`). The stable line is 0.40.16. Between them the engine changed how models run on Apple
Silicon, how downloaded models are stored, what the replies contain and what the OpenAI-compatible routes
default to. Nobody had written down what changed, and Anthill's parsers were only tested against hand-made
shapes, so a bump could not be judged. One example: #222 reads `prompt_eval_cached_count` from a live 0.40.7,
and it was not known whether the pin reports it.

## Requirements

1. **A written diff** of 0.30.10 to 0.40.16 (below): the changes an HTTP client or the bundled binary can feel,
   with the effect on Anthill and the release it came in.
2. **A contract suite** (`tests/test_ollama_compat_contract.py`) that runs Anthill's parsers over a fixture set
   for each version: `/api/chat` (streamed, not streamed, with tools, with an image), `/api/embed`, `/api/show`,
   `/api/ps`, `/api/tags`, delete, and the text `ollama pull` prints. Adding a directory under
   `tests/fixtures/ollama/` adds a column to every test.
3. **Provenance.** Every fixture is labelled `recorded` (a real reply, with date and machine) or `docs` (written
   by hand from the documentation and source, with what it was written from). The suite prints the labels, fails
   on an unlabelled file, and fails if an endpoint has no `recorded` fixture in any version, so a `docs` fixture
   is never the only evidence.
4. **Recorder.** `tests/fixtures/ollama/record_fixtures.py` records a new version from a local engine. It only
   reads and generates with models already installed (no pull, no delete), cuts the embedding to 8 values, drops
   tokenizer arrays and the Modelfile, template, built-in instructions and licence text, and replaces home folders in paths.
5. **Old-archive restore** (`tests/test_backup_ollama_files.py`): a new backup holds no Ollama model files and
   its manifest is Anthill's own; restoring an older plain archive copies its `ollama/` files under the models
   folder by relative path, sets an existing file aside and removes it after success, and puts everything back on
   a failure part way. Anthill never reads an Ollama manifest.
6. **Pin audit** (`tests/test_ollama_pin.py`): the Python pin, the build script pin and the two URL assertions in
   `tests/test_ollama_windows.py` agree, and no workflow or Docker file carries another copy.

No runtime behaviour changes, no version bump, no engine installed or run.

## The diff, 0.30.10 to 0.40.16

Read from the GitHub release notes and the Go sources at the two tags, through a page summariser (wording of
non-quoted rows is paraphrased; spot-check a row before relying on it). Releases not listed carried nothing
relevant. The notes show no year (2026 from asset dates).

| Release | Change | Effect on Anthill |
|---|---|---|
| 0.30.11 | `ollama ps` no longer double-counts mmap'd weights on partial offload | `size` in `/api/ps` can read lower on a partial offload |
| 0.32.6 | `/v1/chat/completions` streaming matches the OpenAI wire format (role only on the first chunk, `finish_reason` in its own chunk, usage in a separate chunk with `include_usage`); a truncated reply reports `finish_reason: "length"`, not `"tool_calls"` | `OpenAICompatBackend` completes on any `finish_reason` and skips choice-less chunks, so streaming is unaffected; `chat_with_tools` already treats "length" with tool calls as incomplete, which it now sees |
| 0.32.10 (pre-release) | default `repeat_penalty` for models that set none changes from 1.1 to 1.0 | answers can differ; not confirmed in a stable build |
| 0.32.15 | resolved model metadata cached between requests (vendor figure: first token about 995 ms to 524 ms) | first-word time may improve; unmeasured here |
| 0.33.0 | prefill restore points are trustworthy; "on models with recurrent layers" a request matching 46k of 47k tokens was reprocessed from zero | prefix reuse for hybrid models (qwen3.5 and 3.6) works only from here; the pin lacks it |
| 0.33.3 | reports cached prompt tokens; honours GGUF default parameters | `prompt_eval_cached_count` appears from here (`TurnStats.cached_tokens` is None on the pin); sampling defaults can change |
| 0.34.1 | `/api/tags` much faster; `typical_p` deprecated; MLX `ollama create` no longer experimental, GGUF creation needs llama.cpp tooling for safetensor conversion and quantisation | `anthill/training/trainer.py` runs `ollama create ... FROM <base> ADAPTER <mlx adapter>`: see open question 4 |
| 0.34.3 | `/api/show` adds thinking controls | additive; Anthill reads `capabilities` only |
| 0.40.0 | on Apple Silicon, architectures the MLX runtime supports (gemma4, qwen3.5, qwen3.6, qwen3.8) run on MLX by default | the engine under the default models changes; speed and memory are unmeasured at 16 and 24 GB; `/api/ps` gains `runner` |
| 0.40.2 | "Models downloaded with earlier versions of Ollama are upgraded in the background the first time you run them"; the original copy is kept as a backup so downgrading is safe; below 0.40 "you'll need to re-pull those models" | disk use spikes; a downgrade needs a re-pull; see open question 2 |
| 0.40.3 | background model upgrade paused for request performance | whether the rewrite still runs is unknown |
| 0.40.7 | `/api/show` adds `renderer`, `parser`, `template_source` (the Go types at 0.30.10 already list renderer and parser: the sources disagree) | additive |
| 0.40.14 | "OpenAI-compatible endpoints no longer override a model's packaged sampler defaults" | Anthill sends `temperature` on every `/v1` call but never `top_p`: requests now get the model's `top_p`, not 1.0 |
| 0.40.15 | recurrent and vision GGUF models can serve more than one request at a time | memory use under concurrent requests can rise |
| 0.40.16 | `/v1/models` returns `[]` instead of `null` when nothing is installed | see open question 5 |

Not found in any note read: changes to archive names or the macOS and Windows bundle layout, to the defaults for
context length, `num_parallel`, `keep_alive`, flash attention or KV cache type, and to environment variables
other than `OLLAMA_LOAD_TIMEOUT` (0.32.1). The asset lists were not opened. The `ollama pull` progress code
(`progress/bar.go`, `progress/progress.go`, `progress/spinner.go`) read the same at both tags: it writes to
stderr, redraws with ANSI cursor moves (no carriage returns), uses SI units (KB, MB, GB) and prints a status line
per phase. 0.40.16 adds an error text for a model that exists only as `:cloud`, which carries no percent.

Facts from the field lists in `api/types.go`: `prompt_eval_cached_count` is absent at 0.30.10 and present at
0.40.16; `runner` is absent from `/api/ps` at 0.30.10 and present at 0.40.16; `context_length` and `size_vram`
are in `/api/ps` at both; `capabilities` and `projector_info` are in `/api/show` at both.

## Open questions

1. Does 0.30.10 ever report a cached-token count? The Go types say no and 0.33.3 added it, so on the pin the
   speed line and `query_metrics.cached_tokens` stay empty. Recording a 0.30.10 engine would settle it.
2. What does restoring an older archive's `ollama/` files do to a store a 0.40.2 or newer engine has already
   rewritten? `_apply_models` copies by relative path and parses nothing, so it cannot break; whether the engine
   then reads the old-layout files, and how large the backups the rewrite keeps are, are not in the notes.
3. Does the background rewrite still run after 0.40.3 paused it, and how much disk does it use on a real library?
4. Does `ollama create ... FROM <base> ADAPTER <mlx adapter>` (the training promote path) still work from 0.34.1?
   This is the highest-risk item for a bump and is not tested anywhere.
5. `anthill/hosting/endpoint.py` `_list_models` reads `r.json().get("data", [])` without a guard. If a pinned
   engine returns `"data": null` for an empty install, that raises `TypeError`; the discovery loop below it
   catches it. Whether 0.30.10 does this was not checked.
6. Whether the 0.32.10 `repeat_penalty` change reached a stable build.
7. Disk size and download time of the 0.40.16 archive against the 137 MB noted in `scripts/build-app.sh`.

## Known limits

- The 0.30.10 fixtures are `docs`: no engine of that version was run, and the documented examples were read
  through a summariser, so they are faithful to keys and values, not byte-exact. `/api/tags` holds the one
  documented entry. There is no recorded vision-model `/api/show` or missing-model reply for that version.
- The 0.40.7 set is recorded from the engine on the developer's Mac (qwen2.5:3b, granite3.2-vision:2b, bge-m3);
  a pull and a delete were not recorded (they would reach the registry or remove a model), so those two are `docs`.
- The suite proves Anthill's parsers read these shapes. It does not prove an engine produces them, and it does not
  run an engine.
- The memory-report parsers added in #230 are not tested here (that PR was open); the suite checks the raw fields
  they need (`size`, `size_vram`, `context_length`, the attention keys).

## Acceptance

- `tests/test_ollama_compat_contract.py` (new) passes on both fixture sets and prints each fixture's provenance.
- `tests/test_backup_ollama_files.py` and `tests/test_ollama_pin.py` (new) pass.
- This spec holds the diff and the open questions and says which fixtures are `docs`. It gives no go or no-go.
