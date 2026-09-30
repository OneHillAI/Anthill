"""Run a live provisioning (or teardown) job for an org and persist the result.

The Organization page dispatches these off-thread (provisioning spins up a real, billable GPU and can
take a while). They load the org's saved choice + provider API key, call the provider's
``Provisioner``, and write the outcome back to ``OrgSettings`` (status + endpoint + teardown handle).
Kept free of the web layer (only DB + crypto + hosting) so the orchestration is unit-testable with an
injected client - no SDK, no live account, no real spend.
"""

from __future__ import annotations

import json
import os
import time

from sqlalchemy.orm import sessionmaker

from ..hosting import lambda_provision, source
from ..hosting import provision as prov
from ..hosting.provision import ProvisionSpec
from .db import OrgSettings


def _mirror_lead_into_council(db, cfg) -> None:
    """Keep org_council_members[0] (the lead) in sync with the legacy singleton columns whenever
    provision_org/teardown_org/_fail touch them - otherwise the ordered-list column (which Phase 1b's
    save route and any future council reader treats as the source of truth) goes stale the moment a
    provision/teardown action runs outside of a settings save. A member beyond index 0 (a reviewer) is
    left untouched; this only ever writes slot 0.

    Refreshes org_council_members from the DB immediately before merging, rather than trusting cfg's
    in-memory value - provision_org/teardown_org hold `cfg` for the ENTIRE duration of a real
    provisioning/teardown call (can be minutes), and a reviewer's provision_council_member/
    teardown_council_member runs concurrently in its own DB session, committing its own slot
    independently. Without this refresh, this function's own commit would silently overwrite whatever
    the reviewer just wrote with the stale full-list snapshot this session loaded when IT started."""
    db.refresh(cfg, attribute_names=["org_council_members"])
    try:
        members = json.loads(cfg.org_council_members or "[]")
    except (TypeError, ValueError):
        members = []
    if not isinstance(members, list):
        members = []
    lead = dict(members[0]) if members else {}
    provider_config: dict = {}
    if cfg.org_lambda_ssh_keys or cfg.org_lambda_instance_type:
        provider_config["ssh_keys"] = cfg.org_lambda_ssh_keys
        provider_config["instance_type"] = cfg.org_lambda_instance_type
    if cfg.org_lambda_tunnel_key_enc or cfg.org_lambda_tunnel_port:
        provider_config["tunnel_key_enc"] = cfg.org_lambda_tunnel_key_enc
        provider_config["tunnel_local_port"] = cfg.org_lambda_tunnel_port
        provider_config["tunnel_remote_host"] = cfg.org_lambda_tunnel_host
    lead.update(
        {
            "endpoint": cfg.org_model_endpoint or "",
            "provider": cfg.org_provider or "",
            "model": cfg.org_model or "",
            "params_b": cfg.org_model_params or "",
            "region": cfg.org_region or "",
            "gpu_tier": cfg.org_gpu or "",
            "quantized": bool(cfg.org_model_quantized),
            "model_key_enc": cfg.org_model_key_enc or "",
            "provision_key_enc": cfg.org_provision_key_enc or "",
            "hf_token_enc": cfg.org_hf_token_enc or "",
            "backend_handle": cfg.org_backend_handle or "",
            "backend_status": cfg.org_backend_status or "unconfigured",
            "backend_detail": cfg.org_backend_detail or "",
            "lifecycle": lead.get("lifecycle") or "vpc",
            "provider_config": provider_config,
        }
    )
    cfg.org_council_members = json.dumps([lead, *members[1:]])


def _spec_from_cfg(cfg) -> ProvisionSpec:
    try:
        params = float(cfg.org_model_params) if cfg.org_model_params else 0.0
    except ValueError:
        params = 0.0
    # The picker stores a friendly display name; resolve it to the concrete id the serving stack needs -
    # an Ollama tag on-prem, a Hugging Face repo id for a cloud/neocloud GPU (vLLM) - so the admin never
    # has to type an id. A raw id typed by an advanced user passes through unchanged.
    provider = cfg.org_provider or ""
    try:
        prefer_hf = prov.tier_of(provider) != "onprem"
    except prov.ProvisionError:
        prefer_hf = True
    model = source.servable_id(
        cfg.org_model or "",
        prefer_hf=prefer_hf,
        quantized=bool(getattr(cfg, "org_model_quantized", False)),
    )
    # Single-node tensor-parallel (docs/specs/multi-gpu-tensor-parallel-serving.md): derived from the
    # Lambda instance type the admin already typed, not a separately-stored field - see
    # lambda_provision.gpu_count_from_instance_type's docstring. Meaningless for every other provider.
    gpu_count = 1
    if provider == "lambda":
        gpu_count = lambda_provision.gpu_count_from_instance_type(
            cfg.org_lambda_instance_type or ""
        )
    return ProvisionSpec(
        provider=provider,
        model=model,
        params_b=params,
        region=cfg.org_region or "",
        serve_wiki=bool(cfg.org_serve_wiki),
        gpu_count=gpu_count,
    )


# Providers with a live provisioner whose provision()/teardown() take an injectable account client.
_LIVE_PROVIDERS = {"runpod", "lambda"}


def _decrypt_opt(enc) -> str:
    """Decrypt an optional encrypted column; "" if unset or undecryptable."""
    from .crypto import decrypt

    if not enc:
        return ""
    try:
        return decrypt(enc)
    except Exception:
        return ""


def _provider_key(cfg) -> str:
    return _decrypt_opt(cfg.org_provision_key_enc)


def _live_client(cfg):
    """Build the real account client for a live provider from the org's encrypted provisioning key."""
    key = _provider_key(cfg)
    if cfg.org_provider == "runpod":
        from ..hosting.runpod_provision import _RealRunpodClient

        return _RealRunpodClient(key)
    if cfg.org_provider == "lambda":
        from ..hosting.lambda_provision import _RealLambdaClient

        return _RealLambdaClient(key)
    return None


# Providers that can auto-provision the always-on wiki/backend host alongside the model (RunPod first).
_WIKI_HOST_PROVIDERS = {"runpod"}


def _wiki_autoprovision_enabled() -> bool:
    """Auto-provisioning the always-on backend host is GATED OFF until its prerequisites ship: the
    official backend image is public (so any org's cloud can pull it) AND the pod is seeded with the org's
    data + the team cut over. Until then the backend runs on this device and provisioning the model must
    NOT silently bring up an empty/unreachable backend pod. Flip ANTHILL_WIKI_HOST_AUTOPROVISION on (e.g.
    for Onehill's own pre-launch testing with registry auth on the pod), and on by default at launch."""
    return os.environ.get("ANTHILL_WIKI_HOST_AUTOPROVISION", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _wiki_client(cfg):
    """Build the real wiki/backend-host client for a provider that supports it (RunPod today)."""
    if cfg.org_provider == "runpod":
        from ..hosting.wiki_host import _RealRunpodWikiClient

        return _RealRunpodWikiClient(_provider_key(cfg))
    return None


def _provision_wiki_host_if_requested(db, cfg, spec, *, wiki_client, build, sleep) -> None:
    """After the model endpoint is up, optionally stand up the always-on backend pod in the same cloud and
    record it. Best-effort: a failure here never unwinds the already-provisioned model - it is noted in the
    status detail and the admin can retry. The live client is built only when ``build`` (production); tests
    inject ``wiki_client`` (or neither, to skip the live step). Seeding the pod with the org's data + cutting
    the team over to it are the next stage; this provisions and records the host."""
    if not (spec.serve_wiki and cfg.org_provider in _WIKI_HOST_PROVIDERS):
        return
    client = wiki_client
    if client is None:
        if not build:
            return  # injected model client but no wiki client -> skip the live wiki step (tests)
        if not _wiki_autoprovision_enabled():
            cfg.org_backend_detail = (
                f"{cfg.org_backend_detail} The wiki/backend will run on your org cloud automatically; "
                "this switches on at launch (the backend image is not public yet). Until then it runs on "
                "this device."
            ).strip()[:255]
            return
        try:
            client = _wiki_client(cfg)
        except Exception as e:
            cfg.org_backend_detail = (f"{cfg.org_backend_detail} Wiki host not ready: {e}").strip()[
                :255
            ]
            return
    if client is None:
        return
    from ..hosting.wiki_host import provision_wiki_host

    try:
        pod_id, url = provision_wiki_host(
            name=f"anthill-wiki-{cfg.org_id}", client=client, sleep=sleep
        )
    except Exception as e:
        cfg.org_backend_detail = (f"{cfg.org_backend_detail} Wiki host not ready: {e}").strip()[
            :255
        ]
        return
    cfg.org_wiki_pod_handle = pod_id
    cfg.wiki_vpc_url = url
    cfg.wiki_hosting = "vpc"
    cfg.org_backend_detail = (f"{cfg.org_backend_detail} Backend pod up at {url}.").strip()[:255]


def _provider_provision_kw(cfg) -> dict:
    """Provider-specific launch kwargs derived from the org's saved config.

    Lambda serves on an always-on GPU VM, so it needs the admin's registered SSH key name(s) and an
    instance type (region rides on the spec). RunPod maps the selected GPU tier to its serverless pool
    id. Only non-empty values are passed, so blanks fall back to the provisioner defaults / env. Other
    providers take no extra launch kwargs.
    """
    from ..hosting import sizing

    if cfg.org_provider == "runpod":
        kw: dict = {}
        tier = sizing.gpu_tier(cfg.org_gpu)
        if tier and tier.runpod_pool:
            kw["gpu_ids"] = tier.runpod_pool
        token = _decrypt_opt(cfg.org_hf_token_enc)
        if token:
            kw["hf_token"] = token
        warm_workers = int(getattr(cfg, "org_warm_workers", 0) or 0)
        if warm_workers > 0:
            kw["min_workers"] = warm_workers
        return kw
    if cfg.org_provider != "lambda":
        return {}
    kw: dict = {}
    keys = [k.strip() for k in (cfg.org_lambda_ssh_keys or "").split(",") if k.strip()]
    if keys:
        kw["ssh_key_names"] = keys
    instance_type = (cfg.org_lambda_instance_type or "").strip()
    if instance_type:
        kw["instance_type"] = instance_type
    return kw


def provision_org(eng, org_id: int, *, client=None, wiki_client=None, **provision_kw) -> str:
    """Provision the org backend and persist the result. Returns the final status.

    ``client`` (and any ``provision_kw`` like ``validate``/``sleep``) are injected in tests; in
    production the client is built from the stored provider key for the live providers (RunPod today)
    and the provisioner's real validation runs. A failed provision records the reason and never
    half-commits a 'provisioned' state.
    """
    db = sessionmaker(bind=eng)()
    cfg = None
    # In production the model client is built here from the stored key; tests inject one. Mirror that for
    # the wiki host: only auto-build the live wiki client when the model client was not injected.
    _model_injected = client is not None
    try:
        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not cfg or not cfg.org_provider or not cfg.org_model:
            return _fail(db, cfg, "Pick a provider and a model first.")
        provider = cfg.org_provider
        try:
            provisioner = prov.get_provisioner(provider)
        except prov.ProvisionError as e:
            return _fail(db, cfg, str(e))

        if client is None and provider in _LIVE_PROVIDERS:
            try:
                client = _live_client(cfg)
            except prov.ProvisionError as e:
                return _fail(db, cfg, str(e))

        spec = _spec_from_cfg(cfg)
        # Provider launch config saved by the admin (e.g. Lambda SSH key + instance type); explicit
        # provision_kw (tests) wins over the derived values. Default a real sleep so the provider's
        # readiness poll is actually paced in production - without it the loop spins network-bound and
        # a slow-to-register endpoint (or a still-booting VM) is misjudged. Tests inject a no-op sleep.
        launch_kw = {**_provider_provision_kw(cfg), **provision_kw}
        launch_kw.setdefault("sleep", time.sleep)
        try:
            if client is not None and provider in _LIVE_PROVIDERS:
                result = provisioner.provision(spec, client=client, **launch_kw)
            else:
                result = provisioner.provision(spec)  # stubs raise ProvisionerNotReady
        except prov.ProvisionError as e:
            return _fail(db, cfg, str(e))

        if not result.ok:
            return _fail(db, cfg, result.detail or "provisioning failed")
        cfg.org_backend_status = "provisioned"
        cfg.org_backend_detail = (result.detail or "")[:255]
        cfg.org_backend_handle = result.handle or ""
        if result.endpoint:
            cfg.org_model_endpoint = result.endpoint.base_url
            # The provisioner generates a fresh random key per run (see e.g. lambda_provision.py's
            # vLLM startup script) and the served endpoint REQUIRES it - without persisting it here,
            # nothing (chat routing, the reachability probe) could ever authenticate to the endpoint
            # this same call just stood up.
            if result.endpoint.api_key:
                from .crypto import encrypt

                cfg.org_model_key_enc = encrypt(result.endpoint.api_key)
            # A bare-VM provisioner's SSH tunnel (docs/specs/llm-endpoint-secure-transport.md) - the
            # port and host are needed to re-supervise the tunnel after an Anthill restart, since the
            # loopback base_url above no longer carries the VM's real address.
            if result.endpoint.tunnel_private_key_openssh:
                from .crypto import encrypt

                cfg.org_lambda_tunnel_key_enc = encrypt(result.endpoint.tunnel_private_key_openssh)
                cfg.org_lambda_tunnel_port = result.endpoint.tunnel_local_port
                cfg.org_lambda_tunnel_host = result.endpoint.tunnel_remote_host
        # Optionally stand up the always-on wiki/backend host in the same cloud (best-effort).
        _provision_wiki_host_if_requested(
            db,
            cfg,
            spec,
            wiki_client=wiki_client,
            build=not _model_injected,
            sleep=launch_kw.get("sleep"),
        )
        _mirror_lead_into_council(db, cfg)
        db.commit()
        return "provisioned"
    except Exception as e:
        # Last-resort guard: a background thread must never leave the org stuck on "provisioning".
        # Any unexpected error (a network fault escaping the provider, etc.) is recorded as a terminal
        # error so the UI shows it and the admin can retry.
        return _fail(db, cfg, f"provisioning error: {e}")
    finally:
        db.close()


def teardown_org(eng, org_id: int, *, client=None, wiki_client=None) -> str:
    """Tear down the provisioned backend (model endpoint + the always-on wiki pod) and reset the org to
    'planned'. Returns the status."""
    db = sessionmaker(bind=eng)()
    try:
        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not cfg:
            return "error"
        handle = cfg.org_backend_handle or ""
        provider = cfg.org_provider
        try:
            provisioner = prov.get_provisioner(provider)
            if client is None and provider in _LIVE_PROVIDERS:
                client = _live_client(cfg)
            if client is not None and provider in _LIVE_PROVIDERS:
                provisioner.teardown(handle, client=client)
            else:
                provisioner.teardown(handle)
        except prov.ProvisionError:
            pass  # best-effort: still reset our state so the admin can re-provision
        # Tear down the always-on wiki/backend pod if one was provisioned.
        wiki_handle = cfg.org_wiki_pod_handle or ""
        if wiki_handle:
            from ..hosting.wiki_host import teardown_wiki_host

            wc = wiki_client if wiki_client is not None else _wiki_client(cfg)
            if wc is not None:
                try:
                    teardown_wiki_host(wiki_handle, client=wc)
                except Exception:
                    pass  # best-effort
        cfg.org_backend_handle = ""
        cfg.org_wiki_pod_handle = ""
        cfg.org_model_endpoint = ""
        # provisioner.teardown() above already stopped this handle's SSH tunnel (LambdaLiveProvisioner
        # keys it by handle internally); clear the persisted tunnel material so a stale key never
        # lingers past the VM it belonged to.
        cfg.org_lambda_tunnel_key_enc = ""
        cfg.org_lambda_tunnel_port = 0
        cfg.org_lambda_tunnel_host = ""
        cfg.org_backend_status = (
            "planned" if (cfg.org_provider and cfg.org_model) else "unconfigured"
        )
        cfg.org_backend_detail = "Torn down."
        _mirror_lead_into_council(db, cfg)
        db.commit()
        return cfg.org_backend_status
    finally:
        db.close()


def _fail(db, cfg, detail: str) -> str:
    if cfg is not None:
        cfg.org_backend_status = "error"
        cfg.org_backend_detail = (detail or "provisioning failed")[:255]
        _mirror_lead_into_council(db, cfg)
        db.commit()
    return "error"


# ── council reviewer members (Phase 1b): member_index >= 1 in org_council_members ──────────────────
#
# The lead (member_index 0) keeps using provision_org/teardown_org above UNCHANGED - it is the
# legacy singleton columns, already live and tested. A reviewer member (index >= 1) has no singleton
# columns; _MemberRef presents one org_council_members[i] dict through the same cfg.org_* attribute
# surface _spec_from_cfg/_provider_key/_live_client/_provider_provision_kw already read, so none of
# that shared logic needs to change to serve either storage.


class _MemberRef:
    """Read-only view of one org_council_members[i] dict through the cfg.org_* attribute surface the
    provisioning helpers above expect. The provision-key and HF token are always account-level -
    shared across every member, per product-council-architecture.md R3's explicit shared-credential
    allowance - so those two read straight off the real cfg regardless of which member this is."""

    def __init__(self, cfg, member: dict):
        self._cfg = cfg
        self._m = member

    @property
    def org_provider(self) -> str:
        return self._m.get("provider") or ""

    @property
    def org_model(self) -> str:
        return self._m.get("model") or ""

    @property
    def org_model_params(self) -> str:
        return self._m.get("params_b") or ""

    @property
    def org_region(self) -> str:
        return self._m.get("region") or ""

    @property
    def org_gpu(self) -> str:
        return self._m.get("gpu_tier") or ""

    @property
    def org_model_quantized(self) -> bool:
        return bool(self._m.get("quantized"))

    @property
    def org_lambda_ssh_keys(self) -> str:
        return (self._m.get("provider_config") or {}).get("ssh_keys", "")

    @property
    def org_lambda_instance_type(self) -> str:
        return (self._m.get("provider_config") or {}).get("instance_type", "")

    @property
    def org_lambda_tunnel_key_enc(self) -> str:
        return (self._m.get("provider_config") or {}).get("tunnel_key_enc", "")

    @property
    def org_lambda_tunnel_port(self) -> int:
        return int((self._m.get("provider_config") or {}).get("tunnel_local_port") or 0)

    @property
    def org_lambda_tunnel_host(self) -> str:
        return (self._m.get("provider_config") or {}).get("tunnel_remote_host", "")

    @property
    def org_serve_wiki(self) -> bool:
        return False  # wiki hosting is account-level (Phase 1); only the lead's run can trigger it

    @property
    def org_hf_token_enc(self):
        return self._cfg.org_hf_token_enc  # account-level, shared across every member

    @property
    def org_provision_key_enc(self):
        return self._cfg.org_provision_key_enc  # account-level, shared across every member


def _members_or_empty(cfg) -> list[dict]:
    try:
        raw = json.loads(cfg.org_council_members or "[]")
    except (TypeError, ValueError):
        raw = []
    return raw if isinstance(raw, list) else []


def _write_member(db, cfg, member_index: int, member: dict) -> None:
    """Write ``member`` into org_council_members[member_index] and stage the commit (caller still
    calls db.commit()). Refreshes org_council_members from the DB immediately before merging, not
    from cfg's own (possibly long-stale) in-memory value - the lead's provision_org/teardown_org and
    another reviewer's provision_council_member/teardown_council_member each run in their own DB
    session and commit independently while THIS call's own live provisioning/teardown was in flight;
    without the refresh, this write would silently clobber whatever they already committed to other
    slots with the stale full-list snapshot this call loaded when it started. If the slot no longer
    exists (a settings save removed it while this call was in flight), there is nowhere left to write
    the result - give up rather than growing or misindexing the list."""
    db.refresh(cfg, attribute_names=["org_council_members"])
    members = _members_or_empty(cfg)
    if member_index < len(members):
        members[member_index] = member
        cfg.org_council_members = json.dumps(members)


def provision_council_member(
    eng,
    org_id: int,
    member_index: int,
    *,
    client=None,
    expected_provider: str | None = None,
    expected_model: str | None = None,
    **provision_kw,
) -> str:
    """Provision one reviewer member (member_index >= 1) and persist the result into
    org_council_members[member_index]. Mirrors provision_org's orchestration (same live-client and
    provider-launch-kwarg derivation via _MemberRef) but never touches the legacy singleton columns
    and never runs the wiki-host step (that stays the lead-only, account-level path).

    ``expected_provider``/``expected_model`` are an optional snapshot the caller took at request time;
    if given, this refuses to act unless the member still at ``member_index`` matches - a settings save
    that reorders or removes members between the request and this (background-threaded) call can shift
    what a stale index actually points at, and provisioning into the wrong slot would be worse than
    refusing.
    """
    db = sessionmaker(bind=eng)()
    try:
        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not cfg:
            return "error"
        members = _members_or_empty(cfg)
        if member_index < 1 or member_index >= len(members):
            return "error"
        member = members[member_index]
        if expected_provider is not None and member.get("provider") != expected_provider:
            return "error"
        if expected_model is not None and member.get("model") != expected_model:
            return "error"
        ref = _MemberRef(cfg, member)
        if not ref.org_provider or not ref.org_model:
            member["backend_status"] = "error"
            member["backend_detail"] = "Pick a provider and a model first."
            _write_member(db, cfg, member_index, member)
            db.commit()
            return "error"

        try:
            provisioner = prov.get_provisioner(ref.org_provider)
        except prov.ProvisionError as e:
            member["backend_status"] = "error"
            member["backend_detail"] = str(e)[:255]
            _write_member(db, cfg, member_index, member)
            db.commit()
            return "error"

        if client is None and ref.org_provider in _LIVE_PROVIDERS:
            try:
                client = _live_client(ref)
            except prov.ProvisionError as e:
                member["backend_status"] = "error"
                member["backend_detail"] = str(e)[:255]
                _write_member(db, cfg, member_index, member)
                db.commit()
                return "error"

        spec = _spec_from_cfg(ref)
        launch_kw = {**_provider_provision_kw(ref), **provision_kw}
        launch_kw.setdefault("sleep", time.sleep)
        try:
            if client is not None and ref.org_provider in _LIVE_PROVIDERS:
                result = provisioner.provision(spec, client=client, **launch_kw)
            else:
                result = provisioner.provision(spec)  # stubs raise ProvisionerNotReady
        except prov.ProvisionError as e:
            member["backend_status"] = "error"
            member["backend_detail"] = str(e)[:255]
            _write_member(db, cfg, member_index, member)
            db.commit()
            return "error"

        if not result.ok:
            member["backend_status"] = "error"
            member["backend_detail"] = (result.detail or "provisioning failed")[:255]
            _write_member(db, cfg, member_index, member)
            db.commit()
            return "error"

        member["backend_status"] = "provisioned"
        member["backend_detail"] = (result.detail or "")[:255]
        member["backend_handle"] = result.handle or ""
        if result.endpoint:
            member["endpoint"] = result.endpoint.base_url
            # The provisioner generates a fresh random key per run and the served endpoint REQUIRES
            # it - persist it (encrypted) so this member's endpoint is actually callable later,
            # mirroring the lead path's provision_org.
            if result.endpoint.api_key:
                from .crypto import encrypt

                member["model_key_enc"] = encrypt(result.endpoint.api_key)
            # A bare-VM provisioner's SSH tunnel: each council member can be its own separate Lambda
            # VM, so its tunnel key/port/host are stored in this member's own provider_config, not the
            # account-level columns (which are the lead's - see _mirror_lead_into_council).
            if result.endpoint.tunnel_private_key_openssh:
                from .crypto import encrypt

                provider_config = dict(member.get("provider_config") or {})
                provider_config["tunnel_key_enc"] = encrypt(
                    result.endpoint.tunnel_private_key_openssh
                )
                provider_config["tunnel_local_port"] = result.endpoint.tunnel_local_port
                provider_config["tunnel_remote_host"] = result.endpoint.tunnel_remote_host
                member["provider_config"] = provider_config
        _write_member(db, cfg, member_index, member)
        db.commit()
        return "provisioned"
    except Exception as e:
        # Last-resort guard: a background thread must never leave a member stuck on "provisioning".
        # Refresh first (not the enclosing `members`, which is stale after the network call above) so
        # this doesn't clobber a lead or another reviewer that committed its own state in the meantime.
        try:
            if cfg is not None:
                db.refresh(cfg, attribute_names=["org_council_members"])
                fresh = _members_or_empty(cfg)
                if 0 <= member_index < len(fresh):
                    fresh[member_index]["backend_status"] = "error"
                    fresh[member_index]["backend_detail"] = f"provisioning error: {e}"[:255]
                    cfg.org_council_members = json.dumps(fresh)
                    db.commit()
        except Exception:
            pass
        return "error"
    finally:
        db.close()


def teardown_council_member(
    eng, org_id: int, member_index: int, *, client=None, expected_handle: str | None = None
) -> str:
    """Tear down one reviewer member (member_index >= 1) and reset its slot to 'planned'.

    Only clears the handle once ``provisioner.teardown()`` was actually invoked - that call's own
    contract (every live client's ``terminate()``) is idempotent and never raises, so reaching it is
    "as good as it gets". Resolving the provisioner or building its account client can fail BEFORE any
    teardown is attempted (an unknown provider, a missing/invalid key); in that case nothing was ever
    torn down, so the handle is kept and the slot is marked error rather than silently reset - clearing
    it here would strand the resource with no record left to retry the teardown against.

    ``expected_handle`` is an optional snapshot the caller took at request time; if given, this refuses
    to act unless the member still at ``member_index`` still has that exact handle - a settings save
    that reorders or removes members between the request and this (background-threaded) call can shift
    what a stale index actually points at, and tearing down a different member's live resource by
    mistake would be worse than refusing.
    """
    db = sessionmaker(bind=eng)()
    try:
        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not cfg:
            return "error"
        members = _members_or_empty(cfg)
        if member_index < 1 or member_index >= len(members):
            return "error"
        member = members[member_index]
        handle = member.get("backend_handle") or ""
        if expected_handle is not None and handle != expected_handle:
            return "error"
        ref = _MemberRef(cfg, member)
        try:
            provisioner = prov.get_provisioner(ref.org_provider)
            if client is None and ref.org_provider in _LIVE_PROVIDERS:
                client = _live_client(ref)
        except prov.ProvisionError as e:
            # Never attempted teardown at all - keep the handle so a retry (or manual cleanup) is
            # still possible, rather than losing the only reference to a live, billable resource.
            member["backend_status"] = "error"
            member["backend_detail"] = f"teardown not attempted: {e}"[:255]
            _write_member(db, cfg, member_index, member)
            db.commit()
            return "error"
        try:
            if client is not None and ref.org_provider in _LIVE_PROVIDERS:
                provisioner.teardown(handle, client=client)
            else:
                provisioner.teardown(handle)
        except prov.ProvisionError:
            pass  # the provisioner's own teardown() is idempotent by contract; best-effort past this point
        member["backend_handle"] = ""
        member["endpoint"] = ""
        # provisioner.teardown() above already stopped this handle's SSH tunnel; drop the persisted
        # tunnel material from this member's provider_config too, alongside the other Lambda launch
        # config it may still carry (ssh_keys/instance_type stay - those are the admin's own settings).
        provider_config = dict(member.get("provider_config") or {})
        provider_config.pop("tunnel_key_enc", None)
        provider_config.pop("tunnel_local_port", None)
        provider_config.pop("tunnel_remote_host", None)
        member["provider_config"] = provider_config
        member["backend_status"] = (
            "planned" if (member.get("provider") and member.get("model")) else "unconfigured"
        )
        member["backend_detail"] = "Torn down."
        _write_member(db, cfg, member_index, member)
        db.commit()
        return member["backend_status"]
    finally:
        db.close()
