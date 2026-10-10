import os
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown

from . import profiles as profiles_mod
from .config import Config
from .inference.base import BackendError, build_backend
from .mesh_auth import mesh_headers
from .wiki import ask as ask_mod
from .wiki import ingest as ingest_mod
from .wiki import lint as lint_mod
from .wiki.agent import run_maintenance
from .wiki.workspace import Workspace

app = typer.Typer(add_completion=False, help="Local wiki-backed GPT for an organization.")
console = Console()


def _cli_cloud_consent(proposal) -> bool:
    """Per-use consent gate for cloud escalation (#252): the local answer looked weak and could be
    sent to a paid cloud model. Ask first; default No. `--cloud` only *enables* escalation - each
    actual send is still confirmed here, so nothing leaves the machine without an explicit yes."""
    who = proposal.provider_name or "the cloud provider"
    detail = (
        f"{proposal.pii_redacted} PII item(s) scrubbed first"
        if proposal.pii_redacted
        else "question only, PII scrubbed"
    )
    from .hybrid.scrub import scrub_coverage

    console.print(
        f"[yellow]Local answer looks weak.[/] It could be sent to [bold]{who}[/] ({detail})."
    )
    # Be explicit about what the scrub does and does NOT cover, so the structured-only default on a
    # base install is never silent (issue #540).
    console.print(f"  [dim]Scrubbed before sending: {scrub_coverage()}.[/]")
    return typer.confirm("  Escalate this question to the cloud?", default=False)


profiles_app = typer.Typer(
    add_completion=False,
    help="Manage isolated profiles - separate accounts on one install (RFC-0003).",
)
app.add_typer(profiles_app, name="profiles")
owner_app = typer.Typer(
    help="The install owner: the account that controls what belongs to the whole install.",
    no_args_is_help=True,
)
app.add_typer(owner_app, name="owner")


def _profiles_base() -> Path:
    """The install's base data dir, which holds the profile registry. Imported lazily so the CLI
    doesn't pull in the desktop launcher unless a profile command is actually used."""
    from .desktop import data_dir

    return data_dir()


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from .env into the environment (no override of
    already-set vars). Keeps persisted secrets working regardless of launcher."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


@app.callback()
def main(
    ctx: typer.Context,
    workspace: Path = typer.Option(
        Path(os.environ.get("ANTHILL_WORKSPACE", "workspace")),  # noqa: B008
        "--workspace",
        "-w",
        help="Path to the wiki workspace.",
    ),
):
    ctx.obj = {"workspace": Workspace(workspace), "config": Config.from_env()}


def _ws(ctx: typer.Context) -> Workspace:
    """Workspace for commands that need an existing wiki (everything but `init`)."""
    ws = ctx.obj["workspace"]
    if not ws.exists():
        console.print(f"[red]No wiki at {ws.root}.[/] Run `anthill -w {ws.root} init` first.")
        raise typer.Exit(code=2)
    return ws


@app.command()
def init(ctx: typer.Context):
    """Create a new wiki workspace (raw/ inbox/ wiki/ skills/ index.md log.md SCHEMA.md principles.md)."""
    ws = ctx.obj["workspace"]
    ws.init()
    console.print(f"[green]Initialized wiki at[/] {ws.root}")


@app.command()
def info(ctx: typer.Context):
    """Show the configured model backend and whether it's reachable."""
    cfg: Config = ctx.obj["config"]
    console.print(
        f"backend: [bold]{cfg.backend}[/]   model: [bold]{cfg.model}[/]   url: {cfg.base_url}"
    )
    try:
        problem = build_backend(cfg).health()
    except BackendError as e:
        problem = str(e)
    console.print(
        f"[yellow]model not ready:[/] {problem}" if problem else "[green]model reachable[/]"
    )


@app.command(name="privacy-pack")
def privacy_pack(
    status: bool = typer.Option(False, "--status", help="Only report whether it's installed."),
):
    """Install the optional privacy pack (Microsoft Presidio + a spaCy model) so cloud PII scrubbing
    also redacts free-text names and locations, not just structured identifiers (issue #540)."""
    from .hybrid import privacy_pack as pack
    from .hybrid.scrub import presidio_available, scrub_coverage

    if presidio_available():
        console.print(
            "[green]Privacy pack installed[/] - names and locations are redacted before egress."
        )
        raise typer.Exit(0)
    console.print(f"[yellow]Not installed.[/] Cloud scrub currently covers: {scrub_coverage()}.")
    if status:
        raise typer.Exit(0)
    if not pack.can_install():
        console.print(
            "[yellow]Can't install it in this build[/] (packaged app). Run Anthill from source or a "
            "server install to add name/location redaction."
        )
        raise typer.Exit(1)
    console.print(
        "Installing Microsoft Presidio + a small spaCy model (this takes a few minutes)..."
    )
    if pack.install():
        console.print(
            "[green]Done[/] - names and locations are now scrubbed before text leaves the machine."
        )
    else:
        console.print(
            "[red]Install did not complete[/] - the structured-identifier scrub still applies."
        )
        raise typer.Exit(1)


@app.command()
def ingest(
    ctx: typer.Context,
    source: Path = typer.Argument(..., exists=True, readable=True),
    web_enrich: bool = typer.Option(
        False, "--web", help="Enrich the wiki page with fresh web search results."
    ),
    confirm_large_pdf: bool = typer.Option(
        False,
        "--confirm-large-pdf",
        help="Process a PDF above automatic-work limits but below hard safety limits.",
    ),
):
    """Read a source file and file it into the wiki as a page.

    Supports .md .txt .pdf .docx .pptx .xlsx .html .htm .png .jpg .jpeg - images and
    scanned PDFs use the vision model.
    Use --web to augment with current web findings after ingestion.
    """
    ws = _ws(ctx)
    try:
        path = ingest_mod.ingest(
            ws,
            source,
            build_backend(ctx.obj["config"]),
            web_enrich=web_enrich,
            allow_large_pdf=confirm_large_pdf,
        )
    except (BackendError, ValueError, ImportError) as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]wrote[/] {path.relative_to(ws.root)}")


@app.command()
def ask(
    ctx: typer.Context,
    question: str,
    save: bool = typer.Option(False, "--save", help="File the answer back as a wiki page."),
    org_url: str = typer.Option(
        os.environ.get("ANTHILL_ORG_URL", ""),
        "--org-url",
        help="Orchestrator URL for org cache + inference routing.",
    ),
    web: bool = typer.Option(
        False, "--web", help="Fall back to web search if the wiki doesn't have the answer."
    ),
    image: list[Path] = typer.Option(
        [],
        "--image",
        help="Image file(s) to include with the question (vision model).",
    ),
    smart: bool = typer.Option(
        True,
        "--smart/--no-smart",
        help="Auto-select the best model for this task (default: on).",
    ),
    cloud: bool = typer.Option(
        False,
        "--cloud",
        help="Allow escalation to a paid cloud model when the local answer is weak "
        "(open-source-first). Configure with ANTHILL_CLOUD_* env vars.",
    ),
):
    """Answer a question using the wiki, cache, and optionally the web.

    Examples:
      anthill -w ./wiki ask "what database did we pick?"
      anthill -w ./wiki ask "explain this diagram" --image arch.png
      anthill -w ./wiki ask "latest EU AI Act news" --web
      anthill -w ./wiki ask "hard general question" --cloud
    """
    ws = _ws(ctx)
    cfg = ctx.obj["config"]

    images_b64: list[str] = []
    for img_path in image:
        if not img_path.exists():
            console.print(f"[red]image not found:[/] {img_path}")
            raise typer.Exit(code=1)
        import base64

        images_b64.append(base64.b64encode(img_path.read_bytes()).decode())

    from .routing import TaskRouter

    router = TaskRouter(ollama_url=cfg.base_url) if smart else None

    hybrid_policy = None
    if cloud:
        from .config import hybrid_policy_from_env
        from .hybrid import PROVIDERS

        hybrid_policy = hybrid_policy_from_env()
        hybrid_policy.enabled = True  # --cloud forces it on for this call
        provider = PROVIDERS.get(hybrid_policy.provider)
        if provider is None or not hybrid_policy.resolved_key(provider):
            console.print(
                f"[yellow]--cloud set but no API key for '{hybrid_policy.provider}'.[/] "
                "Set the provider's key env var (e.g. OPENROUTER_API_KEY). "
                "Continuing local-only."
            )

    try:
        answer, used, cache_hit = ask_mod.ask(
            ws,
            question,
            build_backend(cfg),
            save=save,
            org_url=org_url,
            web_search=web,
            images_b64=images_b64 or None,
            router=router,
            hybrid_policy=hybrid_policy,
            consent=_cli_cloud_consent,
        )
    except BackendError as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(code=1)

    console.print(Markdown(answer))
    if cache_hit:
        console.print("\n[dim]cache hit[/]")
    elif used:
        console.print(f"\n[dim]context: {', '.join(used)}[/]")


_CHAT_HELP = """[bold]Commands[/]
  [cyan]/save[/]     file the last answer back as a wiki page
  [cyan]/web[/]      toggle the web-search fallback
  [cyan]/context[/]  show which wiki pages grounded the last answer
  [cyan]/reset[/]    clear the conversation
  [cyan]/help[/]     show this help
  [cyan]/exit[/]     quit (also Ctrl-D)"""


@app.command()
def chat(
    ctx: typer.Context,
    web: bool = typer.Option(
        False, "--web", help="Allow a web-search fallback when the wiki is thin."
    ),
    smart: bool = typer.Option(
        True, "--smart/--no-smart", help="Auto-select the best model per turn (default: on)."
    ),
    cloud: bool = typer.Option(
        False, "--cloud", help="Allow escalation to a paid cloud model when a local answer is weak."
    ),
    org: bool = typer.Option(
        False, "--org", help="Chat against a running Anthill server (org plane) instead of locally."
    ),
    server: str = typer.Option(
        "", "--server", help="The Anthill server URL for --org (or set ANTHILL_ORG_URL)."
    ),
):
    """Interactive, multi-turn chat grounded in your wiki - a terminal version of the chat box.

    Each turn is answered from the local wiki + cache (optionally the web), and the conversation is
    remembered across turns (solo/local plane). Type a message and press enter; /help for commands.

    With --org, chat against a running Anthill server's shared model + wiki instead (the server keeps
    the history). Set the URL with --server or ANTHILL_ORG_URL; you'll be prompted to log in.

    Example:
      anthill -w ./wiki chat
      anthill chat --org --server https://anthill.acme.internal
    """
    if org:
        _org_chat(server, web=web)
        return

    ws = _ws(ctx)
    cfg = ctx.obj["config"]
    backend = build_backend(cfg)

    from .routing import TaskRouter

    router = TaskRouter(ollama_url=cfg.base_url) if smart else None

    hybrid_policy = None
    if cloud:
        from .config import hybrid_policy_from_env
        from .hybrid import PROVIDERS

        hybrid_policy = hybrid_policy_from_env()
        hybrid_policy.enabled = True  # --cloud forces it on for this session
        provider = PROVIDERS.get(hybrid_policy.provider)
        if provider is None or not hybrid_policy.resolved_key(provider):
            console.print(
                f"[yellow]--cloud set but no API key for '{hybrid_policy.provider}'.[/] "
                "Continuing local-only."
            )

    console.print(
        f"[bold]anthill chat[/] - backend [bold]{cfg.backend}[/], model [bold]{cfg.model}[/], "
        f"wiki [dim]{ws.root}[/]\n[dim]Ask anything. /help for commands, /exit or Ctrl-D to quit.[/]"
    )

    history: list[tuple[str, str]] = []
    last_q = last_answer = ""
    last_used: list[str] = []

    while True:
        try:
            line = console.input("\n[bold cyan]you[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break
        if not line:
            continue

        if line in ("/exit", "/quit", "/q"):
            break
        if line == "/help":
            console.print(_CHAT_HELP)
            continue
        if line == "/reset":
            history.clear()
            console.print("[dim]conversation cleared[/]")
            continue
        if line == "/web":
            web = not web
            console.print(f"[dim]web fallback {'on' if web else 'off'}[/]")
            continue
        if line == "/context":
            console.print(f"[dim]last context: {', '.join(last_used) or '(none)'}[/]")
            continue
        if line == "/save":
            if not last_answer:
                console.print("[yellow]nothing to save yet[/]")
                continue
            title = ask_mod.file_as_page(ws, last_q, last_answer, backend)
            console.print(f"[green]filed[/] as wiki page: [bold]{title}[/]")
            continue
        if line.startswith("/"):
            console.print(f"[yellow]unknown command:[/] {line}  [dim](try /help)[/]")
            continue

        answer = ""
        used: list[str] = []
        try:
            if web or cloud:
                # Web search / cloud escalation are not token-streamed - use the full ask() path.
                with console.status("[dim]thinking...[/]", spinner="dots"):
                    answer, used, _cache_hit = ask_mod.ask(
                        ws,
                        line,
                        backend,
                        web_search=web,
                        router=router,
                        hybrid_policy=hybrid_policy,
                        consent=_cli_cloud_consent,
                        history=history,
                    )
                console.print(Markdown(answer))
            else:
                # Stream the local, wiki-grounded answer token-by-token (first words appear at once).
                console.print("[bold green]anthill[/] ", end="")
                ctx: dict = {}
                parts: list[str] = []
                for chunk in ask_mod.ask_stream(
                    ws,
                    line,
                    backend,
                    router=router,
                    history=history,
                    on_context=lambda u, _ctx=ctx: _ctx.update(used=u),
                ):
                    print(chunk, end="", flush=True)
                    parts.append(chunk)
                print()  # newline after the streamed answer
                answer = "".join(parts)
                used = ctx.get("used", [])
        except BackendError as e:
            console.print(f"[red]error:[/] {e}")
            continue

        if used:
            console.print(f"[dim]context: {', '.join(used)}[/]")
        history.extend([("user", line), ("assistant", answer)])
        last_q, last_answer, last_used = line, answer, used


_ORG_CHAT_HELP = """[bold]Commands[/]
  [cyan]/web[/]      toggle the web-search fallback
  [cyan]/context[/]  show which wiki pages grounded the last answer
  [cyan]/help[/]     show this help
  [cyan]/exit[/]     quit (also Ctrl-D)"""


def _org_chat(server: str, *, web: bool) -> None:
    """REPL against a running Anthill server (the org plane). The server keeps the conversation
    history and owns the shared model + wiki, so the client only logs in, opens a conversation, and
    streams each turn. Slash-commands mirror the local chat (minus /save - the org wiki has its own
    review gate)."""
    server = (server or os.environ.get("ANTHILL_ORG_URL", "")).strip()
    if not server:
        console.print("[red]--org needs a server URL.[/] Pass --server URL or set ANTHILL_ORG_URL.")
        raise typer.Exit(code=2)

    from .web_client import OrgClient, OrgClientError

    email = os.environ.get("ANTHILL_EMAIL") or typer.prompt("Email")
    password = os.environ.get("ANTHILL_PASSWORD") or typer.prompt("Password", hide_input=True)
    client = OrgClient(server)
    try:
        client.login(email, password)
        conv_id = client.new_conversation(plane="org")
    except OrgClientError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    console.print(
        f"[bold]anthill chat[/] (org) - server [dim]{server}[/], conversation #{conv_id}\n"
        "[dim]Chatting against the org's shared model + wiki. /help for commands, /exit to quit.[/]"
    )
    last_used: list = []
    while True:
        try:
            line = console.input("\n[bold cyan]you[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "/q"):
            break
        if line == "/help":
            console.print(_ORG_CHAT_HELP)
            continue
        if line == "/web":
            web = not web
            console.print(f"[dim]web fallback {'on' if web else 'off'}[/]")
            continue
        if line == "/context":
            console.print(f"[dim]last context: {', '.join(last_used) or '(none)'}[/]")
            continue
        if line.startswith("/"):
            console.print(f"[yellow]unknown command:[/] {line}  [dim](try /help)[/]")
            continue

        console.print("[bold green]anthill[/] ", end="")
        used: list = []
        try:
            for kind, payload in client.stream(conv_id, line, web=web):
                if kind == "token":
                    print(payload, end="", flush=True)
                elif kind == "slugs":
                    used = payload
                elif kind == "error":
                    console.print(f"\n[red]error:[/] {payload}")
        except OrgClientError as e:
            console.print(f"\n[red]error:[/] {e}")
            continue
        print()  # newline after the streamed answer
        if used:
            console.print(f"[dim]context: {', '.join(used)}[/]")
        last_used = used


@app.command()
def lint(ctx: typer.Context):
    """Check the wiki for broken links, orphan pages, and empty pages."""
    ws = _ws(ctx)
    findings = lint_mod.lint(ws)
    if not findings:
        console.print("[green]clean[/] - no issues found")
        return
    from rich.markup import escape

    for f in findings:
        console.print(f"[yellow]{f.kind}[/] {f.page}: {escape(f.detail)}")


@app.command()
def generate(
    ctx: typer.Context,
    request: str = typer.Argument(
        ..., help="What to generate, e.g. 'a PDF summary of our database decisions'"
    ),
    output: Path = typer.Option(Path("output.pdf"), "--output", "-o", help="Output file path."),
):
    """Generate a document (PDF) on request.

    Example:
      anthill -w ./wiki generate "PDF summary of our architecture decisions" -o arch.pdf
    """
    ws = _ws(ctx)

    # Generate a document: ask the model, write as PDF
    cfg = ctx.obj["config"]
    backend = build_backend(cfg)

    # Build context from the wiki
    pages = ws.pages()[:6]
    context = "\n\n---\n\n".join(p.read_text() for p in pages) if pages else ""

    from .inference.base import Message

    messages = [
        Message(
            "system",
            "You produce well-structured Markdown documents. "
            "Use the wiki context provided to ground your response in "
            "the organisation's actual knowledge.",
        ),
        Message(
            "user",
            f"WIKI CONTEXT:\n{context}\n\nREQUEST: {request}\n\n"
            "Produce a complete, well-formatted Markdown document.",
        ),
    ]
    try:
        md_content = backend.chat(messages)
    except BackendError as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(code=1)

    suffix = output.suffix.lower()
    if suffix == ".pdf":
        from .multimodal.writer import markdown_to_pdf

        markdown_to_pdf(md_content, output, title=request[:80])
        console.print(f"[green]PDF saved to[/] {output}")
    elif suffix in {".md", ".txt", ""}:
        out = output.with_suffix(".md")
        out.write_text(md_content)
        console.print(f"[green]Markdown saved to[/] {out}")
    else:
        output.write_text(md_content)
        console.print(f"[green]saved to[/] {output}")


@app.command(name="models")
def list_models(
    ctx: typer.Context,
    pull: str = typer.Option("", "--pull", help="Pull a model by tag, e.g. qwen3:8b"),
):
    """Show available models and routing recommendations. Pull new models.

    Recommended pull order for 16 GB Apple Silicon:
      qwen3:8b               - general chat (5.2 GB)
      deepseek-r1:8b         - reasoning & code (5.2 GB)
      granite3.2-vision:2b   - vision / images, licence-clean default (2.4 GB)
    """
    cfg = ctx.obj["config"]

    if pull:
        import subprocess

        ollama_bin = os.path.expanduser("~/bin/ollama")
        console.print(f"[dim]pulling {pull}…[/]")
        result = subprocess.run([ollama_bin, "pull", pull], capture_output=False)
        if result.returncode == 0:
            console.print(f"[green]pulled[/] {pull}")
        else:
            console.print("[red]pull failed[/] - check tag and try again")
        return

    from .routing import CATALOGUE, TaskRouter

    router = TaskRouter(ollama_url=cfg.base_url)
    installed = router._installed_models()

    console.print("\n[bold]Model catalogue[/] (✓ = installed)\n")
    for spec in CATALOGUE:
        tick = (
            "✓"
            if (
                spec.tag in installed
                or any(t.split(":")[0] == spec.tag.split(":")[0] for t in installed)
            )
            else " "
        )
        cloud = " [yellow]⚠ CLOUD[/]" if spec.cloud else ""
        size = f"{spec.size_gb:.1f} GB" if spec.size_gb else "cloud"
        console.print(f"  [{tick}] [bold]{spec.tag}[/]{cloud}  {size}  - {spec.description}")

    console.print(
        "\n[dim]Pull with:[/] anthill models --pull qwen3:8b\n"
        "[dim]Or via the dashboard:[/] /models page\n"
    )


@app.command()
def promote(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Wiki page slug to promote to the org wiki."),
    org_url: str = typer.Option(
        ..., "--org-url", help="Orchestrator base URL, e.g. http://localhost:8080"
    ),
    node_id: str = typer.Option(
        os.environ.get("ANTHILL_NODE_ID", "local"),
        "--node-id",
        help="This node's ID (default: ANTHILL_NODE_ID env var or 'local').",
    ),
    reason: str = typer.Option("", "--reason", help="Short note for the audit log."),
):
    """Promote a personal wiki page to the org wiki (§7.4)."""
    ws = _ws(ctx)
    path = ws.wiki / f"{slug}.md"
    if not path.exists():
        console.print(f"[red]page '{slug}' not found in {ws.wiki}[/]")
        raise typer.Exit(code=2)

    content = path.read_text()
    try:
        resp = httpx.post(
            f"{org_url.rstrip('/')}/wiki/promote",
            json={"slug": slug, "content": content, "promoted_by": node_id, "reason": reason},
            headers=mesh_headers(),
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]cannot reach orchestrator at {org_url}[/]")
        raise typer.Exit(code=1)
    except httpx.HTTPStatusError as e:
        console.print(f"[red]orchestrator rejected promotion:[/] {e.response.text[:200]}")
        raise typer.Exit(code=1)

    console.print(f"[green]promoted[/] {slug} → org wiki")


@app.command(name="train-adapter")
def train_adapter_cmd(
    dataset: Path = typer.Option(..., "--dataset", help="PII-scrubbed gold dataset (jsonl)"),
    base: str = typer.Option(..., "--base", help="base model to fine-tune"),
    out: str = typer.Option(..., "--out", help="output directory for the LoRA adapter"),
    toolchain: str = typer.Option("", "--toolchain", help="peft | mlx (blank = auto-detect)"),
):
    """Fine-tune a LoRA adapter from a gold dataset. This is the entrypoint the trainer container
    runs (``docker run ... anthill train-adapter``); it can also be run directly on a GPU box."""
    from .training.trainer import train_adapter

    path = train_adapter(str(dataset), base, out_dir=out, toolchain=toolchain or None)
    console.print(f"[green]adapter →[/] {path}")


@app.command(name="export-training")
def export_training(
    output: Path = typer.Option(Path("training-data.jsonl"), "--output", "-o"),
    quality: str = typer.Option(
        "silver", "--quality", help="Minimum quality: bronze | silver | gold"
    ),
    db_path: str = typer.Option("data/anthill.db", "--db"),
):
    """Export Q&A training examples for LoRA fine-tuning.

    Quality tiers:
      bronze - everything (use for exploration)
      silver - reused by org index (recommended for training)
      gold   - admin-approved or thumbs-up (highest quality)
    """
    from sqlalchemy.orm import sessionmaker

    from .training.export import export_jsonl, export_stats
    from .web.db import create_tables, get_engine

    engine = get_engine(Path(db_path))
    create_tables(engine)
    db = sessionmaker(bind=engine)()

    stats = export_stats(db)
    console.print(
        f"[dim]total: {stats['total']}  gold: {stats['gold']}  "
        f"silver: {stats['silver']}  bronze: {stats['bronze']}[/]"
    )

    count = export_jsonl(db, output, min_quality=quality)
    console.print(f"[green]exported {count} examples →[/] {output}")
    if not stats["ready_to_train"]:
        console.print(f"[yellow]note:[/] {stats['note']}")


@app.command(name="train-reap")
def train_reap_cmd(
    db_path: str = typer.Option("data/anthill.db", "--db"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be terminated; change nothing."
    ),
    min_age: float | None = typer.Option(
        None,
        "--min-age",
        help="Override: terminate any anthill-train pod at least this many minutes old "
        "(use 0 to reap every leaked pod now). Default: the safe age that never hits a live run.",
    ),
    org: int | None = typer.Option(
        None, "--org", help="Only this org id (default: every org that trains on RunPod)."
    ),
):
    """Terminate leaked RunPod training pods - the safety net for a crash between a pod's launch and the
    trainer's own teardown.

    A healthy run tears its own pod down; this cleans up a pod left billing after a crashed or
    interrupted run. Run it before a live training run to clear leftovers, or any time to check. It never
    touches a healthy in-flight run's pod unless you pass an explicit ``--min-age``.
    """
    from sqlalchemy.orm import sessionmaker

    from .training.reaper import (
        configured_run_max_min,
        reap_orphaned_pods,
        reaper_client_for_cfg,
        sweep,
    )
    from .web.db import OrgSettings, TrainingRun, create_tables, get_engine

    engine = get_engine(Path(db_path))
    create_tables(engine)
    db = sessionmaker(bind=engine)()
    cfgs = db.query(OrgSettings)
    if org is not None:
        cfgs = cfgs.filter(OrgSettings.org_id == org)
    any_runpod = False
    for cfg in cfgs.all():
        client = reaper_client_for_cfg(cfg)  # None unless this org trains on RunPod with a key
        if client is None:
            continue
        any_runpod = True
        if min_age is not None:
            res = reap_orphaned_pods(client, max_age_minutes=min_age, dry_run=dry_run)
        else:
            active = (
                db.query(TrainingRun)
                .filter(TrainingRun.org_id == cfg.org_id, TrainingRun.status == "running")
                .count()
                > 0
            )
            res = sweep(
                client,
                active_run=active,
                run_max_min=configured_run_max_min(cfg),
                dry_run=dry_run,
            )
        console.print(f"[bold]org {cfg.org_id}[/]: {res.summary()}")
        for pod_id, name, age in res.reaped:
            verb = "would terminate" if dry_run else "terminated"
            console.print(f"  [green]{verb}[/] {name} ({age:.0f}m)  [dim]{pod_id}[/]")
        for pod_id, name, err in res.errors:
            console.print(f"  [red]error[/] {name or pod_id}: {err}")
    if not any_runpod:
        console.print(
            "[yellow]No org trains on RunPod (backend=endpoint, provider=runpod) with a key set - "
            "nothing to reap.[/]"
        )


# ── backup & restore ───────────────────────────────────────────────────────────


@app.command(name="backup")
def backup_cmd(
    output: Path = typer.Option(
        None, "--output", "-o", help="Archive path (default: <data>/backups/...)."
    ),
    db_path: str = typer.Option("data/anthill.db", "--db"),
    no_model: bool = typer.Option(False, "--no-model", help="Skip the fine-tuned model (smaller)."),
):
    """Back up everything: DB + wiki + files + skills + encryption keys (and the fine-tuned model)."""
    os.environ.setdefault("ANTHILL_DB", db_path)
    from sqlalchemy.orm import sessionmaker

    from . import backup as bk
    from .web.db import OrgSettings, create_tables, get_engine

    engine = get_engine(Path(db_path))
    create_tables(engine)
    db = sessionmaker(bind=engine)()
    names = (
        None
        if no_model
        else [
            f"org{c.org_id}-model-v{int(c.training_model_ver)}"
            for c in db.query(OrgSettings).filter(OrgSettings.training_model_ver > 0).all()
        ]
    )
    result = bk.create_backup(output, include_model=not no_model, model_names=names)
    console.print(
        f"[green]backup →[/] {result.path}  "
        f"({result.bytes / 1e6:.1f} MB; includes: {', '.join(result.includes)})"
    )
    if result.models:
        console.print(f"[dim]embedded model(s): {', '.join(result.models)}[/]")


@app.command(name="restore")
def restore_cmd(
    archive: Path = typer.Argument(..., help="Path to an Anthill backup .tar.gz"),
    db_path: str = typer.Option("data/anthill.db", "--db"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Restore a backup archive over this install. Stop the server first; a safety copy is taken."""
    os.environ.setdefault("ANTHILL_DB", db_path)
    from . import backup as bk

    if not yes:
        typer.confirm(
            f"Restore from {archive}? Overwrites current data (a safety copy is taken first).",
            abort=True,
        )
    result = bk.restore_backup(archive)
    console.print(f"[green]restored:[/] {', '.join(result.restored) or 'nothing'}")
    if result.models:
        console.print(f"[dim]model(s) restored: {', '.join(result.models)}[/]")
    if result.safety_backup:
        console.print(f"[dim]previous state saved to:[/] {result.safety_backup}")


# ── lifecycle (model upgrades) ─────────────────────────────────────────────────

lifecycle_app = typer.Typer(help="Model lifecycle: eval, renovate, warmup after a model change.")
app.add_typer(lifecycle_app, name="lifecycle")


@lifecycle_app.command("status")
def lifecycle_status(ctx: typer.Context):
    """Show which model wrote each wiki page and how many are stale."""
    ws = _ws(ctx)
    cfg = ctx.obj["config"]
    from .lifecycle.version import read_provenance, stale_pages

    pages = ws.pages()
    if not pages:
        console.print("[dim]wiki is empty[/]")
        return
    stale = stale_pages(ws.wiki, cfg.model)
    stale_paths = {path for path, _ in stale}
    console.print(
        f"current model: [bold]{cfg.model}[/]   pages: {len(pages)}   "
        f"stale: [yellow]{len(stale)}[/]"
    )
    for p in pages:
        prov = read_provenance(p.read_text())
        tag = prov["model"] if prov else "[red]untagged[/]"
        mark = "" if p not in stale_paths else "  [yellow]← stale[/]"
        console.print(f"  {p.stem}  [dim]{tag}[/]{mark}")


@lifecycle_app.command("renovate")
def lifecycle_renovate(
    ctx: typer.Context,
    model: str = typer.Option("", "--model", help="New model tag (default: configured model)."),
    all_pages: bool = typer.Option(
        False, "--all", help="Renovate every page, not just stale ones."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing."
    ),
    confirm_large_pdf: bool = typer.Option(
        False,
        "--confirm-large-pdf",
        help="Process PDFs above automatic-work limits but below hard safety limits.",
    ),
):
    """Regenerate stale wiki pages by re-ingesting their source through a new model."""
    ws = _ws(ctx)
    cfg = ctx.obj["config"]
    target_model = model or cfg.model
    from .lifecycle.renovate import renovate
    from .wiki.ingest import PdfConfirmationRequired, PdfProcessingLimitExceeded

    backend = build_backend(cfg)
    try:
        report = renovate(
            ws,
            backend,
            target_model,
            only_stale=not all_pages,
            dry_run=dry_run,
            allow_large_pdf=confirm_large_pdf,
        )
    except (PdfConfirmationRequired, PdfProcessingLimitExceeded) as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(code=1)
    console.print(("[dim](dry run)[/] " if dry_run else "") + report.summary())
    for slug in report.regenerated:
        console.print(f"  [green]✓[/] {slug}")
    for slug in report.skipped:
        console.print(f"  [yellow]skip[/] {slug} [dim](no source in raw/)[/]")


@lifecycle_app.command("eval")
def lifecycle_eval(
    ctx: typer.Context,
    model_a: str = typer.Argument(..., help="Current/baseline model tag."),
    model_b: str = typer.Argument(..., help="Candidate model tag."),
    sample: int = typer.Option(20, "--sample", help="Number of gold examples to test."),
    db_path: str = typer.Option("data/anthill.db", "--db"),
):
    """Compare two models on the org's gold training examples before switching."""
    cfg = ctx.obj["config"]
    from sqlalchemy.orm import sessionmaker

    from .lifecycle.evaluate import evaluate_models
    from .web.db import TrainingExample, create_tables, get_engine

    engine = get_engine(Path(db_path))
    create_tables(engine)
    db = sessionmaker(bind=engine)()
    rows = (
        db.query(TrainingExample)
        .filter(TrainingExample.quality.in_(["gold", "silver"]))
        .limit(sample)
        .all()
    )
    examples = [(r.instruction, r.output) for r in rows]
    if not examples:
        console.print(
            "[yellow]no gold/silver examples yet[/] - use the system first to collect them."
        )
        raise typer.Exit(code=1)
    backend = build_backend(cfg)
    result = evaluate_models(backend, model_a, model_b, examples, sample=sample)
    console.print(result.summary())


@lifecycle_app.command("warmup")
def lifecycle_warmup(
    ctx: typer.Context,
    model: str = typer.Option(
        "", "--model", help="Model to warm the cache with (default: configured)."
    ),
    top: int = typer.Option(20, "--top", help="Number of recent questions to re-answer."),
    db_path: str = typer.Option("data/anthill.db", "--db"),
):
    """Re-answer the most-asked questions so the cache reflects a new model."""
    ws = _ws(ctx)
    cfg = ctx.obj["config"]
    target = model or cfg.model
    # Pull recent chat questions from the DB as the warm-up set.
    from sqlalchemy.orm import sessionmaker

    from .web.db import ChatMessage, create_tables, get_engine

    engine = get_engine(Path(db_path))
    create_tables(engine)
    db = sessionmaker(bind=engine)()
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.role == "user")
        .order_by(ChatMessage.id.desc())
        .limit(top)
        .all()
    )
    questions = list({r.content for r in rows})
    if not questions:
        console.print("[yellow]no questions in history yet[/] - nothing to warm.")
        raise typer.Exit(code=0)
    from .lifecycle.warmup import warm_cache

    n = warm_cache(ws, build_backend(cfg), questions, new_model=target)
    console.print(f"[green]warmed[/] {n} cache entr{'y' if n == 1 else 'ies'} with {target}")


# ── agent ─────────────────────────────────────────────────────────────────────

agent_app = typer.Typer(help="Proactive maintenance agent (§6.3).")
app.add_typer(agent_app, name="agent")


def _run_agent_once(ctx: typer.Context, org_url: str) -> None:
    ws = _ws(ctx)
    try:
        report = run_maintenance(ws, build_backend(ctx.obj["config"]), org_url=org_url)
    except BackendError as e:
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(code=1)

    if report.ingested:
        console.print(f"[green]ingested:[/] {', '.join(report.ingested)}")
    else:
        console.print("[dim]inbox empty[/]")

    if report.lint_findings:
        for f in report.lint_findings:
            from rich.markup import escape

            console.print(f"[yellow]lint:[/] {escape(f)}")
    else:
        console.print("[dim]lint clean[/]")

    if report.proposed_promotions:
        console.print("[bold]proposed for promotion:[/]")
        for slug in report.proposed_promotions:
            console.print(
                f"  anthill -w {ws.root} promote {slug} --org-url {org_url or '<orchestrator>'}"
            )

    if report.reuse_flags:
        console.print("[bold]possible overlap (review before more work):[/]")
        for flag in report.reuse_flags:
            console.print(f"  {flag}")


@agent_app.command("run")
def agent_run(
    ctx: typer.Context,
    org_url: str = typer.Option(
        os.environ.get("ANTHILL_ORG_URL", ""),
        "--org-url",
        help="Orchestrator URL for promotion proposals and reuse checks.",
    ),
):
    """Run one maintenance pass: drain inbox, lint, propose promotions, flag reuse."""
    _run_agent_once(ctx, org_url)


@agent_app.command("start")
def agent_start(
    ctx: typer.Context,
    interval: int = typer.Option(300, "--interval", help="Seconds between passes."),
    org_url: str = typer.Option(
        os.environ.get("ANTHILL_ORG_URL", ""),
        "--org-url",
        help="Orchestrator URL.",
    ),
):
    """Run maintenance passes on a schedule until interrupted (Ctrl-C)."""
    import signal

    stop = False

    def _handle_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle_signal)
    console.print(f"[green]agent started[/] - interval {interval}s, Ctrl-C to stop")
    while not stop:
        console.print("\n[dim]── maintenance pass ──[/]")
        try:
            _run_agent_once(ctx, org_url)
        except SystemExit:
            pass
        for _ in range(interval):
            if stop:
                break
            import time

            time.sleep(1)
    console.print("[dim]agent stopped[/]")


@app.command()
def serve(
    ctx: typer.Context,
    port: int = typer.Option(9001, "--port", help="Port for the node agent HTTP server."),
    org_url: str = typer.Option(
        os.environ.get("ANTHILL_ORG_URL", ""),
        "--org-url",
        help="Orchestrator URL - enables heartbeats and central cache.",
    ),
    node_id: str = typer.Option(
        os.environ.get("ANTHILL_NODE_ID", "local"),
        "--node-id",
    ),
):
    """Start the node agent HTTP server (peer cache fetch + heartbeats)."""
    import uvicorn

    from .node_agent.app import app as node_app

    ws = _ws(ctx)
    # Inject config via env so the node agent app picks it up.
    os.environ["ANTHILL_WORKSPACE"] = str(ws.root)
    os.environ["ANTHILL_NODE_ID"] = node_id
    if org_url:
        os.environ["ANTHILL_ORG_URL"] = org_url
    base_url = f"http://localhost:{port}"
    os.environ["ANTHILL_NODE_BASE_URL"] = base_url
    # Bind loopback by default (the node agent's OpenAI drop-in + peer cache are for the local host).
    # A real mesh, where peers must reach this node, sets ANTHILL_NODE_HOST=0.0.0.0 - together with
    # ANTHILL_MESH_TOKEN so the now-exposed endpoints are authenticated.
    host = os.environ.get("ANTHILL_NODE_HOST", "127.0.0.1")
    console.print(f"[green]node agent[/] {node_id} at {base_url}, org: {org_url or '(standalone)'}")
    uvicorn.run(node_app, host=host, port=port, log_level="warning")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    db: str = typer.Option(
        "", "--db", help="SQLite database path (default: $ANTHILL_DB or data/anthill.db)."
    ),
    profile: str = typer.Option(
        None,
        "--profile",
        envvar="ANTHILL_PROFILE",
        help="Run a named profile - its own DB, workspace, wikis and secrets. See `anthill profiles`.",
    ),
):
    """Start the anthill web dashboard (login, users, wiki review, metrics, settings)."""
    import uvicorn

    _load_dotenv()  # persisted secrets work under any launch path (start.sh, launchd, manual)
    if profile:
        # Root every persistent-state var at the profile's isolated home *before* uvicorn imports
        # the app (which reads $ANTHILL_DB at import). force=True so the profile wins over any
        # ambient env. --db, if also given, still overrides below.
        from .desktop import activate_profile

        d = activate_profile(profile)
        console.print(f"[dim]profile:[/] {profile} → {d}")
    # Precedence: explicit --db > $ANTHILL_DB (set above when a profile is active) > default.
    # (Previously the --db default silently clobbered $ANTHILL_DB.)
    db = db or os.environ.get("ANTHILL_DB", "data/anthill.db")
    os.environ["ANTHILL_DB"] = db
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]anthill dashboard[/] → http://{host}:{port}")
    console.print("[dim]Open in browser. First run: /setup to create your org.[/]")
    os.environ["ANTHILL_HOST"] = (
        host  # the app reads where it listens (sign-up is by invitation beyond loopback)
    )
    uvicorn.run("anthill.web.app:app", host=host, port=port, log_level="warning", reload=False)


def _owner_session(db: str, profile: str | None):
    """A session on the install's database for the owner commands. The database is, in order: ``--db``,
    the database of ``--profile`` (looked up, not activated), ``$ANTHILL_DB``, ``$ANTHILL_HOME/anthill.db``,
    ``data/anthill.db``. It must already exist: these commands
    never create a database, so a wrong path cannot silently start an empty install."""
    _load_dotenv()
    profile_db = ""
    if profile:
        # Look the profile up in the registry; never activate it. Activating would remember it as the active
        # profile, seed skills and touch the environment, and an unknown name would fall back to the default
        # profile, so the command could act on the wrong install.
        base = _profiles_base()
        found = profiles_mod.get_profile(base, profile)
        if found is None:
            console.print(
                f"[red]No profile named {profile}.[/] Nothing was changed; see `anthill profiles list`."
            )
            raise typer.Exit(1)
        profile_db = str(profiles_mod.data_dir_of(base, found) / "anthill.db")
    home = os.environ.get("ANTHILL_HOME", "")
    default = str(Path(home) / "anthill.db") if home else "data/anthill.db"
    db_path = db or profile_db or os.environ.get("ANTHILL_DB", "") or default
    if not Path(db_path).is_file():
        console.print(
            f"[red]No database at {db_path}.[/] Nothing was changed; pass --db or --profile."
        )
        raise typer.Exit(1)
    os.environ["ANTHILL_DB"] = db_path
    from sqlalchemy.orm import sessionmaker

    from .web.db import create_tables, get_engine

    engine = get_engine(Path(db_path))
    create_tables(
        engine
    )  # the normal start-up upgrade of an existing database, run before the owner record is read
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


@owner_app.command("set")
def owner_set(
    email: str = typer.Argument(..., help="Email of an active admin to make the install owner."),
    db: str = typer.Option(
        "",
        "--db",
        help="SQLite database path (default: $ANTHILL_DB, then $ANTHILL_HOME/anthill.db).",
    ),
    profile: str = typer.Option(
        None, "--profile", envvar="ANTHILL_PROFILE", help="Profile to use."
    ),
):
    """Make an active admin the install owner. Run on the machine that hosts Anthill: it is the way back when
    the owner account is lost, and it needs no sign-in because it needs the host."""
    from .web import audit
    from .web.install_scope import recorded_owner_id, set_install_owner_by_email

    session = _owner_session(db, profile)
    try:
        before = recorded_owner_id(session)
        target = set_install_owner_by_email(session, email)
        if target is None:
            console.print(
                f"[red]No active admin with the email {email}.[/] The owner is unchanged."
            )
            raise typer.Exit(1)
        session.commit()
        audit.log(
            session,
            "install.owner_set_by_host",
            f"uid={target.id} previous={before}",
            org_id=target.org_id,
        )
        console.print(f"[green]{target.email} is now the install owner.[/]")
    finally:
        session.close()


@owner_app.command("show")
def owner_show(
    db: str = typer.Option(
        "",
        "--db",
        help="SQLite database path (default: $ANTHILL_DB, then $ANTHILL_HOME/anthill.db).",
    ),
    profile: str = typer.Option(
        None, "--profile", envvar="ANTHILL_PROFILE", help="Profile to use."
    ),
):
    """Show who the install owner is."""
    from .web.install_scope import install_owner, recorded_owner_id

    session = _owner_session(db, profile)
    try:
        owner = install_owner(session)
        if owner is not None:
            console.print(f"Install owner: {owner.email}")
        elif recorded_owner_id(session) is not None:
            console.print(
                "[yellow]The recorded owner is not an active admin. Run `anthill owner set EMAIL`.[/]"
            )
        else:
            console.print("[yellow]No install owner is recorded yet.[/]")
    finally:
        session.close()


@profiles_app.command("list")
def profiles_list():
    """List profiles on this install and mark the active one."""
    base = _profiles_base()
    reg = profiles_mod.migrate_or_init(base)
    active = reg.get("active")
    for p in reg["profiles"]:
        mark = "[green]*[/]" if p["id"] == active else " "
        console.print(
            f"{mark} [bold]{p['name']}[/]  ([dim]{p['id']}[/])  {profiles_mod.data_dir_of(base, p)}"
        )


@profiles_app.command("create")
def profiles_create(
    name: str = typer.Argument(..., help="Display name for the new profile."),
    colour: str = typer.Option(None, "--colour", help="Accent colour hex, e.g. #059669."),
):
    """Create a new isolated profile (its own DB, workspace, wikis and secrets)."""
    base = _profiles_base()
    try:
        prof, over_cap = profiles_mod.create_profile(base, name, colour)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from None
    console.print(
        f"[green]Created profile[/] [bold]{prof['name']}[/] "
        f"([dim]{prof['id']}[/]) at {profiles_mod.data_dir_of(base, prof)}"
    )
    if over_cap:
        console.print(
            f"[yellow]You now have more than {profiles_mod.SOFT_CAP} profiles. That is allowed - "
            "just note each one you open at the same time uses its own memory.[/]"
        )
    console.print(f"[dim]Run it with:[/] anthill web --profile {prof['id']}")


@profiles_app.command("show")
def profiles_show():
    """Show the active profile and its data directory."""
    base = _profiles_base()
    prof = profiles_mod.resolve(base)
    console.print(
        f"[bold]{prof['name']}[/] ([dim]{prof['id']}[/])\n{profiles_mod.data_dir_of(base, prof)}"
    )


# ── appliance (always-on Mac org backend) ──────────────────────────────────────

appliance_app = typer.Typer(
    help="Run this Mac as a headless, always-on org backend (LaunchAgent + power settings)."
)
app.add_typer(appliance_app, name="appliance")


def _print_appliance_status(st) -> None:
    gpu = f"[green]{st.gpu.backend}[/]" if st.gpu.accelerated else f"[yellow]{st.gpu.backend}[/]"
    console.print(f"GPU:      {gpu} - [dim]{st.gpu.detail}[/]")
    svc = "[green]running as a service[/]" if st.service_loaded else "[yellow]not a service[/]"
    console.print(f"Service:  {svc}  [dim]{st.service_label}[/]")
    console.print(f"Model:    {st.model or '[dim]none set[/]'}")
    console.print(f"LAN URL:  {st.lan_url or '[dim]no LAN address[/]'}")
    if st.tunnel_url:
        console.print(f"Off-LAN:  {st.tunnel_url}")


@appliance_app.command("status")
def appliance_status_cmd(port: int = typer.Option(8000, "--port")):
    """Show service / GPU (Metal vs CPU) / LAN URL / model for this Mac."""
    from .hosting import appliance

    _print_appliance_status(appliance.appliance_status(port=port))


@appliance_app.command("install")
def appliance_install_cmd(
    port: int = typer.Option(8000, "--port", help="Port the server listens on (all interfaces)."),
    model: str = typer.Option("", "--model", help="Ollama tag to serve (default: sized to RAM)."),
    no_pull: bool = typer.Option(False, "--no-pull", help="Skip pulling the model."),
    apply_power: bool = typer.Option(
        False, "--apply-power", help="Run the pmset always-on settings now (needs sudo)."
    ),
):
    """Install Anthill as an always-on LaunchAgent: serve on 0.0.0.0, restart on crash/boot."""
    from .hosting import appliance

    console.print("[dim]installing the Anthill appliance LaunchAgent…[/]")
    res = appliance.install_appliance(
        port=port, model=model, pull=not no_pull, apply_power=apply_power
    )
    console.print(f"[green]installed[/] {res.plist_path}")
    console.print(f"serving [bold]{res.model}[/] " + ("[green](pulled)[/]" if res.pulled else ""))
    console.print(f"LAN URL:  [bold]{res.lan_url}[/]  [dim](share this with your team)[/]")
    gpu = "[green]Metal (GPU)[/]" if res.gpu.accelerated else f"[yellow]{res.gpu.detail}[/]"
    console.print(f"GPU:      {gpu}")
    if res.power_applied:
        console.print("[green]power settings applied[/] (never sleep, restart after power loss)")
    console.print("\n[bold]Left to do by hand:[/]")
    for step in res.next_steps:
        console.print(f"  - {step}")


@appliance_app.command("uninstall")
def appliance_uninstall_cmd():
    """Stop and remove the appliance LaunchAgent (does not touch power settings)."""
    import os

    from .hosting import appliance

    path = appliance.launchagent_path()
    appliance.unload_launchagent(path)
    if os.path.exists(path):
        os.remove(path)
        console.print(f"[green]removed[/] {path}")
    else:
        console.print(f"[dim]nothing to remove at {path}[/]")
