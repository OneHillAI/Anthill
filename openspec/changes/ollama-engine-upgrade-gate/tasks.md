# Tasks

- [x] Write the 0.30.10 to 0.40.16 diff and the open questions in `docs/specs/ollama-engine-upgrade-gate.md`.
- [x] Add `tests/fixtures/ollama/0.30.10/` (`docs`) and `tests/fixtures/ollama/0.40.7/` (`recorded`, two `docs`),
  each with a `provenance.json`, and `record_fixtures.py` to record another version.
- [x] Add `tests/test_ollama_compat_contract.py`: Anthill's parsers over every fixture set, printing provenance.
- [x] Add `tests/test_backup_ollama_files.py`: old-archive restore keeps the relative path, sets an existing file
  aside, undoes a failure; a new backup holds no model files.
- [x] Add `tests/test_ollama_pin.py`: every pin agrees.
- [ ] A recording of a 0.30.10 engine (needs the founder's go to run another version), to replace the `docs` set.
- [ ] Test the training promote path (`ollama create ... ADAPTER ...`) against 0.34.1 and later.
