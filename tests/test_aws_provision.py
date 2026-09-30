"""AWS VPC GPU provisioning: cost guardrails + the launch / guaranteed-teardown / reap
lifecycle. boto3 is not exercised - a fake EC2 client stands in, so the safety-critical
paths (always-terminate, reaper, guardrail refusals) are what's tested."""

import pytest

from anthill.cloud import aws


class _Cfg:
    org_id = 7
    aws_region = "us-east-1"
    aws_access_key_id = "AKIAEXAMPLE"
    aws_secret_access_key_enc = "enc"  # _secret is monkeypatched, so contents don't matter
    aws_instance_type = "g5.xlarge"
    aws_ami_id = "ami-123"
    aws_subnet_id = ""
    aws_security_group_id = ""
    aws_max_runtime_min = 120
    aws_max_cost_usd = "25.00"


def _cfg(**over):
    return type("C", (_Cfg,), over)()


class _FakeEC2:
    def __init__(self):
        self.calls = []
        self.running = []

    def run_instances(self, **kw):
        self.calls.append(("run", kw))
        iid = f"i-{len(self.calls):04d}"
        self.running.append(iid)
        return {"Instances": [{"InstanceId": iid}]}

    def terminate_instances(self, InstanceIds=None):
        self.calls.append(("terminate", InstanceIds))
        self.running = [i for i in self.running if i not in (InstanceIds or [])]
        return {}

    def describe_instances(self, Filters=None):
        self.calls.append(("describe", Filters))
        return {"Reservations": [{"Instances": [{"InstanceId": i} for i in self.running]}]}


@pytest.fixture
def fake_ec2(monkeypatch):
    monkeypatch.setattr(aws, "boto3", object())  # truthy: guardrails clears the boto3 check
    monkeypatch.setattr(aws, "_secret", lambda cfg: "secret")
    fake = _FakeEC2()
    monkeypatch.setattr(aws, "_ec2", lambda cfg: fake)
    return fake


# ── cost guardrails ───────────────────────────────────────────────────────────


def test_estimate_cost():
    assert aws.estimate_cost("g5.xlarge", 60) == 1.01  # ~$1.006/hr
    assert aws.estimate_cost("g5.xlarge", 0) == 0.0


def test_guardrails_ok(fake_ec2):
    ok, _ = aws.guardrails(_cfg())
    assert ok


def test_guardrails_rejects_non_allowlisted_instance(fake_ec2):
    ok, reason = aws.guardrails(_cfg(aws_instance_type="m5.large"))
    assert not ok and "not in the allowed" in reason


def test_guardrails_rejects_over_budget(fake_ec2):
    ok, reason = aws.guardrails(
        _cfg(aws_instance_type="p3.2xlarge", aws_max_runtime_min=600, aws_max_cost_usd="5")
    )
    assert not ok and "cost" in reason.lower()


def test_guardrails_rejects_over_runtime_cap(fake_ec2):
    ok, reason = aws.guardrails(_cfg(aws_max_runtime_min=99999))
    assert not ok and "runtime" in reason.lower()


def test_guardrails_blocks_without_boto3(monkeypatch):
    monkeypatch.setattr(aws, "boto3", None)
    ok, reason = aws.guardrails(_cfg())
    assert not ok and "boto3" in reason


# ── launch / teardown / reap lifecycle ────────────────────────────────────────


def test_provision_gpu_tags_caps_and_self_terminates(fake_ec2):
    inst = aws.provision_gpu(_cfg(), purpose="train")
    kw = fake_ec2.calls[0][1]
    assert kw["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert "shutdown -h" in kw["UserData"]
    tags = kw["TagSpecifications"][0]["Tags"]
    assert {"Key": aws.TAG_KEY, "Value": aws.TAG_VALUE} in tags
    assert {"Key": aws.ORG_TAG_KEY, "Value": "7"} in tags  # per-org isolation tag
    assert inst["instance_id"].startswith("i-") and inst["est_cost_usd"] > 0


def test_provision_test_always_terminates(fake_ec2):
    ok, _detail = aws.provision_test(_cfg())
    assert ok
    kinds = [c[0] for c in fake_ec2.calls]
    assert "run" in kinds and "terminate" in kinds
    assert fake_ec2.running == []  # nothing left running


def test_provision_and_train_terminates_even_when_training_raises(fake_ec2):
    # _run_remote_training is the next build (raises NotImplementedError); the instance
    # MUST still be torn down by the finally - the critical guaranteed-teardown guarantee.
    with pytest.raises(NotImplementedError):
        aws.provision_and_train(_cfg(), "/tmp/gold.jsonl")
    assert fake_ec2.running == []


def test_reap_orphans_scoped_to_this_org(fake_ec2):
    fake_ec2.running = ["i-aaa", "i-bbb"]
    reaped = aws.reap_orphans(_cfg())
    assert set(reaped) == {"i-aaa", "i-bbb"} and fake_ec2.running == []
    # the describe MUST filter by this org's tag (so it can never reap another org's instances)
    describe = next(c for c in fake_ec2.calls if c[0] == "describe")[1]
    assert {"Name": f"tag:{aws.ORG_TAG_KEY}", "Values": ["7"]} in describe


def test_provision_gpu_refuses_outside_guardrails(fake_ec2):
    with pytest.raises(RuntimeError):
        aws.provision_gpu(_cfg(aws_instance_type="m5.large"))
    assert fake_ec2.calls == []  # never launched


def test_default_ami_for_known_and_unknown_region():
    assert aws.default_ami("us-east-1") == "ami-0000f0cf91a6ed38c"
    assert aws.default_ami("  us-east-1  ") == "ami-0000f0cf91a6ed38c"  # trimmed
    assert aws.default_ami("eu-west-1") == ""  # no validated default -> account picks
    assert aws.default_ami("") == ""


def test_provision_gpu_uses_regional_default_when_ami_blank(fake_ec2):
    aws.provision_gpu(_cfg(aws_ami_id=""), purpose="train")  # org left AMI blank
    assert fake_ec2.calls[0][1]["ImageId"] == aws.default_ami("us-east-1")


def test_provision_gpu_prefers_explicit_ami_over_default(fake_ec2):
    aws.provision_gpu(_cfg(aws_ami_id="ami-custom"), purpose="train")
    assert fake_ec2.calls[0][1]["ImageId"] == "ami-custom"


def test_provision_gpu_omits_imageid_when_blank_and_no_regional_default(fake_ec2):
    aws.provision_gpu(_cfg(aws_ami_id="", aws_region="eu-west-1"), purpose="train")
    assert "ImageId" not in fake_ec2.calls[0][1]


def test_iam_policy_is_org_scoped():
    # terminate/stop must be conditioned on BOTH the anthill tag and THIS org's id, so the
    # credentials can never tear down another org's instances.
    import json

    policy = json.loads(aws.iam_policy(7))
    manage = next(s for s in policy["Statement"] if "Terminate" in str(s["Action"]))
    cond = manage["Condition"]["StringEquals"]
    assert cond[f"aws:ResourceTag/{aws.ORG_TAG_KEY}"] == "7"
    assert cond[f"aws:ResourceTag/{aws.TAG_KEY}"] == aws.TAG_VALUE
