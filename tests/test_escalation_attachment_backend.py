"""_build_attachment_backend (anthill/web/app.py): resolves the expert-tier escalation attachment
(OrgSettings.escalation_provider/escalation_provider_key_enc) into a one-off backend, using each
provider's curated escalation_model. Distinct from anthill.web.escalation.build_escalation_backend,
which resolves the account's PRIMARY org/cloud plane - covered separately in test_task_escalation.py
and test_agents_surface.py.
"""

from anthill.inference.openai_compat import OpenAICompatBackend
from anthill.web.app import _ESCALATION_MAX_TOKENS, _INFERENCE_PROVIDERS, _build_attachment_backend
from anthill.web.crypto import encrypt


class _Cfg:
    def __init__(self, escalation_provider="", escalation_provider_key_enc=""):
        self.escalation_provider = escalation_provider
        self.escalation_provider_key_enc = escalation_provider_key_enc


def _decrypt(token):
    from anthill.web.crypto import decrypt

    return decrypt(token)


def test_returns_none_when_nothing_attached():
    assert _build_attachment_backend(_Cfg(), _decrypt) is None


def test_returns_none_when_key_missing():
    assert _build_attachment_backend(_Cfg(escalation_provider="groq"), _decrypt) is None


def test_returns_none_on_a_decrypt_failure():
    cfg = _Cfg(escalation_provider="groq", escalation_provider_key_enc="not-a-real-token")
    assert _build_attachment_backend(cfg, _decrypt) is None


def test_builds_an_openai_compat_backend_with_the_providers_curated_model():
    cfg = _Cfg(escalation_provider="groq", escalation_provider_key_enc=encrypt("gsk_secret"))
    backend = _build_attachment_backend(cfg, _decrypt)
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.model == _INFERENCE_PROVIDERS["groq"]["escalation_model"]
    assert backend.base_url == _INFERENCE_PROVIDERS["groq"]["base_url"]


def test_every_curated_provider_builds_successfully():
    for key in _INFERENCE_PROVIDERS:
        cfg = _Cfg(escalation_provider=key, escalation_provider_key_enc=encrypt("secret"))
        backend = _build_attachment_backend(cfg, _decrypt)
        assert isinstance(backend, OpenAICompatBackend), key
        assert backend.model == _INFERENCE_PROVIDERS[key]["escalation_model"]


def test_escalation_call_is_capped_not_unbounded():
    """#820: an uncapped escalation call on a large flagship model ran ~19s vs ~1.4-3s capped, for a
    single-turn answer that has no business being open-ended."""
    cfg = _Cfg(escalation_provider="berget", escalation_provider_key_enc=encrypt("secret"))
    backend = _build_attachment_backend(cfg, _decrypt)
    assert backend.max_tokens == _ESCALATION_MAX_TOKENS


def test_berget_escalation_model_is_not_the_known_stale_id():
    """#820: Berget removed openai/gpt-oss-120b from its catalog; every handoff 404'd. Pins the curated
    id away from regressing back to a confirmed-dead value (doesn't assert the NEW id stays valid
    forever - that drift is caught by the same class of bug, not by this test)."""
    assert _INFERENCE_PROVIDERS["berget"]["escalation_model"] != "openai/gpt-oss-120b"


def test_curated_escalation_models_are_not_known_reasoning_ids():
    """#854/#857: an escalation answer is shown to the user verbatim, so a reasoning model's chain-of-
    thought becomes the "answer" (Berget's Qwen3.8-27B-FP8 did exactly this, unrecoverably - the CoT
    came back as plain message.content with nothing to strip). Pins each curated id away from a
    confirmed reasoning model per docs/specs/escalation-models-non-reasoning.md; does not assert a
    replacement is non-reasoning forever - a provider can always add reasoning to a model id later,
    which is why this is a pin against known-bad values, not a general reasoning-model detector."""
    assert _INFERENCE_PROVIDERS["berget"]["escalation_model"] != "Qwen/Qwen3.8-27B-FP8"
    assert _INFERENCE_PROVIDERS["infercom"]["escalation_model"] != "MiniMax-M2.7"
