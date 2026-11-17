# Threat model: a single Anthill node

Version 1, 17 November 2026. Lane `pillar:privacy`. Spec: [`docs/specs/privacy-threat-model.md`](specs/privacy-threat-model.md).

**How this was made.** It was written by reading the code on `main` at commit `eb1e963`. Nothing was run for
this document, and the tests named below were not re-run for it. It is not an audit and not a guarantee. A cell
that says **no test yet** means the control is in the code but no test pins it. A cell that says **intended;
not yet verified** means we state the rule we want and make no claim that it holds. Report a security problem
privately (see [`SECURITY.md`](../SECURITY.md)), not in a public issue.

## 1. What the promise is, and what this document does

The README promises that your prompts, documents and embeddings never leave your machines unless you
explicitly and visibly allow it. This document says, for each kind of install, who that promise is defended
against, what they can reach, what stops them, and where a test shows it. It also lists every way data can
leave a node, and what is out of scope. It describes **one node**. How nodes talk to each other is the Node
pillar's threat model (section 8).

## 2. What we protect

| Asset | Where it lives | How it is protected today |
|---|---|---|
| Saved credentials (provider keys, tokens, mailbox passwords) | `*_enc` columns in the database | AES-256-GCM. The key is in the macOS Keychain; on other systems it is in `secrets.env` or `.env`, a plain file |
| The key itself | macOS login Keychain, or a plain file elsewhere | A missing key over existing saved values is refused, never replaced |
| Chats, memories, audit log, training examples | the SQLite database | Plain content; the file is created readable by its owner only |
| Wiki pages, uploaded originals, files, semantic cache, page vectors | the data folder | Plain files. Not encrypted by Anthill. Use disk encryption (FileVault on a Mac) |
| Backups | `.anthillbak` files | Encrypted as a whole with a passphrase (12 characters or more); the file carries the key |
| Model files | the Ollama store, Hugging Face cache | Not checked after download (tracked in issue #209); the model catalogue that names them is signed |
| Passwords and sessions | the database | bcrypt; signed session tokens backed by server-side records that can be revoked |

## 3. The three install shapes

| Shape | How it runs | Who can reach it |
|---|---|---|
| **Solo** | The desktop app, or `start.sh` on one person's machine | The person at the machine. The server listens on loopback only |
| **Headless or LAN** | `anthill web --host 0.0.0.0`, a container, `anthill appliance install`, or a launcher behind a proxy | Anyone who can reach the port. A login is needed for the app's pages; requests from the machine itself count as "local" for a few install-wide controls |
| **Org** | The same web server, run always-on, with accounts, org admins and per-org wikis | Every member of the org, plus whatever can reach the port |

How a request is judged local (`anthill/web/install_scope.py`): the server must be bound to loopback and marked
local-only, the client address and the `Host` header must be loopback, and no forwarding header may be present.
"Local" does **not** skip the login. It only widens a few install-wide controls: creating a second account,
first-run bring-in, built-in skill and profile management, and backup export and restore. A proxy or tunnel on
the same machine that adds no forwarding header makes a remote request look local; the README says so and says
how to avoid it.

## 4. Who we defend against

| Id | Attacker | Wants |
|---|---|---|
| A1 | The author of a web page, an uploaded file, a document in a connector or an import | To make the model obey them, or to get text out through the answer |
| A2 | A poisoned model, or a tampered model catalogue | To run their weights on the user's machine, or to steer which model is trusted |
| A3 | Another user or process on the same computer | To read the key, the database or the wiki |
| A4 | Someone who gets a lost disk or a backup file | To read what is stored |
| A5 | Another machine on the same network | To reach the server and read or change data |
| A6 | Another member of the same org, or a member of another org | To read data that is not theirs, or to act as an admin |
| A7 | Someone who guesses or steals a password or a session | To act as the user |
| A8 | The outside services that legitimately receive data: a remote model endpoint, a provider, a search engine | Not an attacker. We limit and show what they receive |

## 5. Out of scope

- A person with administrator rights on the machine that runs the node, and anyone running the server in
  Org: **the person who runs the server can read what is stored on it.**
- A compromised operating system, a malicious browser extension, malware running as the same user, and hardware
  attacks.
- Peers, and the way nodes talk to each other (section 8).
- Legal duties (EU AI Act Article 50, product liability, the Cyber Resilience Act).
- A model that was already malicious when its publisher released it. A fingerprint cannot detect that.

## 6. What stops each attacker

"Tested by" names the test files that pin the control. Paths are relative to the repo root.

### 6.1 Solo

| Attacker | What they can reach | What stops them | Tested by |
|---|---|---|---|
| A1 web or file author | Text that enters a prompt through web results, uploads and retrieved pages | Untrusted-data fencing in the prompt; an injection detector that routes the turn to the capable model, checks the output and refuses a hijacked answer; web text that matches is dropped; chat output cannot load remote images or other app routes. A safety net, not a guarantee. Covers chat and web-grounded answers; other paths are not claimed | `tests/test_injection_hardening.py`, `tests/test_injection_output_check.py`, `tests/browser/test_chat_markdown.py` |
| A2 model or catalogue | The catalogue refresh and the model pull | The catalogue must verify against a pinned Sigstore signer, an older catalogue is refused, any unsafe row rejects the whole file, only validated fields are stored. The Ollama runtime download is SHA-256 pinned. The files of a pulled model are **not** fingerprinted yet (issue #209) | `tests/test_catalog_trust.py`, `tests/test_local_model_settings.py`, `tests/test_ollama_serving.py` |
| A3 other user or process | The database, the data folder, the key | The database file is created owner-only. On a Mac the key is in the login Keychain and is added without ever appearing on a command line; a process of the same user can read it while the Keychain is unlocked. Other files rely on the operating system's account permissions | `tests/test_keystore.py`, `tests/test_key_startup.py` |
| A4 lost disk or backup | Everything on the disk | Backups are one file encrypted with a passphrase, with a header that cannot be edited and a restore that checks the archive before touching anything. The rest of the data folder is **plain**: turn on disk encryption. Backups and copies made before the key moved still open every saved password | `tests/test_backup_protected.py`, `tests/test_backup.py` |
| A7 password or session | The account | bcrypt passwords; every request re-checks the live user, a server-side session record and an authentication generation, so a password change revokes sessions; sign-in attempts are throttled | `tests/test_jwt_live_revocation.py`, `tests/test_login_ratelimit.py` |
| A8 outside services | What the user lets out (section 7) | Scrubbing of listed identifiers before a call to a non-loopback endpoint; a per-turn local or remote badge; an `inference.call` audit row that never holds message content; consent before an escalation provider is used | `tests/test_openai_compat.py`, `tests/test_inference_call_audit.py`, `tests/test_scrub_pii.py` |

### 6.2 Headless or LAN

Everything in 6.1 applies. In addition:

| Attacker | What they can reach | What stops them | Tested by |
|---|---|---|---|
| A5 machine on the network | The web server, if it is bound beyond loopback | The app's pages need a login. Install-wide controls need the install owner, or a local request on a server that is not shared. Later sign-ups need the owner to open them. A proxy or tunnel that adds no forwarding header is a documented way to defeat the local check, so the README tells the operator how to avoid it | `tests/test_invite_only_signup.py`, `tests/test_install_owner_controls.py`, `tests/test_backup_install_owner.py`, `tests/test_start_sh.py` |
| A5 machine on the network | Account setup and account recovery on a reachable server | **Intended; not yet verified** | no test yet |
| A5 machine on the network | Browser-side hardening of the web pages | **Intended; not yet verified** | no test yet |

### 6.3 Org

Everything in 6.1 and 6.2 applies. In addition:

| Attacker | What they can reach | What stops them | Tested by |
|---|---|---|---|
| A6 another member | Another user's chats, files, personal wiki, tasks | Chats, files and personal wikis are keyed to the user; team wikis need membership; an id that belongs to someone else is refused | `tests/test_idor_owner_scope.py`, `tests/test_security_fixes.py`, `tests/test_task_access.py`, `tests/test_teams.py` |
| A6 member of another org | Another org's wiki and cache | Wikis and the semantic cache are per org | `tests/test_org_wiki_per_org.py`, `tests/test_cache_tenant_isolation.py` |
| A6 a member acting as an admin | Admin pages and install-wide controls | Roles (admin or member), Solo and Org routes kept apart, owner-only controls, the owner cannot be demoted or deactivated by another admin | `tests/test_solo_org_separation.py`, `tests/test_authz_account_and_settings.py`, `tests/test_install_owner_controls.py` |
| A8 the org's model endpoint | Org-plane chats | Org chats use the org's endpoint and leave out personal context; the endpoint address is entered by an org admin | `tests/test_plane_routing.py`, `tests/test_hosting_connect.py` |

## 7. Every way data leaves a node

Nothing is sent to the project for analytics. We found no telemetry code. There is no single offline switch:
each exit below is switched off on its own. "Default" is what a new install does.

| Exit | What leaves | Gate and default | Switch off | Tested by |
|---|---|---|---|---|
| Local model (Ollama on this machine) | nothing leaves | the default path | not applicable | `tests/test_plane_routing.py` |
| Remote model endpoint (org, solo-cloud, a GPU host) | the messages, grounded context and tool results of that turn | only when the account's compute setting is cloud or the org connects an endpoint; listed identifiers are scrubbed, images are not sent; every turn writes an audit row and shows a local or remote badge | choose local compute, or disconnect the endpoint | `tests/test_openai_compat.py`, `tests/test_plane_routing.py` |
| Escalation provider (Groq, Berget, Infercom) | the question text only, scrubbed | a provider and key must be attached; the automatic mode needs a consent that starts off; otherwise a click sends; a monthly cap | clear the provider, or turn consent off | `tests/test_chat_automated_escalation.py`, `tests/test_escalation_consent_revocation.py`, `tests/test_ask_provider_now.py` |
| Task and agent escalation | the goal and context, scrubbed | a per-task setting that starts off; for agents a human approval | untick the task setting, or set the cap to 0 | `tests/test_task_escalation.py`, `tests/test_approval_failclosed.py` |
| Web search | the query text, to DuckDuckGo or to Google Custom Search if keys are set | a per-chat switch; off by default for Solo, on by default for Org chats | the per-chat switch | `tests/test_web_search.py`, `tests/test_chat_web_search_default.py` |
| Page fetch and `fetch_url` | the page address, to the site (or to Firecrawl or Jina Reader if configured) | requests to internal addresses are refused and the resolved address is pinned; agent and task runs carry the `web` scope unless an admin removes it | the chat switch; agent scopes | `tests/test_security_fixes.py`, `tests/test_agent_identity.py`, `tests/test_agent_web_tools_toggle.py` |
| MCP servers (Slack, Notion, GitHub and others) | the arguments the model chooses | an admin must approve the server; the `mcp` scope is not in the default agent scopes; each tool call is audited by name | disable or remove the server | `tests/test_mcp.py`, `tests/test_agent_identity.py` |
| Slack, Discord, mail, web push, single sign-on | answers, notifications, addresses | each is switched on by an admin or by environment settings | its settings page, or unset the settings | `tests/test_slack_bot.py`, `tests/test_discord_bot.py`, `tests/test_mailer.py` |
| Remote access (Cloudflare tunnel) | the whole app is reachable from the internet | only the install owner can turn it on; off by default | settings, remote access | `tests/test_remote_access.py` |
| Inbound MCP server and agent-to-agent | wiki, cache and memory answers to approved consumers | all three expose switches start off; each call is logged | the expose switches | `tests/test_mcp_server.py`, `tests/test_a2a.py` |
| Ollama runtime download | the request reaches GitHub | first run on a desktop with no Ollama; the file is SHA-256 checked | install Ollama first | `tests/test_ollama_serving.py` |
| Model pull | the model tag reaches the Ollama registry | an admin click, audited; the embedding model is also pulled at server start | none for the embedding model | `tests/test_embedding_model_bootstrap.py` |
| Catalogue refresh | the request reaches `anthill.run`; no user data | an admin click; the signature must verify | do not click | `tests/test_catalog_trust.py` |
| Desktop updater | the request reaches GitHub | checked on every launch; the update is signature-verified | none in the app | no test yet |
| Training | scrubbed training examples to a GPU host; weights from Hugging Face | an admin turns remote training on; on-device training is on for Solo | the training switch | `tests/test_training_execution.py`, `tests/test_training_backends.py` |
| Exports and backups | a file to the user's browser | audited | not applicable | `tests/test_backup_protected.py` |
| Meilisearch (`MEILI_URL`) | page content and queries to that server | only if the setting is present | unset it | `tests/test_meili.py` |

## 8. Not claimed in this version

This document makes no claim about these. Each is covered, or will be, by its own work item:

- How nodes talk to each other and to an org server (the orchestrator and node agent, the shared index, wiki
  promotion). That is the Node pillar's threat model, and Privacy takes part in it.
- The weight files of a downloaded model (issue #209).
- Account setup and recovery on a server that others can reach, and browser-side hardening of the web pages
  (section 6.2).
- An independent test of the access and egress boundaries.

Unfixed security problems are not listed here. They go through [`SECURITY.md`](../SECURITY.md) first and are
described only after a fix ships.

## 9. Keeping this true

A change that adds a way for data to leave, a way in, or a new install shape must update sections 6 and 7 in
the same pull request. `tests/test_threat_model_doc.py` fails if this document names a file or a test that no
longer exists, or if a table row in section 6 has no test and does not say **no test yet** or **intended; not
yet verified**.
