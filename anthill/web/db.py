from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, relationship

log = logging.getLogger("anthill.db")

DB_PATH = Path(os.environ.get("ANTHILL_DB", "data/anthill.db"))


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    """Two SQLite defaults are wrong for us; both are per-connection, so set on every connect.

    - ``journal_mode=WAL``: the default rollback journal makes a writer block readers, and this app runs
      background threads (scheduler, provisioning, agent runs) against one file with
      ``check_same_thread=False``. WAL lets readers and the writer proceed concurrently. ``journal_mode``
      is persistent in the file, so re-setting it is idempotent.
    - ``foreign_keys=ON``: SQLite leaves foreign-key enforcement OFF by default, so the ~70 declared
      foreign keys were ORM wiring only - nothing at the database level stopped an orphan row, and
      referential integrity rested entirely on application code. Now enforced (#634). This is not
      retroactive: rows orphaned while enforcement was off stay readable (``_log_fk_violations`` reports
      them); only new writes are checked. Delete paths that would leave a dangling child clear or
      reparent it first (see the team/agent/conversation deletes in ``web/app.py``).

    Registered on ``Engine`` (not just our ``get_engine``) so the invariant holds for every connection,
    including engines the tests build directly. Guarded to sqlite: the only backend we use, and a PRAGMA
    would be invalid on anything else.
    """
    if "sqlite3" not in type(dbapi_conn).__module__:
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")  # persistent; a no-op on an in-memory db
        cur.execute("PRAGMA foreign_keys=ON")  # per-connection; enforces referential integrity
    finally:
        cur.close()


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(60), unique=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    users = relationship("User", back_populates="org", cascade="all, delete-orphan")
    settings = relationship(
        "OrgSettings", uselist=False, back_populates="org", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    email = Column(String(254), unique=True, nullable=False)
    display_name = Column(String(120), nullable=False, default="")
    hashed_password = Column(String(255), nullable=True)  # null for OAuth-only users
    role = Column(String(20), nullable=False, default="member")  # admin | member
    active = Column(Boolean, nullable=False, default=False)  # True after email confirmation
    oauth_provider = Column(String(30), nullable=True)  # "google" | "microsoft" | null
    oauth_subject = Column(String(255), nullable=True)
    invite_token = Column(String(64), nullable=True)  # used for double opt-in flow
    reset_token = Column(
        String(64), nullable=True
    )  # password-reset link (single-use, time-limited)
    reset_expires = Column(Integer, nullable=True)  # reset token expiry, epoch seconds UTC
    # Forced password rotation after a credential exposure. A password that was stored/shown in cleartext
    # (e.g. misfilled into the display-name field, #591) is COMPROMISED - scrubbing the copy is not enough,
    # the account must set a new one. True => the next login requires a reset before a session is issued;
    # cleared when the password is changed. Defaults False, so normal accounts are never affected.
    must_reset_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, nullable=True)
    profile = Column(Text, nullable=False, default="")  # "make it yours" persona/preferences
    onboarding_done = Column(Boolean, nullable=False, default=False)  # post-signup tour seen
    # CSV of per-surface walkthrough keys (wiki|memory|snippets|skills) the user has completed or
    # skipped, independent of `onboarding_done` and of each other - #683.
    completed_walkthroughs = Column(String(200), nullable=False, default="")
    # P4: opt-in to run your Solo (personal) chats on the more capable org model instead of the local
    # one. Ephemeral by contract - the org model serves the answer but nothing is retained, trained
    # on, or shared; personal context stays personal. Off by default. Only effective when the org
    # backend is connected.
    personal_mode_on_org_model = Column(Boolean, nullable=False, default=False)
    # Pause auto-memory: when True, Anthill stops auto-distilling durable memories from this user's
    # chats/tasks/agent runs (existing memories are kept; recall still works). Off by default.
    auto_memory_off = Column(Boolean, nullable=False, default=False)
    # Web access (Settings -> Privacy): the user's default for whether a chat may look things up online.
    # Off by default (privacy-first, matching the Solo web-search default, docs/specs/solo-web-search-
    # default-off.md); seeds the per-chat Web-search toggle's initial state. The per-turn `web` flag +
    # decide_web() still gate each actual search.
    web_access_on = Column(Boolean, nullable=False, default=False)
    # Authentication generation rotated by password changes, resets, and account deactivation.
    # Session JWTs carry this value, so prior access stops authenticating immediately.
    auth_version = Column(Integer, nullable=False, default=0)
    org = relationship("Organization", back_populates="users")


class AuthSession(Base):
    """One revocable signed-in device session."""

    __tablename__ = "auth_sessions"
    id = Column(String(80), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at = Column(Integer, nullable=False)
    revoked_at = Column(Integer, nullable=False, default=0)


class BrowserSessionOrder(Base):
    __tablename__ = "browser_session_orders"
    id = Column(String(80), primary_key=True)
    high_water = Column(Integer, nullable=False, default=0)
    observed_order = Column(Integer, nullable=False, default=0)


class Team(Base):
    """A team ("project"): the middle knowledge tier between personal and org.

    The creating user becomes the owner. In an org, existing org members can be invited; in Solo
    (no org), a team is just a personal project (no invites, ``org_id`` NULL). Each team runs its
    own wiki/KB, isolated from other teams; the owner approves anything promoted out (team -> org).
    """

    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    org_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )  # NULL in Solo (a local project); set in an org
    name = Column(String(120), nullable=False)
    slug = Column(String(60), nullable=False)  # unique within an org (enforced in code)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Connect the project to its PARENT wiki at setup (#419): when True (default), a run in this project
    # also reads its parent wiki read-only - the org wiki for an org project, the user's personal wiki for
    # a Solo/local project. When False the project is fully isolated (its own wiki only). Read grounding
    # only; the write target is always the project's own wiki.
    connect_parent_wiki = Column(Boolean, nullable=False, default=True)


class TeamMembership(Base):
    """A user's place in a team. The owner row is created active on team creation;
    invited members stay 'invited' until they accept (double opt-in, like user invites)."""

    __tablename__ = "team_memberships"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(10), nullable=False, default="member")  # owner | member
    status = Column(String(10), nullable=False, default="invited")  # invited | active
    invite_token = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class OrgSettings(Base):
    __tablename__ = "org_settings"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), unique=True, nullable=False)
    ollama_model = Column(String(80), nullable=False, default="qwen2.5:3b")
    ollama_url = Column(String(255), nullable=False, default="http://localhost:11434")
    # First-run account-type branch: has the fresh account chosen Solo vs organization yet? Until True,
    # the dashboard sends it to /setup/account-type first, before the model picker below - mirrors
    # local_model_chosen's own gate-until-chosen pattern. Defaults to True (already chosen) - the SAFE
    # default for anything that constructs OrgSettings directly without going through the real /setup
    # POST (dozens of test fixtures do this): only setup_post explicitly sets this False on a genuinely
    # fresh sign-up, so only that path is ever routed through the new screen.
    account_type_chosen = Column(Boolean, nullable=False, default=True)
    # First-run model picker (solo): has the user chosen their local model yet? Until True, a solo
    # install is sent to /setup/model to pick a family + size; nothing is pulled before they choose.
    local_model_chosen = Column(Boolean, nullable=False, default=False)
    # A local model tag currently downloading (set on select, cleared on completion). While set, the
    # picked model is NOT yet the active `ollama_model` - the previous model keeps serving until the
    # download finishes; the /models page polls this to show live progress + a "ready" confirmation.
    local_model_pulling = Column(String(120), nullable=False, default="")
    # The latest local-model benchmark run, as a JSON blob (running/candidate/summary/winner/error);
    # "" = none. Transient (overwritten each run). Stored here rather than in process memory so the
    # /models poller sees it regardless of which worker serves the request, mirroring the pull state
    # above and updated the same way (a fresh session from the background benchmark thread).
    benchmark_state = Column(Text, nullable=False, default="")
    # Solo compute (Solo/Project/Org model): where a Solo (personal) run executes. "local" = the
    # on-device model (default; nothing leaves the device); "cloud" = the user's OWN configured cloud
    # endpoint - the same connect/provision backend the org uses (`org_*` fields) - so a single user can
    # run a big frontier-class open model. It is your own cloud, so the run is NOT ephemeral; personal
    # context + personal wiki are kept. Falls back to local when no endpoint is connected. Also set by
    # the setup wizard's compute step ("mac_mini" too, alongside "local"/"cloud") - both "cloud" and
    # "mac_mini" mean the run depends on a connected backend, unlike "local".
    solo_compute = Column(String(10), nullable=False, default="local")  # local | cloud | mac_mini
    # Auto-download a small vision model after first-run so image/screenshot analysis works out of the
    # box (default on; the user can opt out at setup). Best-effort background pull, never bundled.
    vision_autopull = Column(Boolean, nullable=False, default=True)
    # Opt-in "maximum accuracy" vision mode. Default off: the sovereign vision stack is fully-local,
    # licence-clean and non-Chinese (IBM Granite / Mistral). When on, the router prefers Qwen3-VL and
    # autopull fetches it instead of the Granite default. See docs/specs/local-vision-model-selection.md.
    vision_max_accuracy = Column(Boolean, nullable=False, default=False)
    cache_threshold = Column(String(10), nullable=False, default="0.93")
    wiki_hosting = Column(
        String(10), nullable=False, default="local"
    )  # local | vpc (own private instance)
    wiki_vpc_url = Column(
        String(255), nullable=False, default=""
    )  # org-backend URL on the VPC instance
    agent_interval_secs = Column(Integer, nullable=False, default=300)
    # ── Agent (third surface) defaults + run caps; applied to new agents / each run ──
    agent_default_governance = Column(String(12), nullable=False, default="standard")
    agent_default_model = Column(String(80), nullable=False, default="")  # blank = auto-route
    agent_max_steps = Column(
        Integer, nullable=False, default=12
    )  # tool-loop cap per run (cost rail)
    task_max_steps = Column(
        Integer, nullable=False, default=10
    )  # tool-loop cap per scheduled-task run (cost rail)
    # Council review (Phase 4b): when 2+ council reviewers are configured, a task/agent run's finished
    # answer is critiqued by the other members and possibly revised - extra API calls + latency + cost,
    # and the answer content reaching additional backends, on every run. On by default (matches the
    # spec's intent - configuring reviewers means they review everything), but admins can turn it off
    # for the task/agent surfaces specifically without disabling council review for chat.
    council_review_tasks = Column(Boolean, nullable=False, default=True)
    # #278: unattended Tasks are the only escalation surface with real runaway risk (Chat/Agents are
    # rate-limited by a human being present) - a monthly CALL-COUNT cap, not a dollar cap, since there
    # is no reliable per-call cost figure for an arbitrary connected endpoint (onprem has none at all;
    # a manually-connected inference provider isn't in the priced vendor registry hybrid/providers.py
    # owns). Reset lazily on use, mirroring cloud_spent_usd's running-total pattern below.
    task_escalation_cap_per_month = Column(Integer, nullable=False, default=20)
    task_escalations_this_month = Column(Integer, nullable=False, default=0)
    task_escalations_reset_at = Column(DateTime, nullable=True)
    # Skill auto-distillation: after a successful agent run, propose a reusable skill for human
    # accept/reject (never written live). Propose-only, so on by default; admins can pause it.
    skill_autolearn = Column(Boolean, nullable=False, default=True)
    cost_per_query_usd = Column(String(10), nullable=False, default="0.004")  # cloud API comparison
    energy_per_query_gco2 = Column(String(10), nullable=False, default="4.0")  # cloud estimate gCO₂
    # ── deployment topology (chosen at setup) ──────────────────────────────────
    # local - small capable team, everything on-device, no GPU backend
    # gpu   - larger/weak team; coordination + training on an org GPU backend
    # intent/scale of this deployment (backend readiness is tracked by aws_status):
    #   org  - an organization; runs an org GPU backend (VPC/on-prem). The standard.
    #   solo - a single individual, one node, no backend (evaluation / personal use).
    deployment_topology = Column(String(10), nullable=False, default="org")  # org | solo
    gpu_cloud = Column(String(20), nullable=False, default="aws")  # aws (VPC default)
    # ── AWS VPC GPU backend (config captured here; provisioned by anthill.cloud.aws) ─
    aws_region = Column(String(30), nullable=False, default="")
    aws_access_key_id = Column(String(40), nullable=False, default="")
    aws_secret_access_key_enc = Column(Text, nullable=False, default="")  # AES-256-GCM at rest
    aws_instance_type = Column(String(30), nullable=False, default="g5.xlarge")
    aws_ami_id = Column(
        String(40), nullable=False, default=""
    )  # Deep Learning AMI (blank = aws.default_ami for the region)
    aws_subnet_id = Column(String(40), nullable=False, default="")
    aws_security_group_id = Column(String(40), nullable=False, default="")
    aws_max_runtime_min = Column(Integer, nullable=False, default=120)  # cost guardrail: hard cap
    aws_max_cost_usd = Column(String(12), nullable=False, default="25.00")  # per-run budget cap
    aws_status = Column(
        String(20), nullable=False, default="unconfigured"
    )  # unconfigured|validated|error
    aws_status_detail = Column(String(255), nullable=False, default="")
    # ── org serving model (two-plane P1: Anthill provisions the org's own shared model) ──
    # Direction A: Anthill provisions the org's own inference backend. These capture the admin's
    # choice; the provisioning plan is rendered by anthill.hosting.provision (provider -> tier).
    org_provider = Column(
        String(20), nullable=False, default=""
    )  # onprem | aws | gcp | azure | ibm | modal | runpod
    org_model = Column(String(120), nullable=False, default="")  # the org inference model to serve
    org_model_params = Column(String(10), nullable=False, default="")  # size in billions (string)
    org_region = Column(String(40), nullable=False, default="")  # cloud region ("" for on-prem)
    org_gpu = Column(
        String(20), nullable=False, default=""
    )  # selected cloud GPU tier key (sizing.GPU_TIERS); gates which models fit + sets the RunPod pool
    # RunPod only (PR #661 Tier 3): workers kept warm to bound cold starts, at ongoing cost. 0 (default)
    # scales fully to zero when idle, same as before this existed. Not applicable to Lambda/on-prem
    # (always-on VMs, not scale-to-zero serverless).
    org_warm_workers = Column(Integer, nullable=False, default=0)
    # Serve a 4-bit (AWQ) build of the chosen model, so a big model (32B/70B) fits a smaller cloud GPU.
    # Provisioning resolves the catalog's quant_hf_id; vLLM auto-detects the quantization.
    org_model_quantized = Column(Boolean, nullable=False, default=False)
    org_serve_wiki = Column(Boolean, nullable=False, default=True)  # also bring up the wiki host
    org_backend_status = Column(
        String(20), nullable=False, default="unconfigured"
    )  # unconfigured | planned | validated | provisioning | provisioned | error
    org_backend_detail = Column(String(255), nullable=False, default="")
    # Manual "connect an endpoint I run myself" escape hatch (until live provisioning lands):
    org_model_endpoint = Column(String(255), nullable=False, default="")  # base URL incl. /v1
    org_model_key_enc = Column(Text, nullable=False, default="")  # API key, AES-256-GCM at rest
    # Live provisioning (Direction A): the org's provider-account API key + the provisioned handle.
    org_provision_key_enc = Column(Text, nullable=False, default="")  # provider API key, AES-GCM
    # Hugging Face token (AES-GCM), passed to the cloud GPU worker so it can pull HF-gated models (Llama).
    org_hf_token_enc = Column(Text, nullable=False, default="")
    org_backend_handle = Column(
        String(120), nullable=False, default=""
    )  # provisioned id, for teardown
    org_wiki_pod_handle = Column(
        String(120), nullable=False, default=""
    )  # the always-on wiki/backend CPU pod id (provisioned alongside the model), for teardown
    # Lambda Labs (Cloud VPC) launch config: it serves on an always-on GPU VM, which requires a
    # registered SSH key name and an instance type (region uses org_region above). Lambda only.
    org_lambda_ssh_keys = Column(
        String(255), nullable=False, default=""
    )  # comma-separated registered Lambda SSH key names (Lambda requires at least one)
    org_lambda_instance_type = Column(
        String(60), nullable=False, default=""
    )  # e.g. gpu_1x_a10; blank uses the provisioner default
    # SSH tunnel (docs/specs/llm-endpoint-secure-transport.md, Option A): Anthill's own keypair for
    # reaching a bare-VM provisioner's loopback-bound vLLM. The lead's tunnel; a reviewer member's own
    # tunnel key/port/host live in its own org_council_members[i]["provider_config"] entry instead (see
    # provision_run.py's _MemberRef) - each council member can be its own separate Lambda VM.
    org_lambda_tunnel_key_enc = Column(
        Text, nullable=False, default=""
    )  # SSH private key (OpenSSH format), AES-256-GCM at rest
    org_lambda_tunnel_port = Column(
        Integer, nullable=False, default=0
    )  # stable local forward port; 0 = no tunnel provisioned
    org_lambda_tunnel_host = Column(
        String(64), nullable=False, default=""
    )  # the VM's IP/host, needed to re-establish the tunnel after an Anthill restart
    # ── council (Phase 1): ordered list of the account's model members ────────
    # JSON array of member dicts, in council order (list index = council position). Supersedes the
    # single org_* model block above for new saves; those columns stay for backfill/rollback safety
    # and are no longer written. See app._empty_member() for the per-member shape.
    org_council_members = Column(Text, nullable=False, default="[]")
    # ── Tier 5 (#661): distributed local pooling, own-LAN machines only ────────
    # Purely descriptive: Anthill never SSHes into these machines or launches rpc-server/llama-server
    # itself - the admin sets those up. The pool's MAIN node's actual chat endpoint is still
    # org_model_endpoint/org_model_key_enc (llama-server started with --rpc exposes the identical
    # OpenAI-compatible /v1 API, so the chat path needs no changes). This config only drives a
    # capacity estimate (sizing.cluster_max_params_b) and a raw TCP reachability display for the
    # workers - node memory here is self-reported, never measured (no agent runs on a remote box).
    org_cluster_enabled = Column(Boolean, nullable=False, default=False)
    org_cluster_kind = Column(
        String(10), nullable=False, default="apple"
    )  # apple | gpu - whole pool
    org_cluster_main_mem_gb = Column(
        String(10), nullable=False, default=""
    )  # main node, self-reported
    # JSON array of {"label","host","port","mem_gb"} - workers only (not the main node). Mirrors
    # org_council_members's JSON-array-of-dicts convention. Capped at 3 entries (main + 3 = 4 nodes).
    org_cluster_workers = Column(Text, nullable=False, default="[]")
    # ── Expert tier: escalation-provider attachment (compound-compute-tiers spec) ──────────────
    # Orthogonal to the base compute tier (local/cloud - see _apply_solo_compute): either tier can
    # have ONE inference provider (_INFERENCE_PROVIDERS' berget/groq/infercom keys) attached as its
    # escalation path. Deliberately a NEW field, not cloud_provider below - that column belongs to
    # the older, unrelated hybrid-cloud-fallback feature (cloud_enabled/cloud_threshold), and
    # reusing it here would overload one field with two independent on/off features with different
    # triggers. "" means no escalation provider attached (the #278 base engine still works without
    # one, using whatever plane is already connected).
    escalation_provider = Column(
        String(20), nullable=False, default=""
    )  # "" | berget | groq | infercom
    escalation_provider_key_enc = Column(Text, nullable=False, default="")  # AES-256-GCM at rest
    # The specific model to run on the attached provider. "" = use the provider's curated default
    # (_INFERENCE_PROVIDERS[provider]["escalation_model"]). Set from the attach UI's model picker,
    # validated against the provider's live /v1/models. Must be a non-reasoning instruct model
    # (docs/specs/escalation-models-non-reasoning.md) - the picker guard enforces that.
    escalation_model = Column(String(120), nullable=False, default="")
    # ask - #278's existing "type a redo phrase" follow-up flow, now with the free logprob fold-in
    # (intent.seems_uncertain_for_ask) added to its suggestion signal; automated - fires without a
    # follow-up and notifies after the fact, never blocking (escalation.should_escalate_automated).
    # Chosen once, at attachment time - not a per-turn choice.
    escalation_mode = Column(String(10), nullable=False, default="ask")  # ask | automated
    # Automated's runaway-risk cap, same call-count (not dollar) shape and reset-lazily-on-use
    # pattern as task_escalation_cap_per_month/task_escalations_this_month/task_escalations_reset_at
    # above - there's no reliable per-call cost figure for an arbitrary manually-connected provider.
    # Ask mode has no cap: a human types the redo phrase each time, which is the rate limit.
    escalation_cap_per_month = Column(Integer, nullable=False, default=20)
    escalations_this_month = Column(Integer, nullable=False, default=0)
    escalations_reset_at = Column(DateTime, nullable=True)
    # First-use consent gate: Automated mode otherwise fires with zero per-turn confirmation once
    # attached, which is correct for "notifies after the fact" - but the FIRST time a question would
    # actually leave the device to a third party, a human click is required regardless of mode, since
    # picking "Automated" at attachment time is easy to click through without registering what it
    # means. False until the user explicitly chooses "Always" on that offer; choosing "Just this
    # request" leaves this False so the next eligible turn offers again.
    escalation_consented = Column(Boolean, nullable=False, default=False)
    # ── hybrid cloud fallback (open-source-first; off by default) ──────────────
    cloud_enabled = Column(Boolean, nullable=False, default=False)
    cloud_provider = Column(String(30), nullable=False, default="openrouter")
    cloud_model = Column(String(80), nullable=False, default="")
    cloud_threshold = Column(String(10), nullable=False, default="0.5")
    cloud_send_context = Column(Boolean, nullable=False, default=False)
    cloud_scrub_pii = Column(Boolean, nullable=False, default=True)
    cloud_budget_usd = Column(String(12), nullable=False, default="0")
    cloud_spent_usd = Column(String(12), nullable=False, default="0")  # running total
    cloud_api_key_enc = Column(
        Text, nullable=False, default=""
    )  # AES-256-GCM; blank → provider env var
    cloud_status = Column(
        String(15), nullable=False, default="unconfigured"
    )  # unconfigured|validated|error
    cloud_status_detail = Column(String(255), nullable=False, default="")
    # ── local model training (Phase 4, §7.5; off by default) ──────────────────
    training_enabled = Column(Boolean, nullable=False, default=False)
    # where a fine-tune runs (anthill.training.backends): onprem | aws | ibm | gcp | azure |
    # endpoint  (vpc is a back-compat alias for aws)
    training_backend = Column(String(20), nullable=False, default="onprem")
    training_gpu_endpoint = Column(String(255), nullable=False, default="")  # ssh host or API URL
    training_provider = Column(String(20), nullable=False, default="")  # neocloud: modal | runpod
    training_token_id = Column(
        String(120), nullable=False, default=""
    )  # Modal token id (ak-...), not secret
    training_api_key_enc = Column(
        Text, nullable=False, default=""
    )  # neocloud token secret, AES-256-GCM
    training_base_model = Column(String(120), nullable=False, default="qwen3:8b")
    training_schedule_hrs = Column(Integer, nullable=False, default=24)
    training_gold_mark = Column(Integer, nullable=False, default=0)  # gold count at last run
    training_model_ver = Column(Integer, nullable=False, default=0)  # current org model version
    training_status = Column(String(20), nullable=False, default="idle")
    training_status_detail = Column(String(255), nullable=False, default="")  # last test/run detail
    training_last_run = Column(DateTime, nullable=True)
    # ── local/solo on-device fine-tune serving (Apple-Silicon MLX) ──
    # When a locally trained adapter wins the eval-gate it is persisted here and served via a local
    # mlx-lm OpenAI endpoint; the Solo/local plane routes to that endpoint instead of plain Ollama.
    local_finetune_path = Column(String(500), nullable=False, default="")  # promoted adapter dir
    local_serve_url = Column(String(200), nullable=False, default="")  # mlx-lm /v1 base url when up
    local_serve_model = Column(
        String(200), nullable=False, default=""
    )  # model id the endpoint serves
    # ── proactivity: ACTIONS are event-driven; TRAINING stays batched (above) ──
    proactivity_mode = Column(String(12), nullable=False, default="event")  # event | scheduled
    proactivity_last_run = Column(DateTime, nullable=True)
    # ── knowledge digest (#683 phase 7, spec requirement 5's digest half) ──
    digest_schedule = Column(String(10), nullable=False, default="off")  # off | daily | weekly
    digest_last_sent_at = Column(DateTime, nullable=True)
    # ── inbound push events (webhooks + IMAP) feed the workspace inbox/ ──
    webhook_enabled = Column(Boolean, nullable=False, default=False)
    webhook_secret_enc = Column(Text, nullable=False, default="")  # generic shared token, AES-GCM
    slack_signing_secret_enc = Column(
        Text, nullable=False, default=""
    )  # Slack request signing, AES-GCM
    imap_enabled = Column(Boolean, nullable=False, default=False)
    imap_host = Column(String(255), nullable=False, default="")
    imap_port = Column(Integer, nullable=False, default=993)
    imap_user = Column(String(255), nullable=False, default="")
    imap_password_enc = Column(Text, nullable=False, default="")  # AES-GCM at rest
    imap_folder = Column(String(120), nullable=False, default="INBOX")
    imap_status = Column(
        String(20), nullable=False, default="unconfigured"
    )  # unconfigured|connected|error
    imap_status_detail = Column(String(255), nullable=False, default="")
    # ── off-LAN remote access (reach the org backend from outside the LAN) ──
    remote_access_provider = Column(
        String(12), nullable=False, default="off"
    )  # off|cloudflare|manual
    remote_access_token_enc = Column(
        Text, nullable=False, default=""
    )  # named-tunnel token, AES-GCM
    remote_access_url = Column(String(255), nullable=False, default="")  # manual reverse-proxy URL
    # ── MCP server: expose the org brain to other tools. OFF by default; the admin
    #    approves which resources are exposed, and every access is logged. ──
    mcp_server_enabled = Column(Boolean, nullable=False, default=False)
    mcp_expose_wiki = Column(Boolean, nullable=False, default=False)
    mcp_expose_cache = Column(Boolean, nullable=False, default=False)
    mcp_expose_memory = Column(Boolean, nullable=False, default=False)
    mcp_access_token_enc = Column(
        Text, nullable=False, default=""
    )  # legacy shared token (deprecated)
    # per-consumer review: "review" = a consumer must be admin-approved before any query is answered
    # (default, sovereign); "log_only" = answer registered consumers immediately, audit every query.
    mcp_review_mode = Column(String(10), nullable=False, default="review")
    org = relationship("Organization", back_populates="settings")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event = Column(String(60), nullable=False)  # e.g. "user.login", "wiki.promote"
    detail = Column(Text, nullable=False, default="")
    ip = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Additive (#683 phase 7): links an audit row to the KnowledgeItem it acted on, when one was
    # cheaply in hand at the log site (e.g. right after an approve/create call already resolved the
    # registry row) - NULL on delete or wherever resolving it would cost an extra query for little
    # benefit. Deliberately a plain Integer, NOT a ForeignKey: like ProposedSkill.agent_id above, this
    # is a best-effort provenance pointer to a row that can legitimately be deleted later (e.g.
    # skills_delete()), and FK enforcement is ON (see the connect-time PRAGMA at the top of this file)
    # - a hard FK would either block that delete or need ON DELETE SET NULL semantics this column
    # doesn't need (a dangling id here just means "look it up if you still can").
    registry_id = Column(Integer, nullable=True)


class WikiReview(Base):
    """A proposed wiki write held for human review.

    Generalized across the knowledge ladder: target_scope picks the destination
    wiki (personal | team | org) and team_id names the team when relevant. A row
    is created only when the agent review (wiki/review.py) raises flags; clean
    writes auto-apply and never become a review. The approver is resolved from
    target_scope + team_id, not hardcoded to org admin.
    """

    __tablename__ = "wiki_reviews"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    proposed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Provenance: where this content came from, so promoted wiki knowledge is traceable to the exact input
    # + session + author it originated in. The ids link back; provenance_hash is the source turn's
    # ChatMessage.provenance fingerprint (content-addressable, so it can be verified independently).
    source_conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
    source_message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True)
    provenance_hash = Column(String(64), nullable=False, default="")
    slug = Column(String(120), nullable=False)
    kind = Column(String(12), nullable=False, default="page")  # page | principles | skill
    content = Column(Text, nullable=False)
    diff_summary = Column(Text, nullable=False, default="")
    target_scope = Column(String(10), nullable=False, default="org")  # personal | team | org
    team_id = Column(
        Integer, ForeignKey("teams.id"), nullable=True
    )  # set when target_scope == team
    outline = Column(Text, nullable=False, default="")  # agent review: post-apply state + delta
    flags = Column(Text, nullable=False, default="")  # JSON list of FLAG_CODES that queued it
    status = Column(String(20), nullable=False, default="pending")  # pending|approved|rejected
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class QueryMetric(Base):
    """One row per answered query - drives the metrics dashboard."""

    __tablename__ = "query_metrics"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    node_id = Column(String(60), nullable=False, default="local")
    cache_hit = Column(Boolean, nullable=False, default=False)
    source = Column(String(20), nullable=False, default="local")
    # generated|cache|cloud|local|org - "cloud" = escalated to a paid model (the only
    # source where anything left the perimeter; drives the exact Sovereignty %)
    duration_ms = Column(Integer, nullable=True)
    model = Column(String(80), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ScheduledTask(Base):
    """A task the agent runs on a schedule or one-off."""

    __tablename__ = "scheduled_tasks"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(200), nullable=False)
    goal = Column(Text, nullable=False)  # the instruction sent to the agent
    schedule = Column(String(60), nullable=False, default="once")
    # "once" | "hourly" | "daily" | "weekly" | "HH:MM" | "HH:MM weekdays"
    timezone = Column(String(64), nullable=False, default="")  # IANA name; empty = legacy UTC
    schedule_anchor = Column(DateTime, nullable=True)
    cadence_needs_review = Column(Boolean, nullable=False, default=False)
    cadence_review_reason = Column(String(400), nullable=False, default="")
    occurrences_materialized = Column(Boolean, nullable=False, default=False, server_default="0")
    status = Column(String(20), nullable=False, default="pending")
    # pending | running | done | failed | cancelled
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    interrupted_run_at = Column(DateTime, nullable=True)
    interrupted_inputs = Column(Text, nullable=True)
    last_result = Column(Text, nullable=True)
    # Runtime cross-check verifier verdict on the last result (advisory; the run still delivers).
    # verify_needs_review True = the verifier wants a human to look before trusting it (e.g. the result
    # did not cover the goal, or an independent model disagreed). See anthill.verify.
    verify_needs_review = Column(Boolean, nullable=False, default=False)
    verify_reason = Column(String(400), nullable=False, default="")
    verify_confidence = Column(String(8), nullable=False, default="")
    run_count = Column(Integer, nullable=False, default=0)
    queued_inputs = Column(
        Text, nullable=False, default=""
    )  # JSON list of queued follow-up instructions
    # Attached context resolved at run time: {"files":[{"server_id","ref"}], "snippets":[id,...]}.
    # Files are read live from the connected MCP server; snippets from the saved Snippet rows.
    context_refs = Column(Text, nullable=False, default="")
    # Tiers (solo | team | org): solo = local model + personal wiki; team = team wiki (local in solo,
    # org cloud in org mode); org = org cloud model + org wiki. See anthill.planes.
    plane = Column(String(10), nullable=False, default="solo")  # solo | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when plane == team
    # The chat message this task was spawned from (P3: chat is the creation surface), or NULL
    # for tasks created on the Tasks page. Links a task back to its conversation.
    source_message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True)
    # #278: decided ONCE at creation/edit time, never live mid-run (no one is present on an unattended
    # task to approve anything) - if a run's answer looks uncertain, may it retry once on the
    # connected org/RunPod/inference-provider backend? Bounded by OrgSettings.task_escalation_cap_
    # per_month; when off or the cap is hit, the run is flagged via verify_needs_review instead.
    escalate_on_uncertainty = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Agent(Base):
    """A named, persistent, autonomous worker with a standing mandate - the third surface
    (chat / scheduled task / agent). An 'employee with a role': configured once, it then runs
    toward its mandate via AgentExecutor in its plane's memory + wiki + skill scope, under the
    same governance as a ScheduledTask (its consequential actions are verifier-checked and
    human-gateable).

    Scoping mirrors ScheduledTask + MemoryItem: ``org_id`` partitions the org; ``plane`` +
    ``team_id`` + ``created_by`` decide the memory/wiki/skill scope. A solo (personal) agent is
    always local and never touches the org cloud - the privacy invariant is structural (it runs
    through ``planes.route`` + ``workspace_for``, exactly like a Solo task).
    """

    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # owner (personal agents)
    name = Column(String(120), nullable=False)
    persona = Column(Text, nullable=False, default="")  # role/persona - who the agent is
    mandate = Column(Text, nullable=False, default="")  # the standing goal it works toward
    plane = Column(String(10), nullable=False, default="solo")  # solo | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when plane == team
    model = Column(String(80), nullable=False, default="")  # optional pin; else per-step routing
    schedule = Column(String(60), nullable=False, default="manual")
    # manual (run on demand) | hourly | daily | weekly | "HH:MM"
    connectors = Column(String(300), nullable=False, default="")
    # optional CSV of allowed tool scopes (web,wiki,files,docs,email,mcp); empty = default scopes
    governance = Column(String(12), nullable=False, default="standard")  # standard | strict
    # standard: approval-flagged tools (e.g. draft_email) are queued for a human, not run headless;
    # strict: also queue wiki/doc-writing actions.
    status = Column(String(12), nullable=False, default="active")  # active | paused
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_result = Column(Text, nullable=True)
    verify_needs_review = Column(Boolean, nullable=False, default=False)
    verify_reason = Column(String(400), nullable=False, default="")
    verify_confidence = Column(String(8), nullable=False, default="")
    run_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentApproval(Base):
    """A consequential action an Agent wanted to take, held for human approval - the agent's
    human gate (mirrors WikiReview for wiki writes).

    When a governed agent run reaches a tool that needs approval (``draft_email``, or any write
    under a ``strict`` policy), the action is NOT executed headless: a row is created here and
    surfaced on the agent's page for a human to approve or reject. On approval the stored call is
    executed once under the agent's own scope; on rejection it is dropped.
    """

    __tablename__ = "agent_approvals"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    tool = Column(String(60), nullable=False)  # the tool name the agent wanted to run
    arguments = Column(Text, nullable=False, default="")  # JSON of the proposed call arguments
    scope = Column(String(20), nullable=False, default="")  # the tool's permission scope
    rationale = Column(Text, nullable=False, default="")  # the run goal/step context
    status = Column(String(12), nullable=False, default="pending")  # pending | approved | rejected
    result = Column(Text, nullable=False, default="")  # tool output once approved+executed
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentRun(Base):
    """One recorded execution of an Agent - the run history.

    The ``Agent`` row keeps only the latest summary (``last_result`` / ``verify_*`` / ``run_count``);
    this keeps EVERY run so a human can see what the agent did over time, when, how long it took, and
    how the verifier judged each one. Appended by the scheduler's agent tick after each run (best-
    effort - a history-write hiccup never breaks the run loop).
    """

    __tablename__ = "agent_runs"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    trigger = Column(String(12), nullable=False, default="scheduled")  # scheduled | manual
    status = Column(String(12), nullable=False, default="ok")  # ok | error
    result = Column(Text, nullable=False, default="")  # the run's answer ("" on error)
    error = Column(Text, nullable=False, default="")  # the failure message when status == error
    # per-run verifier verdict (the Agent row only holds the latest; this preserves each run's)
    verify_needs_review = Column(Boolean, nullable=False, default=False)
    verify_reason = Column(String(400), nullable=False, default="")
    verify_confidence = Column(String(8), nullable=False, default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    duration_ms = Column(Integer, nullable=True)


class TaskOccurrence(Base):
    """One durable, ordered unit of pending or claimed task work."""

    __tablename__ = "task_occurrences"
    __table_args__ = (
        Index("ix_task_occurrences_status_due_task", "status", "due_at", "task_id"),
        Index("ix_task_occurrences_task_status_due", "task_id", "status", "due_at", "id"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id"), nullable=False)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    kind = Column(String(12), nullable=False)  # scheduled | manual | queued
    status = Column(String(12), nullable=False, default="pending")
    # pending | claimed | interrupted | paused | completed | cancelled
    due_at = Column(DateTime, nullable=False)
    inputs = Column(Text, nullable=False, default="[]")  # JSON list owned only by this occurrence
    claimed_run_id = Column(Integer, nullable=True)
    cadence_deferred = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TaskRunArchive(Base):
    """Legacy task-run history whose task parent no longer exists."""

    __tablename__ = "task_run_archives"

    id = Column(Integer, primary_key=True)
    source_run_id = Column(Integer, nullable=False, unique=True)
    original_task_id = Column(Integer, nullable=True)
    original_org_id = Column(Integer, nullable=True)
    original_row = Column(Text, nullable=False)
    reason = Column(String(80), nullable=False)
    archived_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TaskRun(Base):
    """One recorded execution of a ScheduledTask - the run history (mirrors ``AgentRun``).

    The ``ScheduledTask`` row keeps only the latest summary (``last_result``/``verify_*``/
    ``run_count``); each run overwrote the last, so a human could not see what a recurring task did
    over time (and the Tasks page even advertised "history" it did not keep). This preserves every
    run. An occurrence claim opens the row as ``running``; ``occurrence_id``, ``scheduled_for``, and
    ``claimed_inputs`` bind it to that exact work item. A current claim publishes its outcome, while a
    stale attempt closes as an error without a result. Startup recovery closes attempts orphaned by a
    restart.
    """

    __tablename__ = "task_runs"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id"), nullable=False)
    occurrence_id = Column(Integer, ForeignKey("task_occurrences.id"), nullable=True)
    trigger = Column(String(12), nullable=False, default="scheduled")  # scheduled | manual
    status = Column(String(12), nullable=False, default="ok")  # running | ok | error
    result = Column(Text, nullable=False, default="")  # the run's result ("" on error)
    error = Column(Text, nullable=False, default="")  # the failure message when status == error
    verify_needs_review = Column(Boolean, nullable=False, default=False)
    verify_reason = Column(String(400), nullable=False, default="")
    verify_confidence = Column(String(8), nullable=False, default="")
    claimed_inputs = Column(Text, nullable=False, default="")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    scheduled_for = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    duration_ms = Column(Integer, nullable=True)


class ProposedSkill(Base):
    """A skill auto-distilled from a successful agent run, awaiting human accept/reject - NEVER
    written live. The governed skill-distillation loop (agentskills.io stays the artifact format): an
    agent proposes a reusable skill; a human accepts it (then it is written as a conformant SKILL.md,
    through the normal review gate for shared scopes) or rejects it. Deduped against pending proposals
    before it is created. New table, so create_all picks it up on existing installs."""

    __tablename__ = "proposed_skills"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # the source agent's owner
    agent_id = Column(Integer, nullable=True)  # source agent (provenance)
    scope = Column(String(10), nullable=False, default="personal")  # personal | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    name = Column(String(120), nullable=False)
    description = Column(String(400), nullable=False, default="")
    when_to_use = Column(String(400), nullable=False, default="")
    instructions = Column(Text, nullable=False, default="")
    status = Column(String(12), nullable=False, default="pending")  # pending | accepted | rejected
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeItem(Base):
    """A thin DB index of what wiki/skill/principles content currently EXISTS on disk (phase 7 of
    #683, spec requirement 6: "a thin database layer over the files for a page/skill registry").

    Markdown files on disk stay the sole source of truth for content - this table is purely additive:
    an index/ledger over them, kept in sync from as few choke points as possible (`sync_page()` /
    `sync_skill()` in `knowledge_registry.py`), never hand-edited. Unlike `WikiReview`/`ProposedSkill`
    (which cover a change AWAITING approval), this is a durable record of what is currently live,
    feeding the knowledge digest and any future "browse everything" view. New table, so `create_all`
    picks it up on existing installs - no migration-registry entry needed.
    """

    __tablename__ = "knowledge_items"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    scope = Column(String(10), nullable=False, default="personal")  # personal | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when scope == team
    kind = Column(String(12), nullable=False, default="page")  # page | skill | principles
    slug = Column(String(120), nullable=False)
    title = Column(String(300), nullable=False, default="")
    path = Column(String(500), nullable=False, default="")  # on-disk path, for reference/debugging
    review_state = Column(
        String(20), nullable=False, default="approved"
    )  # matches WikiReview.status
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_editor_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class QueuedUpload(Base):
    """A wiki upload made while the local model is still downloading on first run (#683 phase 5).

    ``wiki_upload`` cannot ingest anything until a model is actually installed and serving, so
    rather than fail with a generic "model not ready" error it saves the file durably here (NOT
    the request's ephemeral tempdir, which is cleaned up before the model would ever be ready)
    and the scheduler's ``_process_queued_uploads_tick`` processes it automatically once
    ``OrgSettings.local_model_pulling`` clears. ``stored_path`` points at the durable copy under
    the app's files root; it is deleted once the row leaves ``queued`` (done or error), so the
    row itself is the permanent record and the file is scratch space.
    New table, so ``create_all`` picks it up on existing installs.
    """

    __tablename__ = "queued_uploads"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_scope = Column(String(10), nullable=False, default="personal")  # personal | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    status = Column(String(12), nullable=False, default="queued")  # queued | done | error
    error = Column(Text, nullable=False, default="")  # the failure message when status == error
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime, nullable=True)


class ContributionProposal(Base):
    """A public/customer contribution idea (the ASDD "spec object"), drafted by the intake agent from a
    free-text idea + optional reference code. This is the channel-agnostic heart of the public
    contribution surface: chat, the website board, and a bring-your-own-agent submission all converge on
    this one shape. Governance guardrails it carries:

    - `idea` and `reference_code` are UNTRUSTED data - the intake agent reads them as data, never as
      instructions, and never writes them to the wiki/model (ASDD security membrane).
    - `reference_code` is REFERENCE, never a diff: a developer agent re-derives from `spec`, so attached
      code is never merged verbatim.
    - `agent_drafted` records that the spec was written by the intake agent (disclosure).
    - `proposer_provider`/`proposer_handle` carry attribution so the same row serves the (later) public
      board and the public-repo folder without a schema change.

    New table, so create_all picks it up on existing installs. Later phases add triage, GitHub-issue
    minting, and merge attribution on top of this same row (status advances through the lifecycle)."""

    __tablename__ = "contribution_proposals"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # the in-app submitter
    # Channel it arrived through, and how the proposer is attributed. For P0 (in-app chat/page) the
    # provider is "inapp"; the public board later sets "x"/"linkedin", GitHub authoring sets "github".
    source = Column(String(12), nullable=False, default="inapp")  # inapp | chat | board
    proposer_provider = Column(String(12), nullable=False, default="inapp")
    proposer_handle = Column(String(120), nullable=False, default="")
    kind = Column(String(12), nullable=False, default="feature")  # feature | bug | improvement
    title = Column(String(200), nullable=False, default="")
    idea = Column(Text, nullable=False, default="")  # the raw free-text idea (untrusted data)
    spec = Column(
        Text, nullable=False, default=""
    )  # the intake-agent-drafted spec (problem/solution/AC)
    reference_code = Column(
        Text, nullable=True
    )  # attached code, stored + shown as DATA, never a diff
    priority = Column(String(8), nullable=False, default="medium")  # low | medium | high
    # submitted -> triaged -> accepted | parked -> minted -> building -> merged (later phases advance it)
    status = Column(String(12), nullable=False, default="submitted")
    completeness = Column(String(12), nullable=False, default="complete")  # complete | needs_detail
    agent_drafted = Column(
        Boolean, nullable=False, default=True
    )  # disclosure: spec written by the agent
    # Relevance triage (P3): the governance-reviewer agent's ADVISORY read of roadmap fit, and the
    # human decision that follows it. The agent recommends; a human accepts or parks (advisory posture,
    # two review roles stay separate). All additive columns - the startup migration adds them in place.
    triage_recommendation = Column(String(8), nullable=False, default="")  # "" | accept | park
    triage_relevance = Column(String(8), nullable=False, default="")  # "" | high | medium | low
    triage_reasons = Column(Text, nullable=False, default="")  # shown to the submitter when parked
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # human who accepted/parked
    # P4: once accepted, a human mints a GitHub issue (attributed, reference code as data). The
    # developer agent later re-derives a PR from the spec. Additive columns (startup migration).
    issue_number = Column(Integer, nullable=True)  # the GitHub issue this proposal became
    issue_url = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TrainingExample(Base):
    """Curated Q&A pairs for future fine-tuning (Perplexity-style).

    Quality tiers:
      gold   - admin-approved wiki page or explicit thumbs-up
      silver - reused by org index (other nodes found it useful)
      bronze - generated answer, not yet validated

    Export with: anthill export-training --output dataset.jsonl
    """

    __tablename__ = "training_examples"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # who produced the signal
    instruction = Column(Text, nullable=False)  # the question / task
    context = Column(Text, nullable=False, default="")  # wiki pages used
    output = Column(Text, nullable=False)  # the answer
    quality = Column(String(10), nullable=False, default="bronze")  # gold|silver|bronze
    # scope decides what may train the SHARED org model:
    #   personal - one user's signal; improves that user only, never the org model
    #   team     - corroborated within a team; shared with that team
    #   org      - corroborated (>=N distinct users) or review-approved -> org model + KB
    scope = Column(String(10), nullable=False, default="personal")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when scope == team
    model = Column(String(80), nullable=False, default="")
    task_type = Column(String(20), nullable=False, default="general")
    source = Column(String(20), nullable=False, default="chat")  # chat|wiki|agent|snippet
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # False when a contributing model was reached through a third-party closed-model API whose own
    # terms restrict using its outputs to train another model (e.g. anthropic/moonshot/deepseek's
    # DIRECT APIs via anthill/hybrid/'s escalation providers) - captured regardless, but not eligible
    # for a later fine-tuning export. Self-hosted and open-weight-via-inference-provider paths stay True.
    training_eligible = Column(Boolean, nullable=False, default=True)
    # JSON array of {"member_index", "text"} - each council member's own draft answer, captured
    # alongside the final synthesized `output` above (previously discarded once synthesis ran). "" when
    # the answer was not council-synthesized (a single member, or no council configured).
    council_drafts = Column(Text, nullable=False, default="")


class TrainingRun(Base):
    """One local fine-tuning cycle (audit trail). Created by the 24h scheduler
    when new gold accumulated; executed by the trainer; eval-gated before promote."""

    __tablename__ = "training_runs"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    base_model = Column(String(120), nullable=False, default="")
    backend = Column(String(20), nullable=False, default="onprem")  # onprem | vpc
    gold_count = Column(Integer, nullable=False, default=0)  # gold examples at trigger
    status = Column(String(20), nullable=False, default="scheduled")
    # scheduled | running | promoted | rejected | failed | skipped
    model_version = Column(Integer, nullable=True)  # vN if promoted
    eval_note = Column(Text, nullable=True)  # win/lose detail
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)


class Folder(Base):
    """A user-created folder for grouping their own conversations (chat organisation)."""

    __tablename__ = "folders"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(80), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    """A chat session (browser UI or API)."""

    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(200), nullable=False, default="New conversation")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    memory_mark = Column(Integer, nullable=False, default=0)  # msg count at last memory extraction
    # Tiers (solo | team | org): chosen at conversation creation; routed by anthill.planes.
    plane = Column(String(10), nullable=False, default="solo")  # solo | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when plane == team
    pinned = Column(Boolean, nullable=False, default=False)  # kept at the top of the chat list
    # Slack thread key ("<channel>:<root_ts>" for a channel thread, "dm:<channel>" for a DM). One
    # conversation per Slack thread, so follow-ups in the thread carry context. "" for normal chats.
    slack_thread = Column(String(80), nullable=False, default="", index=True)
    folder_id = Column(
        Integer, ForeignKey("folders.id"), nullable=True
    )  # user's chat folder, or None
    messages = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    """One turn in a conversation."""

    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user | assistant | tool
    content = Column(Text, nullable=False)
    model = Column(String(80), nullable=False, default="")
    # True when this turn was answered without leaving the machine (local Ollama, or the on-device
    # mlx-lm fine-tune) - False for any real network call (an org's own cloud, or a third-party
    # inference provider). Only meaningful for role="assistant"; drives the chat UI's local/remote badge.
    answered_locally = Column(Boolean, nullable=False, default=True)
    # True when Automated mode's escalation attachment fired on this turn (compound-compute-tiers
    # spec) - drives chat.html's server-rendered "expert" badge on history reload, mirroring the live
    # SSE stream's d.meta.escalated. Distinct from answered_locally: an escalated turn is always remote,
    # but not every remote turn is an escalation (e.g. an org/cloud-tier account's ordinary answer).
    escalated = Column(Boolean, nullable=False, default=False)
    generation_failed = Column(Boolean, nullable=False, default=False)
    cache_hit = Column(Boolean, nullable=False, default=False)
    wiki_slugs = Column(String(500), nullable=False, default="")  # comma-separated
    thumbs_up = Column(Boolean, nullable=True)  # None=unrated, True/False
    # Content provenance: a deterministic hash over (conversation, role, content) so any knowledge that
    # flows from this turn into the wiki can be traced back to the exact input it came from. Content-
    # addressable (same input -> same hash); the author + session are the linked conversation. Stamped
    # automatically on insert by the before_insert event below, so every creation site is covered.
    provenance = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    conversation = relationship("Conversation", back_populates="messages")


def message_provenance(conversation_id, role: str, content: str) -> str:
    """Content-addressable provenance hash for a chat turn: sha256 over the session, role, and content.
    Same input -> same hash, so a wiki entry can cite the exact input it came from and it can be verified."""
    import hashlib

    payload = f"{conversation_id or 0}:{role or ''}:{content or ''}".encode()
    return hashlib.sha256(payload).hexdigest()


@event.listens_for(ChatMessage, "before_insert")
def _stamp_message_provenance(mapper, connection, target):
    if not target.provenance:
        target.provenance = message_provenance(target.conversation_id, target.role, target.content)


class AgentIdentity(Base):
    """A governed agent identity (multi-agent safety + auditability).

    Each autopilot acts under its own named identity with scoped permissions -
    actions are attributable to it, limited to its scopes, and blocked entirely
    if deactivated. Modelled on the 'agent has its own identity' pattern.
    """

    __tablename__ = "agent_identities"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(60), nullable=False)  # e.g. scheduler, chat-agent
    agent_type = Column(String(30), nullable=False, default="custom")
    scopes = Column(String(300), nullable=False, default="web,wiki,files,docs")
    active = Column(Boolean, nullable=False, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # A2A: an AES-GCM-encrypted MCP bearer token authenticating THIS identity to the org's
    # own /mcp endpoint (governed agent-to-agent). Empty = no token issued.
    mcp_token_enc = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PushSubscription(Base):
    """A browser Web Push subscription (one per installed PWA / browser)."""

    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    endpoint = Column(String(500), unique=True, nullable=False)
    p256dh = Column(String(200), nullable=False, default="")
    auth = Column(String(100), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    """One in-app notification for a user - the persisted record behind the bell + notification centre
    (#284). Created through the single ``notify()`` chokepoint (``web/notify.py``); a web push (via the
    ``PushSubscription`` above) is the optional out-of-app channel for the same event."""

    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(
        String(40), nullable=False, default="info"
    )  # coarse category: approval | review | run | info
    title = Column(String(200), nullable=False, default="")
    body = Column(String(500), nullable=False, default="")
    link = Column(String(300), nullable=False, default="")  # in-app URL to act on it
    read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class Snippet(Base):
    """A piece of content a user deliberately marked + tagged to keep.

    The strongest human signal of value - so a saved snippet becomes a GOLD
    training example, and the model writes its 'red line' (rationale): why this
    is worth keeping and learning from, tying it to the question that produced it.
    """

    __tablename__ = "snippets"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    content = Column(Text, nullable=False)  # the saved text (whole msg or selection)
    question = Column(Text, nullable=False, default="")  # the prompt that produced it (context)
    tags = Column(String(300), nullable=False, default="")  # comma-separated
    rationale = Column(Text, nullable=False, default="")  # the "red line" - why it's relevant
    source = Column(String(20), nullable=False, default="chat")  # chat | task
    source_ref = Column(String(60), nullable=False, default="")  # conv/msg/task id
    content_key = Column(String(64), nullable=False, default="")  # hash for corroboration
    scope = Column(String(10), nullable=False, default="personal")  # personal|team|org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when scope == team
    wiki_slug = Column(String(120), nullable=True)  # set if filed to the wiki
    training_id = Column(Integer, nullable=True)  # the gold TrainingExample id
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MemoryItem(Base):
    """A durable thing the system remembers, distilled from chats/tasks.

    The automatic tier between the cache (exact reuse) and the wiki (curated
    knowledge): short facts/decisions/preferences extracted from everyday use and
    recalled into future answers. Personal-first; org-wide only on promotion
    (corroboration / admin), same rule as training.
    """

    __tablename__ = "memory_items"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # owner; null = org-wide
    scope = Column(String(10), nullable=False, default="personal")  # personal | team | org
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)  # set when scope == team
    kind = Column(
        String(20), nullable=False, default="fact"
    )  # fact|decision|preference|task_outcome
    text = Column(Text, nullable=False)
    embedding = Column(Text, nullable=False, default="")  # base64 float32 (best-effort)
    source = Column(String(20), nullable=False, default="chat")  # chat | task
    source_id = Column(Integer, nullable=True)  # conversation / task id
    corroborations = Column(Integer, nullable=False, default=1)
    # "Keep personal": when True, this memory is never auto-promoted out of personal scope (the user
    # opted it out of the corroboration promotion). Off by default.
    pinned_personal = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MCPServer(Base):
    """A third-party MCP server the org's agents may use (Anthill as MCP client).

    Off until an admin approves it: only status=="approved" servers contribute tools.
    Each tool is wrapped as mcp_<slug>_<tool> under the coarse `mcp` scope, so it is
    scope-gated by the agent identity and audited per call like any other tool.
    """

    __tablename__ = "mcp_servers"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(80), nullable=False)
    transport = Column(String(10), nullable=False, default="http")  # http | stdio
    url = Column(String(500), nullable=False, default="")  # http transport
    command = Column(String(500), nullable=False, default="")  # stdio transport
    headers_enc = Column(Text, nullable=False, default="")  # AES-GCM JSON of auth headers
    env_enc = Column(
        Text, nullable=False, default=""
    )  # AES-GCM JSON of env vars for a stdio server
    # OAuth (hosted MCP servers): the registered client + endpoints, and the live tokens, both
    # AES-GCM encrypted. oauth_client_enc = {client_id, client_secret, token_endpoint, resource};
    # oauth_tokens_enc = {access_token, refresh_token, expires_at}.
    oauth_client_enc = Column(Text, nullable=False, default="")
    oauth_tokens_enc = Column(Text, nullable=False, default="")
    # pending|approved|disabled|requested|rejected (requested = a non-admin asked to add it)
    status = Column(String(10), nullable=False, default="pending")
    require_approval = Column(Boolean, nullable=False, default=False)  # per-call human approval
    tool_names = Column(Text, nullable=False, default="")  # JSON list from last successful test
    catalog_id = Column(
        String(60), nullable=False, default=""
    )  # connector-catalog entry, if added from the gallery
    last_error = Column(String(300), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # non-admin requester
    reject_reason = Column(String(300), nullable=False, default="")
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SlackBot(Base):
    """Inbound Slack app config for one org: members @mention the bot (or use /anthill, or DM it) and
    get answers grounded in the org wiki. This is the conversational surface - distinct from the
    OUTBOUND Slack MCP connector (which lets the agent read/post as a tool). A dedicated table (not
    columns on OrgSettings) so it self-creates with no migration; secrets are AES-GCM at rest."""

    __tablename__ = "slack_bots"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    team_id = Column(String(32), nullable=False, default="")  # Slack workspace id (T...)
    bot_user_id = Column(
        String(32), nullable=False, default=""
    )  # the bot's own user id (ignore self)
    bot_token_enc = Column(Text, nullable=False, default="")  # xoxb- token, encrypted
    signing_secret_enc = Column(Text, nullable=False, default="")  # Slack signing secret, encrypted
    # OAuth "Add to Slack" install: the app-level credentials (from the Slack app's Basic Information).
    # With these set, an admin installs by clicking a button - the bot token above is fetched via OAuth
    # rather than pasted. client_id is not secret; client_secret is AES-GCM at rest.
    client_id = Column(String(64), nullable=False, default="")
    client_secret_enc = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DiscordApp(Base):
    """Inbound Discord app config for one org: members run `/anthill <message>` in the org's members
    Discord and get an answer grounded in the org wiki, or route an idea into the contribution intake.
    The conversational surface (distinct from the OUTBOUND Discord MCP connector). Stage 1 trust boundary
    is the guild: only interactions from the configured members guild are served. Nothing here is secret
    - the public key verifies Discord's Ed25519 request signature, and the per-request interaction token
    authorizes the reply - so no encryption and no migration needed (a dedicated self-creating table)."""

    __tablename__ = "discord_apps"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    application_id = Column(String(32), nullable=False, default="")  # the Discord application id
    public_key = Column(
        String(80), nullable=False, default=""
    )  # Ed25519 public key (hex), NOT secret
    guild_id = Column(
        String(32), nullable=False, default=""
    )  # the trusted members guild (server) id
    enabled = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MCPAccessLog(Base):
    """One row per external MCP request served (Anthill as MCP server) - the record
    of what org-brain data was exposed, to whom, and when."""

    __tablename__ = "mcp_access_log"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    client = Column(String(120), nullable=False, default="")  # caller label / IP
    consumer_id = Column(Integer, ForeignKey("mcp_consumers.id"), nullable=True)  # which consumer
    tool = Column(String(60), nullable=False, default="")  # exposed tool called
    args_summary = Column(String(300), nullable=False, default="")  # truncated, no secrets
    query = Column(Text, nullable=False, default="")  # the query text (truncated)
    resources = Column(String(60), nullable=False, default="")  # resource(s) touched
    allowed = Column(Boolean, nullable=False, default=True)  # allow/deny decision
    reason = Column(String(120), nullable=False, default="")  # deny reason, or "ok"
    result_summary = Column(String(300), nullable=False, default="")  # truncated answer summary
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MCPConsumer(Base):
    """A named external party allowed to query the org brain over MCP (Anthill as server).

    Replaces the single shared org token: each consumer has its own scoped, revocable token, and
    (in review mode) must be admin-approved before any of its queries are answered."""

    __tablename__ = "mcp_consumers"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(80), nullable=False)
    purpose = Column(String(255), nullable=False, default="")
    token_enc = Column(Text, nullable=False, default="")  # the consumer's bearer token, AES-GCM
    scopes = Column(String(60), nullable=False, default="")  # CSV of wiki|cache|memory
    status = Column(String(10), nullable=False, default="pending")  # pending|approved|revoked
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def normalize_topology(v: str) -> str:
    """Map any stored deployment topology to the current set (org | solo).

    Legacy alpha rows used local|gpu: local -> solo, gpu -> org, blank -> org. Used for display
    throughout the settings UI, and (since #278) `plane_routing.shares_org_model` branches on the
    result too - a solo-topology account with a connected endpoint stays on its local default rather
    than sharing "one model per account" with an org that was never actually created.
    """
    return {"local": "solo", "gpu": "org"}.get(v, v or "org")


# ── engine + session factory ──────────────────────────────────────────────────


def _precreate_private(p: Path) -> None:
    """Create the database file owner-only (0600) *before* SQLite opens it, so there is no window where it
    or its WAL sidecars are world-readable. SQLite derives the mode of the ``-wal`` and ``-shm`` files from
    the main database file when it creates them, so if the db is already 0600 at first open, the sidecars
    are private from birth - no reliance on ``_secure_db_file`` racing the first write. Only ever creates a
    file that does not exist yet (an existing db keeps its mode; ``_secure_db_file`` fixes those). The
    ``0o600`` survives any umask, since a umask only clears bits. Best-effort: never block startup."""
    try:
        if not p.exists():
            os.close(os.open(p, os.O_CREAT | os.O_WRONLY, 0o600))
    except Exception as e:
        log.debug("could not pre-create %s at 0600 (chmod after open will still apply): %s", p, e)


def get_engine(db_path: Path | None = None):
    p = db_path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    _precreate_private(p)  # own the file at 0600 before SQLite creates it (and its sidecars)
    return create_engine(f"sqlite:///{p}", connect_args={"check_same_thread": False})


def _ensure_columns(engine, table: str, columns: dict[str, str]) -> None:
    """Add any missing columns to an existing SQLite table (there is no migration framework, and
    ``create_all`` adds tables but never new columns). Idempotent + best-effort: a fresh table already
    has every column, so this is a no-op; only an install that created the table under an older schema
    gets the ``ALTER TABLE ... ADD COLUMN``. SQLite-only, matching the app's storage."""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            }
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    except Exception:
        pass  # never block startup on a best-effort column top-up


# The secondary indexes the isolation filters need. After the owner-scoping work (#611) essentially
# every read is filtered by org_id and/or user_id - and SQLite does NOT index a foreign key
# automatically, so each of those was a full table scan. A composite (org_id, user_id) also serves an
# org_id-only lookup through its leftmost prefix, so one index covers both query shapes.
_HOT_INDEXES: dict[str, str] = {
    "ix_conversations_org_user": "conversations(org_id, user_id)",
    "ix_chat_messages_conversation": "chat_messages(conversation_id)",
    "ix_memory_items_org_user": "memory_items(org_id, user_id)",
    "ix_snippets_org_user": "snippets(org_id, user_id)",
    "ix_folders_org_user": "folders(org_id, user_id)",
    # tasks + agents name their owner `created_by`, not `user_id`
    "ix_scheduled_tasks_org_creator": "scheduled_tasks(org_id, created_by)",
    "ix_agents_org_creator": "agents(org_id, created_by)",
    "ix_audit_log_org": "audit_log(org_id)",
    "ix_training_examples_org": "training_examples(org_id)",
    "ix_team_memberships_user": "team_memberships(user_id)",
    "ix_wiki_reviews_org": "wiki_reviews(org_id)",
    "ix_task_runs_active_task": "task_runs(task_id, status)",
    "ix_task_runs_recovery": "task_runs(status, started_at)",
    "ix_task_occurrences_task_status_due": "task_occurrences(task_id, status, due_at, id)",
}


def _ensure_indexes(engine) -> None:
    """Create the isolation-filter indexes. ``create_all`` only builds indexes for tables it creates, so
    an existing install would never get a new one - hence ``CREATE INDEX IF NOT EXISTS``, which covers a
    fresh database and an upgraded one alike. Per-index best-effort: a missing index costs speed, never
    correctness, so one failure must not stop the rest or block startup."""
    from sqlalchemy import text

    for name, target in _HOT_INDEXES.items():
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {target}"))
        except Exception as e:
            log.debug("could not create index %s on %s: %s", name, target, e)


def _secure_db_file(engine) -> None:
    """Make the on-disk database private (0600), like the ``secrets.env`` next to it. SQLite creates the
    file world-readable under the usual umask (0644), but it holds the entire org's data - chats, wiki,
    memory - so on a shared host any other OS user could read it. The encrypted secret columns stay safe
    regardless (their key lives in the 0600 secrets.env), but the content should not be readable either.

    The WAL journal (enabled in ``_sqlite_pragmas``) writes recently-committed pages to a ``-wal`` sidecar
    and its index to ``-shm``; the ``-wal`` holds real content until a checkpoint folds it back in, so it
    is exactly as sensitive as the db and must be locked down too. SQLite creates the sidecars with the
    mode the db had when the connection first opened it (0644, before this function ran), so they do NOT
    inherit the 0600 - lock them explicitly. Once they exist at 0600 they are reused, and any later
    recreation copies the mode of the now-0600 db file.

    On a fresh install ``_precreate_private`` already makes the db (and therefore its sidecars) 0600 from
    birth; this is the belt-and-braces pass that also covers an existing install whose db file was created
    0644 before this change shipped. Best-effort: a permissions failure must never block startup, but it is
    logged rather than silently swallowed - it is security-relevant. An in-memory db has no file."""
    try:
        name = engine.url.database
        if not name or name == ":memory:":
            return
        base = Path(name)
        for p in (base, Path(f"{name}-wal"), Path(f"{name}-shm")):
            if p.exists():
                os.chmod(p, 0o600)
    except Exception as e:
        log.warning(
            "could not restrict database file permissions (db content may be readable): %s", e
        )


def _log_fk_violations(engine) -> None:
    """Report rows that already violate a foreign key. Enforcement (see ``_sqlite_pragmas``) only guards
    NEW writes, so anything an older install orphaned while enforcement was off stays readable and
    silent. Surface it once so it can be cleaned up. Never blocks startup."""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            rows = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
        if rows:
            log.warning(
                "%d pre-existing foreign-key violation(s) in the database (rows orphaned before "
                "enforcement was enabled). They stay readable; new writes are now checked. First few: %s",
                len(rows),
                rows[:5],
            )
    except Exception:
        pass  # diagnostics only


def create_tables(engine=None):
    from sqlalchemy import inspect as _sa_inspect

    engine = engine or get_engine()
    # A truly empty database is a first run: create_all builds the latest schema, so the versioned
    # migrations below are stamped-not-run (their end-state already exists). Checked before create_all.
    fresh = not _sa_inspect(engine).get_table_names()
    Base.metadata.create_all(engine)
    # Columns added to existing tables after their first release need an explicit top-up.
    _ensure_columns(
        engine,
        "slack_bots",
        {"client_id": "VARCHAR(64) DEFAULT ''", "client_secret_enc": "TEXT DEFAULT ''"},
    )
    _ensure_columns(engine, "conversations", {"slack_thread": "VARCHAR(80) DEFAULT ''"})
    _ensure_columns(
        engine, "org_settings", {"escalation_model": "VARCHAR(120) NOT NULL DEFAULT ''"}
    )
    _ensure_columns(
        engine,
        "chat_messages",
        {"generation_failed": "BOOLEAN NOT NULL DEFAULT 0"},
    )
    _ensure_columns(
        engine,
        "org_settings",
        {
            "benchmark_state": "TEXT DEFAULT ''",
            # Added here (not left to the generic migrate.ensure_columns pass) so it exists BEFORE
            # run_migrations() runs below - the version-1 migration backfills this column, and
            # migrate.ensure_columns only runs later, in web.app._db(), after create_tables() returns.
            "org_council_members": "TEXT DEFAULT '[]'",
            "council_review_tasks": "BOOLEAN NOT NULL DEFAULT 1",
            # DEFAULT 1 (already chosen) matches the ORM-level default=True on the column above - an
            # account that already exists has already effectively chosen Solo or org by using the
            # product as one. Only setup_post's fresh OrgSettings() explicitly sets this False.
            "account_type_chosen": "BOOLEAN NOT NULL DEFAULT 1",
            "org_warm_workers": "INTEGER NOT NULL DEFAULT 0",
            "org_lambda_tunnel_key_enc": "TEXT DEFAULT ''",
            "org_lambda_tunnel_port": "INTEGER NOT NULL DEFAULT 0",
            "org_lambda_tunnel_host": "VARCHAR(64) DEFAULT ''",
        },
    )
    _ensure_columns(
        engine,
        "users",
        {
            "must_reset_password": "BOOLEAN NOT NULL DEFAULT 0",
            "web_access_on": "BOOLEAN NOT NULL DEFAULT 0",
            "auth_version": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_columns(
        engine,
        "browser_session_orders",
        {"observed_order": "INTEGER NOT NULL DEFAULT 0"},
    )
    _ensure_columns(
        engine,
        "scheduled_tasks",
        {
            "timezone": "VARCHAR(64) NOT NULL DEFAULT ''",
            "schedule_anchor": "DATETIME",
            "cadence_needs_review": "BOOLEAN NOT NULL DEFAULT 0",
            "cadence_review_reason": "VARCHAR(400) NOT NULL DEFAULT ''",
            "occurrences_materialized": "BOOLEAN NOT NULL DEFAULT 0",
            "interrupted_run_at": "DATETIME",
            "interrupted_inputs": "TEXT",
            "queued_inputs": "TEXT NOT NULL DEFAULT ''",
            "verify_needs_review": "BOOLEAN NOT NULL DEFAULT 0",
            "verify_reason": "VARCHAR(400) NOT NULL DEFAULT ''",
            "verify_confidence": "VARCHAR(8) NOT NULL DEFAULT ''",
        },
    )
    _ensure_columns(
        engine,
        "task_runs",
        {
            "occurrence_id": "INTEGER",
            "scheduled_for": "DATETIME",
        },
    )
    _ensure_columns(
        engine,
        "task_occurrences",
        {"cadence_deferred": "BOOLEAN NOT NULL DEFAULT 0"},
    )
    _secure_db_file(engine)  # 0600, like secrets.env
    # Ordered, versioned migrations for non-additive changes (renames, backfills, table rebuilds).
    # Fresh databases are stamped at head; existing ones apply pending entries after a snapshot. A
    # migration failure logs loudly and boots on the current schema rather than bricking startup.
    try:
        from .migrate import run_migrations

        run_migrations(engine, fresh=fresh)
    except Exception:
        log.exception(
            "versioned schema migration failed; a snapshot was taken, booting current schema"
        )
    # Rebuild migrations drop their old indexes; top up after every structural migration.
    _ensure_indexes(engine)
    _log_fk_violations(engine)
