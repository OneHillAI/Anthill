# Tasks

1. `anthill/web/db.py`: `TrainingExample` gains `training_eligible` (Boolean, default True) and
   `council_drafts` (Text, nullable/default "").
2. `anthill/council/engine.py`: `CouncilResult` gains `proposals: list[tuple[int, str]]` (populate from
   the existing local `proposals` list in `run_council`, currently discarded after synthesis).
3. `anthill/training/collect.py`: `record_example()` gains `training_eligible: bool = True`,
   `council_drafts: str = ""` optional kwargs, passed straight to the `TrainingExample` row.
4. `anthill/web/app.py`: the chat-turn call site that invokes `record_example()` after a council-
   synthesized answer passes `council_drafts=json.dumps([...])` when `result.synthesized`, and derives
   `training_eligible` from whether the answering backend(s) were self-hosted/open-weight vs. a
   third-party closed-model `anthill/hybrid/` escalation.
5. `anthill/web/templates/settings_organization.html`: a Berget quick-connect card next to "Connect a
   model server you already run" - a button that fills `org_model_endpoint`/note text via JS, no new
   route. A wiki-hosting reminder banner (`{% if not solo and cfg.wiki_hosting == 'local' %}`) pointing
   at the Wiki tab.
6. Provider ordering: reorder/annotate the tiers/provider list so RunPod is the recommended default,
   Lambda secondary, DataCrunch/Verda no longer says "not fully wired up" (correct the annotation).
7. `docs/specs/local-vs-frontier-capability-roadmap.md` and `docs/specs/model-onboarding-and-sovereignty.md`:
   correct R6/R7/R8 in place per proposal.md.
8. Tests: `TrainingExample`/`record_example` new-field round-trip; `CouncilResult.proposals` populated;
   wiki-hosting banner shown/hidden; provider list ordering/annotation.
9. `ruff check` / `ruff format --check` / `mypy` / full pytest.

## Explicitly out of scope
Same as proposal.md.
