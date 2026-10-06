# Open standards and tools in Anthill

A pinned inventory of the open standards, formats, models, and libraries Anthill uses, split into
**adopted** (in the code today) vs **planned** (agreed direction, not yet implemented). Keep this honest:
when something moves from planned to adopted, move its row up and cite the module.

Verify with: `grep -ri <name> anthill/` and the `pyproject.toml` dependency list.

## Open standards and protocols (interoperability)

| Standard | Status | Where / notes |
|---|---|---|
| **MCP (Model Context Protocol)** | ADOPTED | Official `mcp` SDK (`mcp` extra). Anthill is both an MCP **client** (connect Slack/Notion/GitHub/Linear) and a governed MCP **server** (exposes the org brain via the default-deny PDP). `anthill/web/mcp_store.py`, connectors. |
| **Standard `/v1` (OpenAI-compatible) API** | ADOPTED | The open de-facto endpoint format, deliberately framed as "standard /v1" with **no OpenAI dependency**. Used to connect any org model endpoint (RunPod / own cloud). |
| **OKF (Open Knowledge Format)** | ADOPTED | Google's base knowledge format; the wiki interchange layer (YAML frontmatter). `anthill/wiki/okf.py`. |
| **OKGF (Open Knowledge and Governance Format)** | ADOPTED | Anthill's strict **superset** of OKF: adds lifecycle, provenance, and trust for org knowledge. An OKGF bundle is a conformant OKF bundle. `docs/OKGF.md`. |
| **agentskills.io skill format** | ADOPTED | The open Agent Skills format for agent skills, **adopt-and-extended** with governance under `x-anthill-*` (the same OKF -> OKGF move): conformant `name`/`description` + folder, governance/routing as extensions, a name validator, and a backward-compatible reader. `anthill/agent/skills.py`, `docs/AGENT_SKILLS.md`. |
| **agent.md / AGENTS.md** | ADOPTED | A canonical repo `AGENTS.md` guides AI agents working on the codebase; `CLAUDE.md` defers to it. (This is the repo-contributor convention, distinct from the runtime agent skills above.) |

## Open model runtime and models

| Tool | Status | Notes |
|---|---|---|
| **Ollama** | ADOPTED | The local inference engine (the core). Open-source, runs the open-weight models in-perimeter. |
| **Open-weight models** | ADOPTED | qwen3 (general/reasoning), deepseek-r1 (reasoning), qwen2.5vl (vision), mistral-nemo (the eval judge). Nous's open-weight **Hermes** models are a candidate agent brain. |
| **MLX + mlx-lm** | ADOPTED | Apple-Silicon on-device LoRA train + serve (`train-mac` extra). |
| **PyTorch + PEFT + transformers** | ADOPTED | The NVIDIA-GPU training toolchain (for connected GPU hosts). |

## Open frameworks and libraries (the stack)

| Area | Libraries |
|---|---|
| Web / API | **FastAPI**, **Uvicorn**, Jinja2, python-multipart |
| Data / retrieval | **LanceDB** (vector database), **sentence-transformers** (embedder), SQLAlchemy, numpy |
| Desktop app | **Tauri** (Rust; `src-tauri/`) |
| Auth / crypto | Authlib (OAuth), PyJWT (JWT), passlib/bcrypt, cryptography |
| Privacy / PII | **built-in PII scrubber** (`anthill/hybrid/scrub.py`, no heavyweight dep): regex redaction of emails, cards, SSNs, phones, IPs, API keys, JWTs, IBANs, MAC addresses, and URLs before any egress - cloud escalation (`hybrid/escalate.py`), training-data export, and wiki-review flagging. Default on (`cloud_scrub_pii`). |
| Web search / research | **DDGS (DuckDuckGo Search)** zero-config fallback (Google CSE primary when keys set); **Firecrawl / Jina** page fetch; **Meilisearch** index |
| Document / output | **python-docx / python-pptx / openpyxl** (Office, `docs` extra), **reportlab** (PDF output), **MarkItDown** (local structured PDF-to-Markdown), **pypdf** (embedded-image fallback), **Pillow** (images/charts) |
| GPU / cloud provisioning | **RunPod**, **Modal** (serverless GPU: org model + training), **boto3** (AWS VPC GPU, `aws` extra) |
| Testing / dev | pytest, ruff, mypy, **Playwright** (browser/visual tests) |

## Planned / parked (not adopted, tracked so we do not claim them)

- **Presidio** (Microsoft's heavyweight PII library) - **deliberately NOT adopted.** PII scrubbing is
  handled by the lighter **built-in scrubber** listed above, chosen per the minimal-deps / sovereignty
  ethos; Presidio would pull in spaCy + models for marginal gain on structured identifiers. Revisit only
  if regex redaction proves insufficient (e.g. reliable free-text name detection is required).

---
*Rule of thumb: this file lists what is IN the repo. If it is only in a spec or a chat, it goes under
"planned" until the code lands, with the module cited when it does.*
