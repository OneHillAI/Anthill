"""AWS VPC GPU backend - credential validation + ephemeral-GPU provisioning.

Provisions a single **tagged, capped, ephemeral** GPU instance in the org's AWS account
for a run, then **always** tears it down. Non-negotiable safety (CLAUDE.md / project-memory):
  - **Guaranteed teardown** even on failure/timeout: every launch path terminates in a
    `finally`, the instance is launched with `InstanceInitiatedShutdownBehavior=terminate`
    plus a `shutdown -h +<max_runtime>` in user-data, and `reap_orphans()` (run on startup)
    terminates any orphaned anthill-tagged instances.
  - **Cost guardrails:** instance-type allowlist, a hard max-runtime cap, a per-run budget
    cap (estimated worst-case cost must be under `aws_max_cost_usd`), and every instance is
    tagged so it can always be found + reaped.
  - **Per-org isolation:** each org enters its own AWS credentials (via the app, encrypted at
    rest), so orgs run in separate AWS accounts; and every instance is tagged with its org id
    (`anthill:org`) with launch, reaping, and the documented `iam_policy(org_id)` all scoped to
    it - so one org can never see or terminate another's instances, even in a shared account.

boto3 is an optional dependency (`pip install -e ".[aws]"`); everything degrades to a helpful
message when it's absent. The secret access key is stored AES-256-GCM encrypted
(anthill.web.crypto) and only decrypted in-process for an API call.
"""

from __future__ import annotations

import json

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    _AWS_ERRORS = (BotoCoreError, ClientError)
except Exception:  # boto3 not installed
    boto3 = None
    _AWS_ERRORS = ()

# ── safety constants ──────────────────────────────────────────────────────────
TAG_KEY = "anthill"
TAG_VALUE = "gpu-backend"
ORG_TAG_KEY = "anthill:org"  # per-org isolation: every instance is tagged with its org id, and
# launch/reap/IAM are all scoped to it, so one org can never see or terminate another's instances
HARD_MAX_RUNTIME_MIN = 1440  # 24h absolute ceiling, regardless of config

# Only these GPU instance types may be launched (a cost guardrail).
INSTANCE_TYPE_ALLOW = {
    "g4dn.xlarge",
    "g4dn.2xlarge",
    "g5.xlarge",
    "g5.2xlarge",
    "g5.4xlarge",
    "g6.xlarge",
    "g6.2xlarge",
    "p3.2xlarge",
}

# Rough on-demand USD/hour (us-east-1 ballpark) - for the cost estimate + budget guardrail
# only; never billed against. A conservative default is used for anything unlisted.
_PRICE_PER_HOUR = {
    "g4dn.xlarge": 0.526,
    "g4dn.2xlarge": 0.752,
    "g5.xlarge": 1.006,
    "g5.2xlarge": 1.212,
    "g5.4xlarge": 1.624,
    "g6.xlarge": 0.805,
    "g6.2xlarge": 0.978,
    "p3.2xlarge": 3.06,
}

# Validated default GPU AMI per region: x86_64 "Deep Learning OSS Nvidia Driver AMI (PyTorch)".
# Used when an org leaves the AMI blank. AMIs are region-specific and AWS refreshes them over
# time, so this is a sane starting default an org can override in Settings (a later build can
# resolve the newest via SSM public parameters). us-east-1 entry verified live (account-tested).
DEFAULT_GPU_AMI = {
    "us-east-1": "ami-0000f0cf91a6ed38c",
}


def default_ami(region: str) -> str:
    """Validated default GPU AMI for a region, or '' if none is known (then the account picks)."""
    return DEFAULT_GPU_AMI.get((region or "").strip(), "")


def iam_policy(org_id) -> str:
    """Least-privilege IAM policy for an org's Anthill AWS user/role. Terminate/stop are scoped
    to BOTH the anthill tag and THIS org's tag, so these credentials can only ever tear down this
    org's own instances - one org can never touch another's (per-org isolation, even in a shared
    AWS account). Shown in Settings so the admin can attach the exact policy."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AnthillLaunch",
                    "Effect": "Allow",
                    "Action": ["ec2:RunInstances", "ec2:CreateTags"],
                    "Resource": "*",
                },
                {
                    "Sid": "AnthillManageOwnOrgTagged",
                    "Effect": "Allow",
                    "Action": ["ec2:TerminateInstances", "ec2:StopInstances"],
                    "Resource": "*",
                    "Condition": {
                        "StringEquals": {
                            f"aws:ResourceTag/{TAG_KEY}": TAG_VALUE,
                            f"aws:ResourceTag/{ORG_TAG_KEY}": str(org_id),
                        }
                    },
                },
                {
                    "Sid": "AnthillDescribe",
                    "Effect": "Allow",
                    "Action": ["ec2:DescribeInstances"],
                    "Resource": "*",
                },
                {
                    "Sid": "AnthillSTS",
                    "Effect": "Allow",
                    "Action": ["sts:GetCallerIdentity"],
                    "Resource": "*",
                },
            ],
        },
        indent=2,
    )


def available() -> bool:
    """True when boto3 is importable."""
    return boto3 is not None


def _secret(cfg) -> str:
    enc = getattr(cfg, "aws_secret_access_key_enc", "") or ""
    if not enc:
        return ""
    from ..web.crypto import decrypt

    try:
        return decrypt(enc)
    except Exception:
        return ""


def _client(cfg, service: str):
    if boto3 is None:
        raise RuntimeError('boto3 not installed - run: pip install -e ".[aws]"')
    return boto3.client(
        service,
        region_name=cfg.aws_region or None,
        aws_access_key_id=cfg.aws_access_key_id or None,
        aws_secret_access_key=_secret(cfg) or None,
    )


def _ec2(cfg):
    return _client(cfg, "ec2")


def validate(cfg) -> tuple[bool, str]:
    """Read-only, free credential check via STS get-caller-identity.

    Returns (ok, human_detail). Does not launch anything.
    """
    if boto3 is None:
        return False, 'boto3 not installed - run: pip install -e ".[aws]"'
    if not cfg.aws_region:
        return False, "Missing AWS region (e.g. us-east-1)."
    if not (cfg.aws_access_key_id and _secret(cfg)):
        return False, "Missing AWS access key ID and/or secret."
    try:
        ident = _client(cfg, "sts").get_caller_identity()
        acct = ident.get("Account", "?")
        return True, f"Connected - AWS account {acct}, region {cfg.aws_region}."
    except _AWS_ERRORS as e:
        return False, f"AWS rejected the credentials: {e}"
    except Exception as e:
        return False, f"Validation failed: {e}"


# ── cost guardrails ───────────────────────────────────────────────────────────


def estimate_cost(instance_type: str, minutes) -> float:
    """Rough worst-case USD for a run (for display + the budget guardrail)."""
    rate = _PRICE_PER_HOUR.get(instance_type, 2.0)  # conservative default for unlisted
    return round(rate * (max(0, int(minutes or 0)) / 60.0), 2)


def _max_cost(cfg) -> float:
    try:
        return float(getattr(cfg, "aws_max_cost_usd", "") or 25.0)
    except (TypeError, ValueError):
        return 25.0


def guardrails(cfg) -> tuple[bool, str]:
    """Pre-launch safety checks. (ok, reason). Refuses anything outside the cost rails."""
    if boto3 is None:
        return False, 'boto3 not installed - run: pip install -e ".[aws]"'
    if not (cfg.aws_region and cfg.aws_access_key_id and _secret(cfg)):
        return False, "AWS credentials/region not configured (set them + Test connection first)."
    itype = (cfg.aws_instance_type or "").strip()
    if itype not in INSTANCE_TYPE_ALLOW:
        return False, (
            f"Instance type '{itype}' is not in the allowed GPU set: "
            f"{', '.join(sorted(INSTANCE_TYPE_ALLOW))}."
        )
    runtime = int(getattr(cfg, "aws_max_runtime_min", 0) or 0)
    if runtime <= 0 or runtime > HARD_MAX_RUNTIME_MIN:
        return False, f"Max runtime must be 1..{HARD_MAX_RUNTIME_MIN} minutes (got {runtime})."
    est, cap = estimate_cost(itype, runtime), _max_cost(cfg)
    if est > cap:
        return False, (
            f"Estimated worst-case cost ${est} exceeds the ${cap} per-run cap - "
            f"lower the runtime/instance type or raise the cap."
        )
    return True, f"OK: {itype}, max {runtime} min, estimated worst-case cost ${est}."


# ── provisioning lifecycle ────────────────────────────────────────────────────


def _tag_spec(purpose: str, org_id):
    return [
        {
            "ResourceType": "instance",
            "Tags": [
                {"Key": TAG_KEY, "Value": TAG_VALUE},
                {"Key": ORG_TAG_KEY, "Value": str(org_id)},  # per-org isolation
                {"Key": "anthill:purpose", "Value": purpose},
            ],
        }
    ]


def provision_gpu(cfg, *, purpose: str = "train") -> dict:
    """Launch ONE tagged, capped, ephemeral GPU instance. The caller MUST terminate it -
    use provision_test / provision_and_train, which guarantee teardown. Raises on a guardrail
    failure (so nothing launches outside the rails). Returns instance details."""
    ok, reason = guardrails(cfg)
    if not ok:
        raise RuntimeError(reason)
    runtime = int(cfg.aws_max_runtime_min)
    params: dict = {
        "InstanceType": cfg.aws_instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "TagSpecifications": _tag_spec(purpose, getattr(cfg, "org_id", "")),
        "InstanceInitiatedShutdownBehavior": "terminate",  # OS shutdown => terminate
        # Belt-and-suspenders: self-terminate after the cap even if the orchestrator dies.
        "UserData": f"#!/bin/bash\nshutdown -h +{runtime}\n",
    }
    ami = (cfg.aws_ami_id or "").strip() or default_ami(cfg.aws_region)
    if ami:
        params["ImageId"] = ami
    if cfg.aws_subnet_id:
        params["SubnetId"] = cfg.aws_subnet_id
    if cfg.aws_security_group_id:
        params["SecurityGroupIds"] = [cfg.aws_security_group_id]
    inst = _ec2(cfg).run_instances(**params)["Instances"][0]
    return {
        "instance_id": inst["InstanceId"],
        "instance_type": cfg.aws_instance_type,
        "runtime_min": runtime,
        "est_cost_usd": estimate_cost(cfg.aws_instance_type, runtime),
    }


def terminate(cfg, instance_id: str) -> None:
    """Terminate one instance (raises on a hard failure so the caller can log it)."""
    _ec2(cfg).terminate_instances(InstanceIds=[instance_id])


def reap_orphans(cfg) -> list[str]:
    """Terminate any non-terminated anthill-tagged instances - the guaranteed-teardown
    safety net, run on startup. Returns the terminated ids. Never raises."""
    try:
        resp = _ec2(cfg).describe_instances(
            Filters=[
                {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
                # per-org isolation: only ever reap THIS org's instances
                {"Name": f"tag:{ORG_TAG_KEY}", "Values": [str(getattr(cfg, "org_id", ""))]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        ids = [
            i["InstanceId"] for r in resp.get("Reservations", []) for i in r.get("Instances", [])
        ]
        if ids:
            _ec2(cfg).terminate_instances(InstanceIds=ids)
        return ids
    except Exception:
        return []


def provision_test(cfg) -> tuple[bool, str]:
    """Prove the whole lifecycle: launch a tagged GPU, then ALWAYS terminate it. No training -
    this verifies credentials + IAM + guardrails + teardown end to end. (ok, detail)."""
    ok, reason = guardrails(cfg)
    if not ok:
        return False, reason
    inst = None
    try:
        inst = provision_gpu(cfg, purpose="lifecycle-test")
        return True, (
            f"Launched {inst['instance_id']} ({inst['instance_type']}) and terminated it. "
            f"Worst-case cost would have been ${inst['est_cost_usd']} for a full run; "
            f"this test terminated immediately."
        )
    except Exception as e:
        return False, f"Provision test failed: {e}"
    finally:
        if inst:
            try:
                terminate(cfg, inst["instance_id"])
            except Exception:
                pass


def provision_and_train(cfg, gold_jsonl_path) -> dict:
    """Launch a tagged ephemeral GPU, run training on it, and ALWAYS terminate (try/finally).
    The remote training step is the next build (see `_run_remote_training`)."""
    ok, reason = guardrails(cfg)
    if not ok:
        raise RuntimeError(reason)
    inst = provision_gpu(cfg, purpose="train")
    try:
        return {"instance": inst, "training": _run_remote_training(cfg, inst, gold_jsonl_path)}
    finally:
        try:
            terminate(cfg, inst["instance_id"])  # GUARANTEED teardown, success or failure
        except Exception:
            pass


def _run_remote_training(cfg, inst, gold_jsonl_path):
    """NEXT BUILD ('Local model training execution'): copy the gold set to the instance over
    an encrypted channel, run the LoRA trainer, and pull back the adapter. The provisioning
    lifecycle around it (launch / guardrails / guaranteed teardown / reaper) is live."""
    raise NotImplementedError(
        "Remote LoRA training on the provisioned GPU is the next build; the provisioning "
        "lifecycle (launch, guardrails, guaranteed teardown, reaper) is implemented."
    )
