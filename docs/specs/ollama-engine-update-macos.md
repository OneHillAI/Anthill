# Spec: update the bundled Ollama to 0.40.16 on macOS

Status: measured on this PR's own branch. Lane: `pillar:model`. Issue: #264. Builds on `docs/specs/ollama-engine-upgrade-gate.md`
(#227: the written diff and contract suite), `docs/specs/reference-machine-scoreboard.md` (#236) and the exact GGUF tags of #345 (merged
as #358), without which a plain tag on 0.40.x pulls an MLX build whose memory grows without bound.

## Problem and decision

Anthill's macOS engine is Ollama 0.30.10. The maintainer decided on 2026-11-23 to move it to 0.40.16 and to leave Windows on 0.30.10. The
engine is not shipped inside the desktop app: the canonical Tauri app downloads it once, on first launch, from the constants in
`anthill/inference/ollama.py`; `scripts/build-app.sh` is the older standalone PyInstaller build and carries the same pin (the test
`tests/test_ollama_pin.py` keeps the two equal).

## Requirements

1. The macOS pin is 0.40.16: `_OLLAMA_VERSION` and `_OLLAMA_DARWIN_SHA256` in `anthill/inference/ollama.py` and `OLLAMA_VERSION` and
   `OLLAMA_SHA256` in `scripts/build-app.sh`. SHA-256 `a743717494315da7cd352982b805f6b992d4b4452ca117fd5fbf86c4ee46fca0` (186,116,182
   bytes; checked again on 2026-11-25 against a fresh download; 632 MB unpacked).
2. The Windows pin is `_OLLAMA_WINDOWS_VERSION` = 0.30.10, unchanged; the Windows download URL is built from it.
3. `sizing.bundled_engine_version()` (the engine a catalogue row's `min_engine_version` is compared with) reads the pin of the platform
   the app runs on, so Windows keeps comparing with 0.30.10 (`tests/test_ollama_pin.py`).
4. `tests/fixtures/ollama/0.40.16/` holds replies recorded from a real 0.40.16 on a second port (`OLLAMA_URL`, 2026-11-19); the
   contract suite passes on every version. `tests/test_ollama_pin.py` checks both pins and the places that carry each.

## Measured (2026-11-25, M4 16 GB, AC power, private engines on their own ports, the user's Ollama untouched)

The same model files on both engines (equal digests), a copy-on-write copy of one model folder, the scoreboard protocol of #236 (three
repeats, medians). Decode was measured twice per engine in an A-B-A-B order because the first 0.30.10 figure was 8% below the one from
19 November.

| | 0.30.10 | 0.40.16 |
|---|---|---|
| qwen2.5:3b decode, no context (tok/s), two runs | 43.1, 42.1 | 48.5, 47.9 |
| qwen2.5:3b decode at about 4,000 tokens (tok/s) | 39.8, 41.0 | 44.6, 43.8 |
| qwen3.5:4b-q4_K_M decode, no context (tok/s) | 27.8, 29.7 | 31.7, 31.5 |
| qwen3.5:4b-q4_K_M decode at about 4,000 tokens (tok/s) | 26.4, 27.0 | 30.2, 23.1 (one run unstable) |
| prefill at about 4,000 tokens (tok/s), qwen2.5:3b / qwen3.5:4b | 497 / 369 | 500 / 384 (run 2: 493 / 352) |
| wiki prompt (2,540 tokens) cold prefill (tok/s), qwen2.5:3b / qwen3.5:4b | 511 / 352 | 519 / 389 |
| cold load (ms), qwen2.5:3b / qwen3.5:4b | 641 / 2,009 | 539 / 807 |
| memory allocated, 20 turns (GB), qwen2.5:3b / qwen3.5:4b | 2.4 / 3.4 | 2.4 / 3.3 |
| cached tokens reported | none | wiki prompt repeated: 2,541 and 2,570 |

- Decode is 8 to 14% faster on both models in both pairs of runs; prefill is the same within noise (about 1 to 4%, one pair of runs
  differs the other way). The model memory is the same. This is a modest gain; it is not the 15% prefill gain first hoped for.
- Swap-outs in the scoreboard's memory scenario were 29,772 on 0.30.10 and 0 on 0.40.16 for qwen3.5:4b. That figure is system-wide,
  one reading, and swap on this Mac drifts by itself (about 0.4 GB a minute at idle); it is NOT credited to the engine.

**A 6-turn chat with changing pages through the app's real prompt** (`answer_question`, the stable-first order of #346; each turn a
different set of three of five synthetic pages and a new question, earlier turns kept; 8,192 window; first word is time to the first
streamed text):

| | 0.30.10 | 0.40.16 |
|---|---|---|
| qwen2.5:3b, first word on turns 2 to 6 (s), median | 3.06 (3.38 to 3.01) | 2.88 (3.22 to 2.78) |
| qwen2.5:3b, prompt tokens per turn; cached tokens | 1,969 to 2,208; not reported | 1,920 to 2,069; 338, 491, 547, 638, 699 |
| qwen3.5:4b-q4_K_M, first word on turns 2 to 6 (s), median | 5.74 (5.56 to 6.38) | 5.27 (5.01 to 5.73) |
| qwen3.5:4b-q4_K_M, prompt tokens per turn; cached tokens | 1,995 to 2,162; not reported | 1,940 to 2,100; 0 on every turn |

- The follow-up first word is 6% (qwen2.5:3b) to 8% (qwen3.5:4b) shorter on 0.40.16, in line with the faster prefill, not with prompt reuse:
  prompt reuse is not a difference between the engines.
- qwen2.5:3b reuses 17 to 34% of each follow-up prompt (the part before the changing pages); 0.30.10 does too, it just does not report it
  (the first-word times are equal within the prefill difference).
- qwen3.5:4b (recurrent layers) reuses NOTHING on 0.40.16 (0 cached tokens on every turn): the engine's prefill restore points from 0.33.0
  did not show up for this model, so every follow-up re-reads its whole 1,900 to 2,100 token prompt, the same as on 0.30.10. The speed gain
  that the prompt order of #346 was meant to give does not reach this model on either engine.

**Store upgrade and rollback.** A model folder written by 0.30.10 (qwen2.5:3b, qwen3.5:4b-q4_K_M, bge-m3) was copied (copy-on-write) and opened by
0.40.16: all three models listed, and 0.40.16 added only four small metadata files; every blob and manifest was byte for byte unchanged.
Opening the same folder again with 0.30.10 (rollback) listed the three models and both chat models answered.

**First-run download path, on the real archive, with Anthill's own code** (`download_ollama`, `find_ollama_bin`, `ensure_serving`, in a scratch
home): the pinned checksum was verified, 632 MB unpacked, the managed binary was found, the engine served 6.7 s later and reported 0.40.16
(download 45 s).

**Catalogue.** All 13 exact GGUF tags of #345 and the plain tags of every other catalogue row were accepted at the pull handshake by both
engines on 2026-11-20 (nothing is refused by the old engine; the four rows needing a newer one were removed in #319).

## Not shown

- The full app smoke test on a BUILT app (Rust and PyInstaller are not installed here and nothing was installed). The beta is built by
  GitHub's runners; the plan is to run that build on this Mac after it exists.
- That an EXISTING install gets the new engine (see Known limits).
- Windows, Linux, 8 GB and 24 GB Macs; the MLX build on 0.40.16 is deliberately not used (#345 names the GGUF builds).
- Models other than qwen2.5:3b and qwen3.5:4b-q4_K_M; a large real library (one small folder was upgraded); the 0.40.2 background rewrite.
- Memory over a long chat on 0.40.16 for the GGUF builds other than qwen3.5:4b-q4_K_M (measured flat on 2026-11-19, 48 turns).

## Known limits

- `managed_ollama_bin()` returns whatever engine is already in the data folder, with no version check. A user who has already downloaded
  0.30.10 keeps it after an app update; only fresh installs download 0.40.16. `sizing.bundled_engine_version()` reads the pin, not the engine
  actually in use. Nothing breaks (the exact tags are accepted by both engines and no row needs a newer engine yet), but the faster decode and
  the cached-token counts reach fresh installs only until an upgrade step exists (a separate change).
- The merge-to-GGUF fine-tune route (#273, PR #360) makes a fine-tune registrable on this engine once the trainer image is rebuilt.

## Acceptance

- The pin tests, the contract suite on the recorded versions, the Windows tests and the catalogue engine tests pass.
- This spec holds the tables above, the not-shown list and the known limits.
