"""Unit, security-invariant, and two-instance E2E pilot tests for the peer-to-peer Node pillar.

Covers blocking rules from the Node pillar brief (v3.3):
  - Rule 1 & 5: Signed, chained records; unsigned, tampered, and non-member records rejected.
  - Rule 2: Visible exit preview; peer sends logged in egress/usage ledger and Sovereignty metric
            with zero prompt or answer content stored.
  - Rule 3 & Leak test: Canary secret in un-lent/locked wiki areas never reaches peer consult
                        prompts or responses; PII scrubbed on egress.
  - Rule 4: Inbound peer PageChange lands strictly in WikiReview as Proposed/Contested/Quarantined
            (never directly in local wiki; no auto-merge; quarantined excluded from AI retrieval).
  - Rule 7: Device revocation and peer removal reject subsequent records; recovery kit restores
            exact Ed25519 root key; open export contains OKGF wiki + signed JSONL and zero secrets.
  - Rule 12 & 13: Off by default (P2P routes return 403); peer consultation exposes 0 tools.
  - Two-instance E2E pilot: Pair + SAS -> .anthill-bundle sync -> live HTTP sync -> counter-check
                            consultation -> Open Thread -> Wiki Review acceptance.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from anthill.node import consult as node_consult
from anthill.node import identity as node_id
from anthill.node import lending as node_lending
from anthill.node import log as node_log
from anthill.node import pairing as node_pairing
from anthill.node import wiki_bridge as node_wiki_bridge
from anthill.web import db as web_db
from anthill.web import metrics as web_metrics
from anthill.web import node_routes
from anthill.web.db import (
    Base,
    NodeEgressLedger,
    NodeInvite,
    NodePeer,
    NodeRecord,
    Organization,
    OrgSettings,
    User,
    WikiReview,
)
from anthill.wiki import okf
from anthill.wiki.workspace import Workspace

# -- Helpers for isolated two-instance test harnesses --------------------------


class _StubPeerBackend:
    """Model-free backend double that records every prompt and forbids tool calls (Rule 13)."""

    def __init__(self, model: str, reply_json: dict[str, Any]) -> None:
        self.model = model
        self.reply_json = reply_json
        self.prompts: list[list[dict[str, str]]] = []
        self.chat_calls = 0

    def chat(self, messages, **kwargs) -> str:
        self.chat_calls += 1
        self.prompts.append([{"role": m.role, "content": m.content} for m in messages])
        if kwargs.get("fmt") == "json":
            return json.dumps(self.reply_json)
        return "Independent peer answer grounded in lent solar energy notes."

    def chat_with_tools(self, *args, **kwargs):  # pragma: no cover - must NEVER be called
        raise AssertionError("Rule 13 violation: peer consultation attempted to invoke tools!")


def _make_desktop_instance(
    tmp_path: Path,
    *,
    name: str,
    email: str,
    org_id: int = 1,
    model: str = "qwen2.5:3b",
) -> dict[str, Any]:
    """Create an isolated Anthill Desktop FastAPI + SQLite + Workspace instance."""
    inst_dir = tmp_path / name
    inst_dir.mkdir(parents=True, exist_ok=True)
    db_path = inst_dir / "anthill.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    sess = factory()
    org = Organization(id=org_id, name=f"{name} Org", slug=f"{name.lower()}-org")
    sess.add(org)
    sess.commit()
    sess.refresh(org)
    user = User(
        org_id=org.id,
        email=email,
        display_name=name,
        hashed_password="x",
        role="admin",
        active=True,
    )
    sess.add(user)
    sess.add(OrgSettings(org_id=org.id, ollama_model=model))
    sess.commit()
    sess.refresh(user)
    uid, oid = int(user.id), int(org.id)
    sess.close()

    ws_root = inst_dir / "personal-wiki"
    ws = Workspace(ws_root, scope="personal")
    ws.init()

    templates_dir = Path(node_routes.__file__).resolve().parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    app = FastAPI()

    def _get_db() -> Session:
        return factory()

    def _require_user() -> dict[str, Any]:
        return {"sub": str(uid), "org": oid, "role": "admin"}

    app.include_router(
        node_routes.build_node_router(
            get_db=_get_db, require_user=_require_user, templates=templates
        )
    )
    return {
        "name": name,
        "app": app,
        "client": TestClient(app),
        "factory": factory,
        "uid": uid,
        "oid": oid,
        "ws": ws,
        "db_path": db_path,
    }


class _MultiAppTransport(httpx.BaseTransport):
    """Routes httpx calls to in-process TestClients by base URL (no sockets needed)."""

    def __init__(self, targets: dict[str, TestClient]) -> None:
        self.targets = {k.rstrip("/"): v for k, v in targets.items()}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        origin = f"{request.url.scheme}://{request.url.host}"
        if request.url.port:
            origin += f":{request.url.port}"
        tc = self.targets.get(origin)
        if tc is None:
            raise httpx.ConnectError(f"Unknown test peer origin: {origin}")
        resp = tc.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
            request=request,
        )


# -- Rule 1 & 5: Signed chained records, membership, and detached erasure ------


@pytest.mark.privacy_invariant
def test_rule1_and_5_signed_chain_membership_and_detached_erasure():
    admin_root = node_id.generate_ed25519_keypair()
    admin_dev = node_id.generate_ed25519_keypair()
    admin_x = node_id.generate_x25519_keypair()
    admin_bind = node_id.sign_device_binding(
        admin_root.private_b64,
        admin_root.public_b64,
        admin_dev.public_b64,
        admin_x.public_b64,
        "mac-a",
    )
    assert node_id.verify_device_binding(
        admin_root.public_b64, admin_dev.public_b64, admin_x.public_b64, "mac-a", admin_bind
    )

    member_root = node_id.generate_ed25519_keypair()
    member_dev = node_id.generate_ed25519_keypair()
    outsider_root = node_id.generate_ed25519_keypair()
    outsider_dev = node_id.generate_ed25519_keypair()

    bindings = {
        admin_dev.public_b64: admin_root.public_b64,
        member_dev.public_b64: member_root.public_b64,
        outsider_dev.public_b64: outsider_root.public_b64,
    }

    r1 = node_log.create_record(
        record_type="Membership",
        scope="private-group",
        hill_id="pilot",
        author_root_key=admin_root.public_b64,
        device_key=admin_dev.public_b64,
        device_priv_b64=admin_dev.private_b64,
        seq=1,
        prev_hash=node_log.GENESIS_PREV_HASH,
        payload={"members": [admin_root.public_b64, member_root.public_b64], "revoked_devices": []},
    )
    body = "# Solar Tariff\n\nSeasonal step recovers battery wear."
    r2 = node_log.create_record(
        record_type="PageChange",
        scope="private-group",
        hill_id="pilot",
        author_root_key=admin_root.public_b64,
        device_key=admin_dev.public_b64,
        device_priv_b64=admin_dev.private_b64,
        seq=2,
        prev_hash=r1["record_hash"],
        payload={"slug": "solar-tariff", "title": "Solar Tariff"},
        content_body=body,
    )
    r3 = node_log.create_record(
        record_type="Deletion",
        scope="private-group",
        hill_id="pilot",
        author_root_key=admin_root.public_b64,
        device_key=admin_dev.public_b64,
        device_priv_b64=admin_dev.private_b64,
        seq=3,
        prev_hash=r2["record_hash"],
        payload={"erased_record_hash": r2["record_hash"]},
    )

    members, revoked, latest_seq = node_log.resolve_membership(
        [r1, r2, r3], admin_root.public_b64, device_bindings=bindings
    )
    assert members == {admin_root.public_b64, member_root.public_b64}
    assert revoked == set() and latest_seq == 1

    # Chain stays valid even after r2's detached body blob is erased!
    ok, reason = node_log.verify_chain(
        [r1, r2, r3], active_members=members, revoked_devices=revoked, device_bindings=bindings
    )
    assert ok, reason
    assert r2["content_ref"] and r2["content_hash"]
    assert node_log.verify_content_blob(body, r2["content_hash"])

    # Unsigned record refused (Rule 1 & 5).
    unsigned = dict(r2)
    unsigned["signature"] = ""
    ok, reason = node_log.verify_record(unsigned, active_members=members, device_bindings=bindings)
    assert not ok and "unsigned" in reason.lower()

    # Tampered payload or hash fails verification.
    tampered = dict(r2)
    tampered["payload"] = {"slug": "solar-tariff", "title": "Tampered Title"}
    ok, reason = node_log.verify_record(tampered, active_members=members, device_bindings=bindings)
    assert not ok and "mismatch" in reason.lower()

    # Non-member record ignored/rejected.
    outsider_rec = node_log.create_record(
        record_type="PageChange",
        scope="private-group",
        hill_id="pilot",
        author_root_key=outsider_root.public_b64,
        device_key=outsider_dev.public_b64,
        device_priv_b64=outsider_dev.private_b64,
        seq=1,
        prev_hash=node_log.GENESIS_PREV_HASH,
        payload={"slug": "evil", "title": "Poison"},
    )
    ok, reason = node_log.verify_record(
        outsider_rec, active_members=members, device_bindings=bindings
    )
    assert not ok and "membership" in reason.lower()


# -- Rule 7: Recovery kit, device revocation, peer removal, open export --------


@pytest.mark.privacy_invariant
def test_rule7_recovery_revocation_and_open_export(tmp_path: Path):
    root = node_id.generate_ed25519_keypair()
    dev = node_id.generate_ed25519_keypair()
    x_kp = node_id.generate_x25519_keypair()

    # Private keys encrypted at rest via AES-256-GCM (never plaintext).
    enc = node_id.encrypt_secret(root.private_b64)
    assert root.private_b64 not in enc
    assert node_id.decrypt_secret(enc) == root.private_b64

    # 33-word recovery kit restores exact root keypair; tampered word fails checksum.
    kit = node_id.export_recovery_kit(root.private_b64)
    assert len(kit.split()) == 33
    assert node_id.verify_recovery_kit(kit, root.public_b64)
    restored = node_id.restore_root_from_recovery_kit(kit)
    assert restored == root
    bad_words = kit.split()
    bad_words[0] = "olive" if bad_words[0] != "olive" else "acorn"
    assert not node_id.verify_recovery_kit(" ".join(bad_words), root.public_b64)

    # Device revocation rejects subsequent records from that device key.
    rev_sig = node_id.sign_device_revocation(
        root.private_b64, root.public_b64, dev.public_b64, "2026-11-23T12:00:00+00:00"
    )
    assert node_id.verify_device_revocation(
        root.public_b64, dev.public_b64, "2026-11-23T12:00:00+00:00", rev_sig
    )
    rec = node_log.create_record(
        record_type="PageChange",
        scope="private-group",
        hill_id="pilot",
        author_root_key=root.public_b64,
        device_key=dev.public_b64,
        device_priv_b64=dev.private_b64,
        seq=1,
        prev_hash=node_log.GENESIS_PREV_HASH,
        payload={"slug": "after-revoke"},
    )
    ok, reason = node_log.verify_record(rec, revoked_devices={dev.public_b64})
    assert not ok and "revoked" in reason.lower()

    # Open-format export contains OKGF wiki + signed JSONL and zero private keys.
    ws = Workspace(tmp_path / "wiki", scope="personal")
    ws.init()
    ws.write_page("solar-notes", "# Solar Notes\n\nDry season capacity notes.", review="approved")
    tgz = node_id.build_open_export_tgz(
        {"personal": ws},
        [rec],
        {
            "root_pub_key": root.public_b64,
            "x25519_pub_key": x_kp.public_b64,
            "root_priv_key": "MUST_BE_STRIPPED",
        },
    )
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
        names = set(tar.getnames())
        assert "identity-public.json" in names
        assert "records.jsonl" in names
        assert "wiki/personal/solar-notes.md" in names
        ident_txt = tar.extractfile("identity-public.json").read().decode("utf-8")
        assert "MUST_BE_STRIPPED" not in ident_txt
        assert root.private_b64 not in ident_txt
        jsonl = tar.extractfile("records.jsonl").read().decode("utf-8").strip().splitlines()
        assert len(jsonl) == 1 and json.loads(jsonl[0])["record_hash"] == rec["record_hash"]


# -- Rule 3 & Leak Test: Canary secret in un-lent/locked area never leaks ------


@pytest.mark.privacy_invariant
def test_rule3_canary_secret_never_leaks_and_pii_scrubbed(tmp_path: Path):
    ws = Workspace(tmp_path / "wiki", scope="personal")
    ws.init()

    # 1. Lent area page (area:energy).
    energy_page = okf.OkfPage(
        type="Concept",
        title="Kenyan Solar Co-op Notes",
        tags=["area:energy"],
        body="# Kenyan Solar Co-op Notes\n\nOutput drops 30% during April-May long rains.",
        scope="personal",
        review="approved",
    )
    (ws.wiki / "solar-coop.md").write_text(okf.to_okf(energy_page), encoding="utf-8")
    ws.write_page("solar-coop", energy_page.body, review="approved")
    # Preserve area:energy tag on the OKGF page.
    parsed = okf.parse_okf((ws.wiki / "solar-coop.md").read_text(encoding="utf-8"))
    parsed.tags = ["area:energy"]
    (ws.wiki / "solar-coop.md").write_text(okf.to_okf(parsed), encoding="utf-8")

    # 2. Un-lent personal notes page with canary secret.
    canary_unlent = "CANARY-UNLENT-SECRET-7741"
    ws.write_page(
        "personal-diary", f"# Personal Diary\n\nSecret: {canary_unlent}", review="approved"
    )

    # 3. Locked sensitive page with canary secret (cannot be lent even if requested).
    canary_locked = "CANARY-HEALTH-LOCKED-9912"
    ws.write_page(
        "private-health", f"# Health Notes\n\nMedical note: {canary_locked}", review="approved"
    )

    excerpts = node_lending.select_lent_excerpts(
        [("personal", ws, "")],
        allowed_areas={"area:energy", node_lending.LOCKED_AREA_ID},  # even trying locked fails
    )
    assert len(excerpts) == 1
    assert excerpts[0]["area_id"] == "area:energy"
    combined_lent = json.dumps(excerpts)
    assert canary_unlent not in combined_lent
    assert canary_locked not in combined_lent

    # Run 0-tool peer counter-check and verify neither canary enters prompts or output,
    # and structured PII (email + API key) is scrubbed before leaving the node.
    backend = _StubPeerBackend(
        "qwen2.5:3b",
        {
            "suggestions": [
                {
                    "kind": "missing",
                    "issue": "Seasonal rains drop",
                    "wording": "Add a dry-season step; contact ops@example.com with sk-1234567890abcdef.",
                    "reason": "Long rains drop solar yield by 30%.",
                    "sources": ["Kenyan Solar Co-op Notes"],
                }
            ]
        },
    )
    res = node_consult.peer_counter_check(
        backend,
        question="What tariff works for a 40-household solar co-op?",
        draft="Net billing with a flat service fee.",
        lent_sources=excerpts,
        model_label="qwen2.5:3b",
    )
    assert res["tools_exposed"] == 0
    assert backend.chat_calls == 2  # Step 1 independent + Step 2 critique
    all_prompt_text = json.dumps(backend.prompts)
    assert canary_unlent not in all_prompt_text
    assert canary_locked not in all_prompt_text
    assert "ops@example.com" not in res["suggestions"][0]["wording"]
    assert "sk-1234567890abcdef" not in res["suggestions"][0]["wording"]


# -- Rule 4: Inbound Review Gate (Proposed, Contested, Quarantined) ------------


@pytest.mark.knowledge_invariant
def test_rule4_inbound_wiki_proposed_contested_and_quarantined(tmp_path: Path):
    ws = Workspace(tmp_path / "wiki", scope="personal")
    ws.init()
    ws.write_page("tariff-guide", "# Tariff Guide\n\nOriginal local guidance.", review="approved")

    dev = node_id.generate_ed25519_keypair()
    root = node_id.generate_ed25519_keypair()

    # 1. New page from private-group peer -> Proposed (local wiki untouched).
    rec_new = node_log.create_record(
        record_type="PageChange",
        scope="private-group",
        hill_id="pilot",
        author_root_key=root.public_b64,
        device_key=dev.public_b64,
        device_priv_b64=dev.private_b64,
        seq=1,
        prev_hash=node_log.GENESIS_PREV_HASH,
        payload={"slug": "rain-yield", "title": "Rain Yield"},
        content_body="# Rain Yield\n\nYield drops 30% in April.",
    )
    p1 = node_wiki_bridge.evaluate_inbound_page_change(
        ws,
        record=rec_new,
        content="# Rain Yield\n\nYield drops 30% in April.",
        signer_name="Peer B",
    )
    assert p1.peer_state == "proposed"
    assert not (ws.wiki / "rain-yield.md").exists()  # Rule 4: NEVER writes directly!

    # 2. Conflicting edit to existing slug -> Contested (no auto-merge; local content untouched).
    rec_conflict = node_log.create_record(
        record_type="PageChange",
        scope="private-group",
        hill_id="pilot",
        author_root_key=root.public_b64,
        device_key=dev.public_b64,
        device_priv_b64=dev.private_b64,
        seq=2,
        prev_hash=rec_new["record_hash"],
        payload={"slug": "tariff-guide", "title": "Tariff Guide"},
        content_body="# Tariff Guide\n\nConflicting peer tariff guidance.",
    )
    p2 = node_wiki_bridge.evaluate_inbound_page_change(
        ws,
        record=rec_conflict,
        content="# Tariff Guide\n\nConflicting peer tariff guidance.",
        signer_name="Peer B",
    )
    assert p2.peer_state == "contested"
    assert "Original local guidance." in p2.local_content
    assert "Original local guidance." in (ws.wiki / "tariff-guide.md").read_text(encoding="utf-8")

    # 3. Low-trust Open Hill page -> Quarantined and NOT retrievable by AI.
    rec_open = node_log.create_record(
        record_type="PageChange",
        scope="open",
        hill_id="open-hill",
        author_root_key=root.public_b64,
        device_key=dev.public_b64,
        device_priv_b64=dev.private_b64,
        seq=1,
        prev_hash=node_log.GENESIS_PREV_HASH,
        payload={"slug": "untrusted-claim", "title": "Untrusted Claim"},
        content_body="# Untrusted Claim\n\nDubious open-hill claim.",
    )
    p3 = node_wiki_bridge.evaluate_inbound_page_change(
        ws,
        record=rec_open,
        content="# Untrusted Claim\n\nDubious open-hill claim.",
        signer_name="Unknown",
        signer_karma=0,
        open_trust_threshold=10,
    )
    assert p3.peer_state == "quarantined"
    retrievable_stems = {p.stem for p in ws.retrievable_pages()}
    assert "untrusted-claim" not in retrievable_stems

    # Accepting writes the page and updates local index.
    node_wiki_bridge.accept_peer_page(
        ws, slug=p1.slug, content=p1.content, sources=[f"node:{p1.record_hash}"]
    )
    assert (ws.wiki / "rain-yield.md").exists()
    assert "rain-yield.md" in ws.index_md.read_text(encoding="utf-8")


# -- Rule 12 & Two-Instance End-to-End Pilot Test ------------------------------


def test_two_instance_e2e_pilot_and_rule12_and_rule2(tmp_path: Path, monkeypatch):
    inst_a = _make_desktop_instance(tmp_path, name="Raul", email="raul@example.com", org_id=1)
    inst_b = _make_desktop_instance(
        tmp_path, name="Christoph", email="christoph@example.com", org_id=2
    )

    # Route workspace_for cleanly to each instance's isolated Workspace.
    ws_by_org = {inst_a["oid"]: inst_a["ws"], inst_b["oid"]: inst_b["ws"]}

    def _fake_workspace_for(scope="personal", *, user_id=None, team_id=None, org_id=None):
        if scope == "personal" and org_id in ws_by_org:
            return ws_by_org[org_id]
        empty = Workspace(tmp_path / f"empty-{scope}-{org_id}", scope=scope)
        empty.init()
        return empty

    monkeypatch.setattr(node_routes, "workspace_for", _fake_workspace_for)

    # Wire in-process peer HTTP transport between Instance A and Instance B.
    router_transport = _MultiAppTransport(
        {"http://peer-a.local": inst_a["client"], "http://peer-b.local": inst_b["client"]}
    )
    stub_b = _StubPeerBackend(
        "qwen2.5:3b",
        {
            "suggestions": [
                {
                    "kind": "missing",
                    "issue": "Dry-season rate",
                    "wording": "Add a dry-season step for April-May long rains.",
                    "reason": "Kenyan solar co-op output drops 30% during long rains.",
                    "sources": ["Solar Notes"],
                }
            ]
        },
    )
    node_routes.set_test_hooks(
        transport_resolver=lambda _url: router_transport,
        backend_factory=lambda _db, _oid, _uid: stub_b,
    )

    try:
        # 1. Rule 12: With Node pillar OFF (default), P2P routes reject requests with 403.
        assert (
            inst_a["client"]
            .post("/api/node/p2p/heads", json={"hill_id": "anthill-core-pilot"})
            .status_code
            == 403
        )
        assert (
            inst_b["client"]
            .post("/api/node/p2p/consult", json={"hill_id": "anthill-core-pilot"})
            .status_code
            == 403
        )

        # 2. Enable Node pillar on both instances and set their peer URLs.
        r = inst_a["client"].post(
            "/peers/toggle", data={"action": "enabled"}, follow_redirects=False
        )
        assert r.status_code == 303
        r = inst_b["client"].post(
            "/peers/toggle", data={"action": "enabled"}, follow_redirects=False
        )
        assert r.status_code == 303
        inst_a["client"].post(
            "/peers/toggle",
            data={"action": "endpoint", "public_endpoint": "http://peer-a.local"},
            follow_redirects=False,
        )
        inst_b["client"].post(
            "/peers/toggle",
            data={"action": "endpoint", "public_endpoint": "http://peer-b.local"},
            follow_redirects=False,
        )

        # 3. Pairing + SAS verification: A creates invite -> B joins -> both show IDENTICAL SAS -> confirm.
        with inst_a["factory"]() as db_a:
            hill_a = db_a.query(web_db.NodeHill).first()
            assert hill_a is not None
            pilot_hill_id = hill_a.hill_id

        inst_a["client"].post(
            "/peers/invites/create",
            data={"hill_id": pilot_hill_id, "endpoint_url": "http://peer-a.local"},
            follow_redirects=False,
        )
        with inst_a["factory"]() as db_a:
            inv_a = db_a.query(NodeInvite).filter(NodeInvite.role == "inviter").first()
            assert inv_a is not None and inv_a.token.startswith(node_pairing.PAIR_PREFIX)
            token = inv_a.token
            inv_a_id = inv_a.id

        inst_b["client"].post(
            "/peers/invites/join",
            data={"token": token, "my_endpoint_url": "http://peer-b.local"},
            follow_redirects=False,
        )
        with inst_a["factory"]() as db_a, inst_b["factory"]() as db_b:
            inv_a_after = db_a.query(NodeInvite).filter(NodeInvite.id == inv_a_id).first()
            inv_b = db_b.query(NodeInvite).filter(NodeInvite.role == "joiner").first()
            assert inv_a_after is not None and inv_b is not None
            assert inv_a_after.status == "awaiting_sas" and inv_b.status == "awaiting_sas"
            assert inv_a_after.sas_code == inv_b.sas_code
            assert len(inv_a_after.sas_code) == 9  # "NNN - NNN"
            # Before human confirmation, neither side has a verified peer!
            assert db_a.query(NodePeer).filter(NodePeer.verified.is_(True)).count() == 0
            assert db_b.query(NodePeer).filter(NodePeer.verified.is_(True)).count() == 0
            inv_b_id = inv_b.id

        # Both humans confirm matching SAS code.
        inst_a["client"].post(f"/peers/invites/{inv_a_id}/confirm", follow_redirects=False)
        inst_b["client"].post(f"/peers/invites/{inv_b_id}/confirm", follow_redirects=False)

        with inst_a["factory"]() as db_a, inst_b["factory"]() as db_b:
            peer_b_on_a = db_a.query(NodePeer).filter(NodePeer.verified.is_(True)).first()
            peer_a_on_b = db_b.query(NodePeer).filter(NodePeer.verified.is_(True)).first()
            assert peer_b_on_a is not None and peer_a_on_b is not None
            peer_b_id = peer_b_on_a.id

        # 4. Offline-first encrypted .anthill-bundle sync: A publishes a page -> exports -> B imports.
        inst_a["ws"].write_page(
            "bundle-spec",
            "# Bundle Spec\n\nEncrypted to recipients' X25519 keys.",
            review="approved",
        )
        inst_a["client"].post(
            "/peers/wiki/publish",
            data={"hill_id": pilot_hill_id, "slug": "bundle-spec"},
            follow_redirects=False,
        )
        exp_resp = inst_a["client"].post("/peers/bundle/export", data={"hill_id": pilot_hill_id})
        assert exp_resp.status_code == 200
        bundle_bytes = exp_resp.content
        assert b"Encrypted to recipients" not in bundle_bytes  # ciphertext only!

        imp_resp = inst_b["client"].post(
            "/peers/bundle/import",
            files={
                "bundle_file": ("pilot.anthill-bundle", bundle_bytes, "application/octet-stream")
            },
            follow_redirects=False,
        )
        assert imp_resp.status_code == 303
        # Rule 4: Page landed in B's WikiReview as Proposed, NOT directly in B's wiki!
        assert not (inst_b["ws"].wiki / "bundle-spec.md").exists()
        with inst_b["factory"]() as db_b:
            rev = db_b.query(WikiReview).filter(WikiReview.slug == "bundle-spec").first()
            assert rev is not None and rev.peer_state == "proposed"

        # 5. Live authenticated HTTP sync: B publishes a page, A pulls via direct peer HTTP.
        inst_b["ws"].write_page(
            "solar-notes",
            "# Solar Notes\n\nOutput drops 30% during April-May long rains.",
            review="approved",
        )
        # Tag B's solar-notes as area:energy and add a locked canary secret on B!
        parsed_b = okf.parse_okf((inst_b["ws"].wiki / "solar-notes.md").read_text(encoding="utf-8"))
        parsed_b.tags = ["area:energy"]
        (inst_b["ws"].wiki / "solar-notes.md").write_text(okf.to_okf(parsed_b), encoding="utf-8")
        inst_b["ws"].write_page(
            "private-canary",
            "# Private Canary\n\nCANARY-B-NEVER-LEAKS-5501",
            review="approved",
        )
        inst_b["client"].post(
            "/peers/wiki/publish",
            data={"hill_id": pilot_hill_id, "slug": "solar-notes"},
            follow_redirects=False,
        )
        sync_resp = inst_a["client"].post(f"/peers/{peer_b_id}/sync", follow_redirects=False)
        assert sync_resp.status_code == 303
        assert not (inst_a["ws"].wiki / "solar-notes.md").exists()
        with inst_a["factory"]() as db_a:
            rev_a = db_a.query(WikiReview).filter(WikiReview.slug == "solar-notes").first()
            assert rev_a is not None and rev_a.peer_state == "proposed"

        # 6. Counter-check consultation: B lends area:energy -> A consults with visible exit preview.
        inst_b["client"].post(
            "/peers/lending/toggle",
            data={"hill_id": pilot_hill_id, "area_id": "area:energy", "area_name": "Energy"},
            follow_redirects=False,
        )
        start_resp = inst_a["client"].post(
            "/peers/consult/start",
            data={
                "question": "Best tariff for a 40-household solar co-op?",
                "draft": "Net billing with a flat service fee.",
                "hill_id": pilot_hill_id,
            },
            follow_redirects=False,
        )
        assert start_resp.status_code == 303
        cid = int(start_resp.headers["location"].split("cid=")[1].split("&")[0])

        # Sending without confirming visible exit preview is refused (Rule 2).
        no_confirm = inst_a["client"].post(
            f"/peers/consult/{cid}/send",
            data={"confirm_egress": "no"},
            follow_redirects=False,
        )
        assert "err=" in no_confirm.headers["location"]

        # Confirming visible exit preview executes live authenticated peer consult.
        send_resp = inst_a["client"].post(
            f"/peers/consult/{cid}/send",
            data={"confirm_egress": "yes", "peer_ids": [str(peer_b_id)]},
            follow_redirects=False,
        )
        assert send_resp.status_code == 303
        assert stub_b.chat_calls == 2
        assert "CANARY-B-NEVER-LEAKS-5501" not in json.dumps(stub_b.prompts)

        # Rule 2: Verify zero-content egress ledger + Sovereignty QueryMetric recorded on A.
        with inst_a["factory"]() as db_a:
            ledger_rows = (
                db_a.query(NodeEgressLedger)
                .filter(NodeEgressLedger.direction == "outbound_brief")
                .all()
            )
            assert len(ledger_rows) == 1
            assert "40-household" not in ledger_rows[0].outcome
            usage_rows = db_a.query(NodeRecord).filter(NodeRecord.record_type == "Usage").all()
            assert usage_rows
            for ur in usage_rows:
                assert "40-household" not in ur.envelope_json
                assert "flat service fee" not in ur.envelope_json
            msum = web_metrics.summary(db_a, inst_a["oid"])
            assert msum["peer_egress"] == 1
            assert msum["in_perimeter_pct"] < 100

        # Accept suggestion -> writes signed Acceptance, awards +4 karma, updates draft.
        page_html = inst_a["client"].get(f"/peers/consult?cid={cid}").text
        assert "Dry-season rate" in page_html
        with inst_a["factory"]() as db_a:
            c_row = db_a.query(web_db.NodeConsultation).filter_by(id=cid).first()
            assert c_row is not None
            sug_id = json.loads(c_row.suggestions_json)[0]["id"]

        inst_a["client"].post(
            f"/peers/consult/{cid}/decide",
            data={"suggestion_id": sug_id, "decision": "accepted"},
            follow_redirects=False,
        )
        with inst_a["factory"]() as db_a:
            c_row = db_a.query(web_db.NodeConsultation).filter_by(id=cid).first()
            assert c_row is not None and "dry-season step" in c_row.draft_current.lower()
            peer_b_after = db_a.query(NodePeer).filter_by(id=peer_b_id).first()
            assert peer_b_after is not None and peer_b_after.karma == 4
            assert db_a.query(NodeRecord).filter_by(record_type="Acceptance").count() >= 1

        # Upgrade to Open Thread (Rule 11: human posts own answer) & propose to wiki.
        inst_a["client"].post(f"/peers/consult/{cid}/open-thread", follow_redirects=False)
        inst_a["client"].post(
            f"/peers/consult/{cid}/post",
            data={"kind": "own_answer", "body": "Year-one flat share keeps metering simple."},
            follow_redirects=False,
        )
        inst_a["client"].post(f"/peers/consult/{cid}/propose-wiki", follow_redirects=False)
        with inst_a["factory"]() as db_a:
            consult_rev = (
                db_a.query(WikiReview)
                .filter(WikiReview.diff_summary.like(f"%consultation #{cid}%"))
                .first()
            )
            assert consult_rev is not None and consult_rev.peer_state == "proposed"

        # 7. Rule 7: Removing peer B revokes access; subsequent P2P calls from B are rejected (403).
        inst_a["client"].post(f"/peers/{peer_b_id}/remove", follow_redirects=False)
        with inst_b["factory"]() as db_b:
            peer_a_row = db_b.query(NodePeer).filter(NodePeer.verified.is_(True)).first()
            assert peer_a_row is not None
            peer_a_id = peer_a_row.id
        revoked_sync = inst_b["client"].post(f"/peers/{peer_a_id}/sync", follow_redirects=False)
        assert "err=" in revoked_sync.headers["location"]
    finally:
        node_routes.set_test_hooks(transport_resolver=None, backend_factory=None)


# -- Two Different Locations Over the Internet (HTTPS Tunnel + One-Sided NAT) --


class _ProxiedInternetTransport(httpx.BaseTransport):
    """Simulates two desktops in different cities connected via an HTTPS tunnel/reverse proxy."""

    def __init__(self, targets: dict[str, TestClient]) -> None:
        self.targets = {k.rstrip("/"): v for k, v in targets.items()}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        origin = f"{request.url.scheme}://{request.url.host}"
        if request.url.port:
            origin += f":{request.url.port}"
        tc = self.targets.get(origin)
        if tc is None:
            raise httpx.ConnectError(f"Host unreachable over Internet: {origin}")
        proxied_headers = dict(request.headers)
        proxied_headers["X-Forwarded-Proto"] = "https"
        proxied_headers["X-Forwarded-Host"] = request.url.host
        proxied_headers["X-Forwarded-For"] = "203.0.113.42"
        resp = tc.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            content=request.content,
            headers=proxied_headers,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
            request=request,
        )


def test_cross_internet_tunnel_and_nat_peer_pilot(tmp_path: Path, monkeypatch):
    """Two desktops in different networks: Desktop A behind an HTTPS tunnel, Desktop B behind NAT.

    Verifies:
      - Ed25519 peer auth headers pass cleanly through HTTPS reverse proxies (X-Forwarded-*).
      - One-sided NAT works: B (no public URL) pairs live with A, syncs bidirectionally in one
        POST (pushing B's page to A AND pulling A's page to B), and consults A's AI live.
      - Rotating A's quick-tunnel URL via POST /peers/{id}/endpoint preserves pinned keys.
    """
    raul = _make_desktop_instance(
        tmp_path, name="Raul-Remote", email="raul@city-a.example", org_id=11
    )
    christoph = _make_desktop_instance(
        tmp_path, name="Christoph-NAT", email="christoph@city-b.example", org_id=12
    )
    ws_by_org = {raul["oid"]: raul["ws"], christoph["oid"]: christoph["ws"]}

    def _fake_workspace_for(scope="personal", *, user_id=None, team_id=None, org_id=None):
        if scope == "personal" and org_id in ws_by_org:
            return ws_by_org[org_id]
        empty = Workspace(tmp_path / f"net-empty-{scope}-{org_id}", scope=scope)
        empty.init()
        return empty

    monkeypatch.setattr(node_routes, "workspace_for", _fake_workspace_for)

    tunnel_v1 = "https://raul-city-a-v1.trycloudflare.com"
    tunnel_v2 = "https://raul-city-a-v2.trycloudflare.com"
    internet = _ProxiedInternetTransport({tunnel_v1: raul["client"], tunnel_v2: raul["client"]})
    stub_raul = _StubPeerBackend(
        "qwen2.5:3b",
        {
            "suggestions": [
                {
                    "kind": "add",
                    "issue": "Cross-network check",
                    "wording": "Verify the 6-digit SAS code on a separate call before trusting keys.",
                    "reason": "From Raul's lent remote pilot notes.",
                    "sources": ["Remote Pilot Notes"],
                }
            ]
        },
    )
    node_routes.set_test_hooks(
        transport_resolver=lambda _url: internet,
        backend_factory=lambda _db, _oid, _uid: stub_raul,
    )

    try:
        # Both enable Node pillar; only Raul exposes an HTTPS quick tunnel (Christoph stays behind NAT).
        raul["client"].post("/peers/toggle", data={"action": "enabled"}, follow_redirects=False)
        christoph["client"].post(
            "/peers/toggle", data={"action": "enabled"}, follow_redirects=False
        )
        raul["client"].post(
            "/peers/toggle",
            data={"action": "endpoint", "public_endpoint": tunnel_v1},
            follow_redirects=False,
        )

        with raul["factory"]() as db_r:
            hill_id = db_r.query(web_db.NodeHill).first().hill_id

        # 1. Raul creates invite with HTTPS tunnel URL; Christoph joins from behind NAT (empty URL).
        raul["client"].post(
            "/peers/invites/create",
            data={"hill_id": hill_id, "endpoint_url": tunnel_v1},
            follow_redirects=False,
        )
        with raul["factory"]() as db_r:
            inv_r = db_r.query(NodeInvite).filter(NodeInvite.role == "inviter").first()
            token, inv_r_id = inv_r.token, inv_r.id

        christoph["client"].post(
            "/peers/invites/join",
            data={"token": token, "my_endpoint_url": ""},
            follow_redirects=False,
        )
        with raul["factory"]() as db_r, christoph["factory"]() as db_c:
            r_inv = db_r.query(NodeInvite).filter(NodeInvite.id == inv_r_id).first()
            c_inv = db_c.query(NodeInvite).filter(NodeInvite.role == "joiner").first()
            assert r_inv.status == "awaiting_sas" and c_inv.status == "awaiting_sas"
            assert r_inv.sas_code == c_inv.sas_code
            inv_c_id = c_inv.id

        raul["client"].post(f"/peers/invites/{inv_r_id}/confirm", follow_redirects=False)
        christoph["client"].post(f"/peers/invites/{inv_c_id}/confirm", follow_redirects=False)

        with christoph["factory"]() as db_c:
            raul_peer = db_c.query(NodePeer).filter(NodePeer.verified.is_(True)).first()
            assert raul_peer is not None and raul_peer.endpoint_url == tunnel_v1
            raul_peer_id = raul_peer.id

        # 2. Both publish pages locally; Christoph initiates ONE bidirectional HTTPS sync over the tunnel.
        raul["ws"].write_page(
            "remote-pilot-notes",
            "# Remote Pilot Notes\n\nAlways verify SAS out of band over voice or Signal.",
            review="approved",
        )
        parsed_r = okf.parse_okf(
            (raul["ws"].wiki / "remote-pilot-notes.md").read_text(encoding="utf-8")
        )
        parsed_r.tags = ["area:pilot"]
        (raul["ws"].wiki / "remote-pilot-notes.md").write_text(
            okf.to_okf(parsed_r), encoding="utf-8"
        )
        raul["client"].post(
            "/peers/wiki/publish",
            data={"hill_id": hill_id, "slug": "remote-pilot-notes"},
            follow_redirects=False,
        )
        christoph["ws"].write_page(
            "field-report",
            "# Field Report\n\nOffline bundle handoff tested on Mac Mini.",
            review="approved",
        )
        christoph["client"].post(
            "/peers/wiki/publish",
            data={"hill_id": hill_id, "slug": "field-report"},
            follow_redirects=False,
        )

        # Single sync from Christoph (behind NAT) pushes field-report to Raul AND pulls remote-pilot-notes!
        sync_resp = christoph["client"].post(f"/peers/{raul_peer_id}/sync", follow_redirects=False)
        assert sync_resp.status_code == 303 and "err=" not in sync_resp.headers["location"]

        with raul["factory"]() as db_r, christoph["factory"]() as db_c:
            assert (
                db_r.query(WikiReview).filter_by(slug="field-report", peer_state="proposed").first()
            )
            assert (
                db_c.query(WikiReview)
                .filter_by(slug="remote-pilot-notes", peer_state="proposed")
                .first()
            )

        # 3. Rotate Raul's quick-tunnel URL (simulating cloudflared restart); pinned keys stay valid!
        upd = christoph["client"].post(
            f"/peers/{raul_peer_id}/endpoint",
            data={"endpoint_url": tunnel_v2},
            follow_redirects=False,
        )
        assert upd.status_code == 303 and "err=" not in upd.headers["location"]

        # 4. Raul lends area:pilot -> Christoph runs live counter-check over the rotated HTTPS tunnel.
        raul["client"].post(
            "/peers/lending/toggle",
            data={"hill_id": hill_id, "area_id": "area:pilot", "area_name": "Pilot"},
            follow_redirects=False,
        )
        start = christoph["client"].post(
            "/peers/consult/start",
            data={
                "question": "How should we verify keys across two locations?",
                "draft": "Paste the invite token and click confirm.",
                "hill_id": hill_id,
            },
            follow_redirects=False,
        )
        cid = int(start.headers["location"].split("cid=")[1].split("&")[0])
        send = christoph["client"].post(
            f"/peers/consult/{cid}/send",
            data={"confirm_egress": "yes", "peer_ids": [str(raul_peer_id)]},
            follow_redirects=False,
        )
        assert send.status_code == 303 and "err=" not in send.headers["location"]
        assert stub_raul.chat_calls == 2
        html = christoph["client"].get(f"/peers/consult?cid={cid}").text
        assert "Cross-network check" in html
    finally:
        node_routes.set_test_hooks(transport_resolver=None, backend_factory=None)
