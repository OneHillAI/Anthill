"""The developer council says what it actually did (docs/specs/asdd-model-roster.md, R4).

Found on two real runs of cli/dev-council.py (2026-10-03): the lead model, a reasoning model, spent the whole
token budget thinking and returned nothing, the script quietly handed back proposal 1 as the "synthesis", and
the result still opened with "verify passed" although no test runner was wired. Pins, with no network (the model
call or the HTTP layer is stubbed):

- a lead that returns nothing is named in the result header, the transcript and the audit record, and the
  fallback proposal is named, not passed off as a synthesis;
- a result nothing verified says "NOT VERIFIED", and is neither recorded as a pass nor curated as an exemplar;
- a verified run still reads "verify passed" and is still curated (the control for the two above);
- a proposal cut off at the token cap (finish_reason "length") is flagged in the transcript and the header;
- a call that ran out of tokens before answering is not retried at the same budget, and says why;
- `dev_council.reasoning_effort` reaches every council call, and the max_completion_tokens switch still works.
"""

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("dev_council", ROOT / "cli" / "dev-council.py")
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)

OK_CMD = f'"{sys.executable}" -c pass'
FAIL_CMD = f'"{sys.executable}" -c "raise SystemExit(1)"'
LEAD_BURNED_BUDGET = dc._reply("", why="token cap reached before any answer, finish_reason length")


def _run_council(tmp_path, monkeypatch, *, lead, proposals=None, test_cmd=None, calls=None):
    """Run dc.main() against stubbed model calls. Returns (result text, transcript, audit records)."""
    proposals = proposals or {"m-a": "draft from a", "m-b": "draft from b"}
    for name in ("ASDD_MODEL_URL", "ASDD_RUNTIME_TOKEN"):
        monkeypatch.setenv(name, "x")
    for i in (1, 2, 3):
        monkeypatch.delenv(f"ASDD_MODEL_URL__COUNCIL_{i}", raising=False)
        monkeypatch.delenv(f"ASDD_RUNTIME_TOKEN__COUNCIL_{i}", raising=False)

    def fake_call_model(member, system, user, max_tokens, **kw):
        if calls is not None:
            calls.append((system, kw))
        if system == dc.PROPOSE_SYS:
            return proposals[member["model"]]
        if system == dc.CRITIQUE_SYS:
            return "REJECT: it misses the criteria"
        if system == dc.SYNTH_SYS:
            return lead
        return ""

    records = []
    monkeypatch.setattr(dc, "call_model", fake_call_model)
    monkeypatch.setattr(
        dc,
        "record",
        lambda root, action, verdict, reasoning, payload, lens=None: records.append(
            {
                "action": action,
                "verdict": verdict,
                "reasoning": reasoning,
                "payload": payload,
                "lens": lens,
            }
        ),
    )
    out, transcript = tmp_path / "out.md", tmp_path / "transcript.json"
    argv = [
        "dev-council",
        "--change",
        "demo",
        "--root",
        str(tmp_path),
        "--models",
        "m-a,m-b,m-lead",
        "--out",
        str(out),
        "--transcript",
        str(transcript),
    ]
    if test_cmd:
        argv += ["--test-cmd", test_cmd]
    monkeypatch.setattr(sys, "argv", argv)
    assert dc.main() == 0
    return out.read_text(), json.loads(transcript.read_text()), records


def _run_record(records):
    return next(r for r in records if r["action"] == "dev-council.run")


# --- a lead that returns nothing ---------------------------------------------------------------------


def test_a_lead_that_returns_nothing_is_named_not_passed_off_as_a_synthesis(tmp_path, monkeypatch):
    text, transcript, records = _run_council(
        tmp_path, monkeypatch, lead=LEAD_BURNED_BUDGET, test_cmd=OK_CMD
    )
    heading = text.splitlines()[0]
    assert "LEAD FAILED" in heading and "not a synthesis" in heading
    assert "m-lead" in text and "token cap reached before any answer" in text
    assert "proposal from m-a" in text
    # The draft is still handed back (a human gets something to start from) ...
    assert transcript["synthesis"] == transcript["proposals"][0]["text"]
    # ... and the transcript says it is not the lead's.
    assert transcript["lead_failed"] is True
    assert transcript["fallback_proposal"] == "m-a"
    assert "token cap" in transcript["lead_failure"]
    # The audit record is not a pass and carries the flag; nothing is curated as an exemplar.
    run = _run_record(records)
    assert run["verdict"] != "pass" and run["payload"]["lead_failed"] is True
    assert "LEAD FAILED" in run["reasoning"]
    assert not [r for r in records if r["action"] == "dev-council.synthesis"]


def test_a_healthy_lead_is_not_labelled_failed(tmp_path, monkeypatch):
    text, transcript, records = _run_council(
        tmp_path, monkeypatch, lead="the synthesis\nRATIONALE: it fits", test_cmd=OK_CMD
    )
    assert text.splitlines()[0] == "# Developer council result (3 models, verify passed)"
    assert "LEAD FAILED" not in text and "NOT VERIFIED" not in text
    assert transcript["lead_failed"] is False and transcript["fallback_proposal"] == ""
    assert transcript["synthesis"] == "the synthesis\nRATIONALE: it fits"
    run = _run_record(records)
    assert run["verdict"] == "pass" and run["payload"]["verify_pass"] is True
    assert [r["lens"] for r in records if r["action"] == "dev-council.synthesis"] == [
        "council-synthesis"
    ]


# --- a result nothing verified -----------------------------------------------------------------------


def test_an_unverified_result_says_not_verified(tmp_path, monkeypatch):
    text, transcript, records = _run_council(tmp_path, monkeypatch, lead="the synthesis")
    heading = text.splitlines()[0]
    assert "not verified" in heading.lower() and "verify passed" not in text
    assert "no test runner wired" in text
    assert transcript["verify"]["verified"] is False
    run = _run_record(records)
    assert run["verdict"] == "unverified"
    assert run["payload"]["verified"] is False and run["payload"]["verify_pass"] is False
    assert "not verified" in run["reasoning"]
    assert not [r for r in records if r["action"] == "dev-council.synthesis"]


def test_a_failed_verification_still_reads_failed(tmp_path, monkeypatch):
    text, _transcript, records = _run_council(
        tmp_path, monkeypatch, lead="the synthesis", test_cmd=FAIL_CMD
    )
    assert "verify FAILED" in text.splitlines()[0] and "NOT VERIFIED" not in text
    assert _run_record(records)["verdict"] == "changes-requested"


# --- text cut off at the token cap -------------------------------------------------------------------


def test_a_proposal_cut_off_at_the_token_cap_is_flagged(tmp_path, monkeypatch):
    cut = dc._reply("**Thinking about the change**\n\nI am now", truncated=True)
    text, transcript, records = _run_council(
        tmp_path,
        monkeypatch,
        lead="the synthesis",
        proposals={"m-a": "draft from a", "m-b": cut},
        test_cmd=OK_CMD,
    )
    assert [p["truncated"] for p in transcript["proposals"]] == [False, True]
    assert "Cut off at the token cap" in text and "proposal from m-b" in text
    assert "m-a" not in text.split("\n\n")[1]  # only the cut-off proposal is named in the note
    assert _run_record(records)["payload"]["truncated_proposals"] == 1


def test_a_synthesis_cut_off_at_the_token_cap_is_flagged_and_not_an_exemplar(tmp_path, monkeypatch):
    cut = dc._reply("the synthesis, half of it", truncated=True)
    text, transcript, records = _run_council(tmp_path, monkeypatch, lead=cut, test_cmd=OK_CMD)
    assert transcript["synthesis_truncated"] is True
    assert "final text below was cut off" in text
    assert not [r for r in records if r["action"] == "dev-council.synthesis"]


def _fake_urlopen(responses, bodies):
    """A urlopen stand-in: returns/raises each response in turn and keeps every request body."""
    queue = list(responses)

    class _Resp:
        def __init__(self, payload):
            self._data = json.dumps(payload).encode()

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)

    return urlopen


def _completion(content, finish_reason):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


MEMBER = {"model": "m", "url": "http://endpoint.test/v1", "token": "t"}


def test_call_model_marks_a_reply_that_hit_the_token_cap(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        dc.urllib.request,
        "urlopen",
        _fake_urlopen([_completion("an unfinished thought", "length")], bodies),
    )
    reply = dc.call_model(MEMBER, "s", "u", 100)
    assert reply == "an unfinished thought" and reply.truncated is True
    monkeypatch.setattr(
        dc.urllib.request, "urlopen", _fake_urlopen([_completion("a whole answer", "stop")], bodies)
    )
    assert dc.call_model(MEMBER, "s", "u", 100).truncated is False


def test_a_call_that_ran_out_of_tokens_before_answering_is_not_retried_and_says_why(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        dc.urllib.request, "urlopen", _fake_urlopen([_completion("", "length")] * 3, bodies)
    )
    reply = dc.call_model(MEMBER, "s", "u", 8000)
    assert not reply and "token cap reached before any answer" in reply.why
    assert len(bodies) == 1  # the same budget would just be spent again


def test_a_plain_empty_reply_is_still_retried(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        dc.urllib.request,
        "urlopen",
        _fake_urlopen([_completion("", "stop"), _completion("ok", "stop")], bodies),
    )
    assert dc.call_model(MEMBER, "s", "u", 100) == "ok" and len(bodies) == 2


# --- the request the council sends -------------------------------------------------------------------


def test_reasoning_effort_is_sent_only_when_asked_for(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        dc.urllib.request,
        "urlopen",
        _fake_urlopen([_completion("a", "stop"), _completion("b", "stop")], bodies),
    )
    dc.call_model(MEMBER, "s", "u", 100)
    dc.call_model(MEMBER, "s", "u", 100, effort="low")
    assert "reasoning_effort" not in bodies[0]
    assert bodies[1]["reasoning_effort"] == "low"


def test_the_max_completion_tokens_switch_still_works(monkeypatch):
    bodies = []
    rejected = urllib.error.HTTPError(
        "http://endpoint.test",
        400,
        "Bad Request",
        {},
        io.BytesIO(b'{"error": "Unsupported parameter: max_tokens. Use max_completion_tokens"}'),
    )
    monkeypatch.setattr(
        dc.urllib.request, "urlopen", _fake_urlopen([rejected, _completion("ok", "stop")], bodies)
    )
    assert dc.call_model(MEMBER, "s", "u", 123) == "ok"
    assert bodies[0]["max_tokens"] == 123 and "max_tokens" not in bodies[1]
    assert bodies[1]["max_completion_tokens"] == 123


def test_the_configured_reasoning_effort_reaches_every_council_call(tmp_path, monkeypatch):
    (tmp_path / ".asdd.yml").write_text("dev_council:\n  reasoning_effort: low\n")
    calls = []
    _run_council(tmp_path, monkeypatch, lead="the synthesis", test_cmd=FAIL_CMD, calls=calls)
    assert [system for system, _kw in calls].count(
        dc.SYNTH_SYS
    ) == 2  # the synthesis and the refine
    assert all(kw.get("effort") == "low" for _system, kw in calls)


@pytest.mark.parametrize("config", ["", "dev_council:\n  max_tokens: 100\n"])
def test_no_reasoning_effort_is_sent_by_default(tmp_path, monkeypatch, config):
    (tmp_path / ".asdd.yml").write_text(config)
    calls = []
    _run_council(tmp_path, monkeypatch, lead="the synthesis", calls=calls)
    assert calls and all(not kw.get("effort") for _system, kw in calls)


# --- a member that never answered --------------------------------------------------------------------


def test_a_member_that_gave_no_answer_is_named_not_counted_as_present(tmp_path, monkeypatch):
    silent = dc._reply("", why="HTTP 401")
    text, transcript, records = _run_council(
        tmp_path,
        monkeypatch,
        lead="the synthesis",
        proposals={"m-a": "draft from a", "m-b": silent},
        test_cmd=OK_CMD,
    )
    assert "NO ANSWER FROM m-b" in text.splitlines()[0]
    assert "m-b gave no answer at propose (HTTP 401)" in text
    steps = [(f["model"], f["step"]) for f in transcript["failures"]]
    assert ("m-b", "propose") in steps and ("m-b", "critique") not in steps  # m-b still critiques
    assert _run_record(records)["payload"]["failed_calls"] == len(transcript["failures"])


def test_a_run_where_every_member_answers_names_nobody(tmp_path, monkeypatch):
    text, transcript, records = _run_council(tmp_path, monkeypatch, lead="s", test_cmd=OK_CMD)
    assert transcript["failures"] == [] and "NO ANSWER" not in text
    assert _run_record(records)["payload"]["failed_calls"] == 0


def test_when_every_proposer_fails_the_message_says_why(tmp_path, monkeypatch, capsys):
    down = dc._reply("", why="HTTP 503")
    _run_council_no_out(tmp_path, monkeypatch, proposals={"m-a": down, "m-b": down})
    printed = capsys.readouterr().out
    assert "no proposer produced a draft" in printed
    assert "m-a: HTTP 503" in printed and "m-b: HTTP 503" in printed


def _run_council_no_out(tmp_path, monkeypatch, proposals):
    """main() with no --out, so the all-failed message goes to stdout."""
    for name in ("ASDD_MODEL_URL", "ASDD_RUNTIME_TOKEN"):
        monkeypatch.setenv(name, "x")
    for i in (1, 2, 3):
        monkeypatch.delenv(f"ASDD_MODEL_URL__COUNCIL_{i}", raising=False)
        monkeypatch.delenv(f"ASDD_RUNTIME_TOKEN__COUNCIL_{i}", raising=False)
    monkeypatch.setattr(dc, "call_model", lambda member, *a, **kw: proposals[member["model"]])
    monkeypatch.setattr(dc, "record", lambda *a, **kw: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["dev-council", "--change", "demo", "--root", str(tmp_path), "--models", "m-a,m-b,m-lead"],
    )
    assert dc.main() == 0


# --- the repo's own council config -------------------------------------------------------------------


def test_the_repo_council_runs_the_reasoning_models_at_low_effort():
    """GPT-5.6 spent the whole 8000-token budget thinking and answered nothing at the default effort."""
    council = dc.load_config(ROOT)["dev_council"]
    assert council["reasoning_effort"] == "low"
    assert len(dc.as_model_list(council["models"])) == 3
