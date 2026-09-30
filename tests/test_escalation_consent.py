"""Cloud escalation is fail-closed: no per-use consent => no cloud call (#252).

The decision (`propose_escalation`) is split from execution (`run_escalation`). `maybe_escalate`
performs the outbound call ONLY when a consent callback approves it, and `ask()` passes that gate
through. With no consent callback, the local answer stands and nothing leaves the machine.
"""

from anthill.hybrid import escalate as esc
from anthill.hybrid.escalate import HybridPolicy, maybe_escalate, propose_escalation
from anthill.hybrid.providers import CloudResult

WEAK = "I don't know."  # a weak local answer -> the quality gate proposes escalation


def _policy():
    return HybridPolicy(enabled=True, provider="openrouter", api_key="k")


def _boom_cloud(*a, **k):  # any invocation is a failure: no cloud call may happen without consent
    raise AssertionError("call_cloud must not run without consent")


def _ok_cloud(provider, messages, *, api_key, model=None):
    return CloudResult(answer="cloud says hi", provider="openrouter", model="x")


# ── the library: decision never touches the network ──────────────────────────


def test_propose_escalation_makes_no_network_call(monkeypatch):
    monkeypatch.setattr(esc, "call_cloud", _boom_cloud)
    prop = propose_escalation("obscure q", WEAK, policy=_policy())
    assert prop.proposed is True  # it WOULD escalate...
    assert prop.provider_name and prop._messages  # ...payload is ready, but no call was made


def test_no_consent_callback_is_fail_closed(monkeypatch):
    monkeypatch.setattr(esc, "call_cloud", _boom_cloud)
    out = maybe_escalate("obscure q", WEAK, policy=_policy(), consent=None)
    assert out.escalated is False and out.answer == WEAK


def test_declined_consent_is_fail_closed(monkeypatch):
    monkeypatch.setattr(esc, "call_cloud", _boom_cloud)
    out = maybe_escalate("obscure q", WEAK, policy=_policy(), consent=lambda p: False)
    assert out.escalated is False and out.answer == WEAK


def test_consent_that_raises_is_treated_as_refusal(monkeypatch):
    monkeypatch.setattr(esc, "call_cloud", _boom_cloud)

    def _raise(_proposal):
        raise RuntimeError("boom")

    out = maybe_escalate("obscure q", WEAK, policy=_policy(), consent=_raise)
    assert out.escalated is False


def test_granted_consent_escalates(monkeypatch):
    monkeypatch.setattr(esc, "call_cloud", _ok_cloud)
    seen = {}
    out = maybe_escalate(
        "obscure q",
        WEAK,
        policy=_policy(),
        consent=lambda p: seen.update(provider=p.provider_name) or True,
    )
    assert out.escalated is True and "cloud says hi" in out.answer
    assert seen.get("provider")  # the callback received the proposal descriptor


# ── the ask() surface passes the gate through ────────────────────────────────


class _Weak:
    model = "test"

    def chat(self, messages, **kw):
        return WEAK


def _ws(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    ws = workspace_for("personal", user_id=1)
    ws.init()
    return ws


def test_ask_without_consent_never_escalates(tmp_path, monkeypatch):
    from anthill.wiki import ask as ask_mod

    monkeypatch.setattr(esc, "call_cloud", _boom_cloud)  # raises if ever called
    ws = _ws(tmp_path, monkeypatch)
    answer, _slugs, _hit = ask_mod.ask(
        ws, "obscure q", _Weak(), hybrid_policy=_policy(), consent=None
    )
    assert "Answered by" not in answer  # no escalation annotation; _boom_cloud never fired


def test_ask_with_consent_escalates(tmp_path, monkeypatch):
    from anthill.wiki import ask as ask_mod

    monkeypatch.setattr(esc, "call_cloud", _ok_cloud)
    ws = _ws(tmp_path, monkeypatch)
    answer, _slugs, _hit = ask_mod.ask(
        ws, "obscure q", _Weak(), hybrid_policy=_policy(), consent=lambda p: True
    )
    assert "cloud says hi" in answer
