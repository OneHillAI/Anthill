"""_training_eligible(cfg): the gate that decides whether a chat turn's TrainingExample is eligible
for a later fine-tuning export (record_example()'s training_eligible field, added in #661 but never
actually computed at its one real call site until now). Verified against every current provider's
real terms before wiring this: none of Berget/Groq/Infercom restrict using their output to train
another model (their "no training" clauses are the PROVIDER not training on the CUSTOMER's data, the
opposite direction), and none of model_catalog.json's licenses (Apache-2.0, MIT, Gemma, Llama
Community, NVIDIA Open Model, OpenMDW-1.1) restrict output use either - so this evaluates True for
every account today. The point of wiring it now is the extension point: the day a genuinely
training-restricted provider is added to _INFERENCE_PROVIDERS, this starts protecting automatically.
"""

import anthill.web.app as app_mod
from anthill.web.app import _INFERENCE_PROVIDERS, _training_eligible


class _Cfg:
    def __init__(self, org_provider=""):
        self.org_provider = org_provider


def test_local_onprem_is_eligible():
    assert _training_eligible(_Cfg(org_provider="onprem")) is True


def test_self_provisioned_cloud_is_eligible():
    assert _training_eligible(_Cfg(org_provider="runpod")) is True
    assert _training_eligible(_Cfg(org_provider="lambda")) is True


def test_blank_provider_is_eligible():
    assert _training_eligible(_Cfg(org_provider="")) is True


def test_every_live_inference_provider_is_eligible_today():
    for key in _INFERENCE_PROVIDERS:
        assert _training_eligible(_Cfg(org_provider=key)) is True, key
        assert _INFERENCE_PROVIDERS[key]["training_restricted"] is False, key


def test_a_training_restricted_provider_is_not_eligible(monkeypatch):
    fake = dict(_INFERENCE_PROVIDERS)
    fake["closed-example"] = {
        "name": "Closed Example",
        "base_url": "https://example.invalid/v1",
        "training_restricted": True,
    }
    monkeypatch.setattr(app_mod, "_INFERENCE_PROVIDERS", fake)
    assert _training_eligible(_Cfg(org_provider="closed-example")) is False


def test_provider_lookup_is_case_and_whitespace_insensitive():
    assert _training_eligible(_Cfg(org_provider=" Groq ")) is True
