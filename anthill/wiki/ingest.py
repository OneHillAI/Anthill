from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import shutil
import time
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import chain
from math import ceil
from pathlib import Path

from ..common.text import WIKILINK_RE, first_h1, normalize_wiki_page, slugify
from ..inference.base import InferenceBackend
from ..multimodal import read_file
from ..multimodal.reader import PDF_HARD_PROCESSING_SECONDS
from . import prompts
from .workspace import Workspace

_SOURCE_CHUNK_CHARS = 10_000
_INTERMEDIATE_SUMMARY_CHARS = 4_000
_VISION_BATCH_SIZE = 4
_PDF_AUTO_MAX_PAGES = 40
_PDF_AUTO_MAX_SOURCE_BYTES = 10 * 1024 * 1024
_PDF_AUTO_MAX_TEXT_CHUNKS = 12
_PDF_AUTO_MAX_IMAGES = 8
_PDF_AUTO_MAX_DECODED_IMAGE_BYTES = 32 * 1024 * 1024
_PDF_AUTO_MAX_MODEL_CALLS = 24
_PDF_HARD_MAX_MODEL_CALLS = 64
_PDF_HARD_MAX_TEXT_CHUNKS = 32
_PDF_PROCESSING_TIMEOUT_SECONDS = PDF_HARD_PROCESSING_SECONDS
_DEFAULT_FILESYSTEM_NAME_MAX_BYTES = 255


class PdfConfirmationRequired(ValueError):
    pass


class PdfProcessingLimitExceeded(ValueError):
    """A PDF could not be processed within the hard safety limits.

    `transient` carries the same meaning as on `PdfSafetyLimitExceeded`: the run or the
    host failed, not the file. Autonomous callers retry those and reject the rest.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class _NoSourceSummaries(ValueError):
    pass


@dataclass
class _ProcessingBudget:
    deadline: float | None
    pdf: bool = True
    calls_remaining: int | None = None

    @classmethod
    def start(cls, *, pdf: bool = True) -> _ProcessingBudget:
        deadline = time.monotonic() + _PDF_PROCESSING_TIMEOUT_SECONDS if pdf else None
        return cls(deadline, pdf=pdf)

    def check_time(self) -> None:
        if not self.pdf or self.deadline is None:
            return
        if time.monotonic() >= self.deadline:
            raise PdfProcessingLimitExceeded(
                "PDF processing exceeded the 10 minute hard safety limit.", transient=True
            )

    def remaining_seconds(self) -> float:
        self.check_time()
        if self.deadline is None:
            return _PDF_PROCESSING_TIMEOUT_SECONDS
        return self.deadline - time.monotonic()

    def chat(self, backend, messages, **kwargs) -> str:
        self.check_time()
        if self.calls_remaining is not None:
            if self.calls_remaining <= 0:
                raise PdfProcessingLimitExceeded(
                    "PDF processing exceeded its preflighted model-call budget."
                )
            self.calls_remaining -= 1
        original_timeout = getattr(backend, "timeout", None)
        if self.pdf and self.deadline is not None and isinstance(original_timeout, (int, float)):
            remaining = max(0.001, self.deadline - time.monotonic())
            backend.timeout = min(float(original_timeout), remaining)
        try:
            result = backend.chat(messages, **kwargs)
        except Exception as exc:
            if self.pdf and _is_timeout_error(exc):
                raise PdfProcessingLimitExceeded(
                    "PDF processing exceeded the 10 minute hard safety limit.", transient=True
                ) from exc
            raise
        finally:
            if (
                self.pdf
                and self.deadline is not None
                and isinstance(original_timeout, (int, float))
            ):
                backend.timeout = original_timeout
        self.check_time()
        return result


def _is_timeout_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    timeout_types = {
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "TimeoutException",
        "WriteTimeout",
    }
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or current.__class__.__name__ in timeout_types:
            return True
        current = current.__cause__ or current.__context__
    return False


def _neutralize_dangling_links(page_md: str, ws: Workspace, *, self_title: str = "") -> str:
    """Drop `[[wiki-link]]` brackets whose target has no existing page yet, keeping the link text;
    a link to a page that genuinely exists is left intact.

    The summariser writes a trailing "## Related" section of `[[links]]` it infers from the
    document's own content. On a fresh or sparse wiki those targets don't exist yet, so
    `outline_change`'s mechanical broken-links check flags every ingested page and
    `propose_wiki_write` never writes it - the model's own generated links strand its own first
    pages in review forever (issue: "document upload doesn't do anything"). This runs only for
    ingest's AI-generated links; a genuinely broken link a person types during a later edit still
    goes through `outline_change` untouched and is still caught.
    """
    existing = {p.stem for p in ws.pages()}
    self_slug = slugify(self_title) if self_title else ""

    def _is_dangling(target: str) -> bool:
        slug = slugify(target.strip())
        return not (slug in existing or (self_slug and slug == self_slug))

    def _sub(m: re.Match) -> str:
        target = m.group(1)
        return target if _is_dangling(target) else m.group(0)

    # A "## Related" section is, in practice, nothing but [[links]] (the summariser's own format;
    # normalize_wiki_page has already joined its lines into one). If every one of those is
    # dangling, the section becomes a heading over a bare, delinked list with nothing left in it -
    # drop the whole section rather than ship that. A section with any other content (real prose,
    # or at least one link whose target already exists) is left alone. "Nothing but links" is
    # judged by removing every [[link]] plus ordinary list punctuation and seeing if anything of
    # substance remains, rather than assuming a particular line/bullet shape.
    section_re = re.compile(r"(?im)^##\s*related\s*\n(?P<body>(?:(?!^##\s).)*)", re.DOTALL)

    def _section_sub(m: re.Match) -> str:
        body = m.group("body")
        links = WIKILINK_RE.findall(body)
        if not links:
            return m.group(0)  # no links here at all - not this fix's concern, leave it alone
        residual = WIKILINK_RE.sub("", body)
        residual = re.sub(r"[-*,\s]+", "", residual)
        if residual:
            return m.group(0)  # real prose mixed in alongside the links - keep the section
        if all(_is_dangling(t) for t in links):
            return ""  # nothing but dangling links - drop heading and body together
        return m.group(0)

    page_md = section_re.sub(_section_sub, page_md)
    return WIKILINK_RE.sub(_sub, page_md)


def ingest(
    ws: Workspace,
    source: Path,
    backend: InferenceBackend,
    *,
    web_enrich: bool = False,
    allow_large_pdf: bool = False,
    on_write=None,
    vision_max_accuracy: bool = False,
) -> Path | None:
    """Read a source file and file it as a wiki page.

    ``vision_max_accuracy`` opts the vision/scanned-PDF pass into the higher-accuracy Qwen3-VL model
    (the account's OrgSettings flag, passed through by the web upload handlers). Off keeps the
    sovereign, licence-clean, non-Chinese default (Granite / Mistral). The env override
    ``ANTHILL_VISION_MAX_ACCURACY`` still applies for callers with no OrgSettings (e.g. the CLI).

    Supports: .md .txt .pdf .docx .pptx .xlsx .html .htm .png .jpg .jpeg .gif .webp

    If web_enrich=True, the page is enriched with fresh web search results
    after the initial summary is written.

    If on_write is given, the finished (title, page_md) is handed to it instead of
    being written directly - so an autonomous caller can route the page through the
    review gate. Returns None in that case; otherwise the written path.
    """
    is_pdf = source.suffix.lower() == ".pdf"
    budget = _ProcessingBudget.start(pdf=is_pdf)
    raw_copy = _raw_source_path(ws, source)
    raw_created = is_pdf and source.resolve() != raw_copy.resolve() and not raw_copy.exists()
    if raw_created:
        preserve_raw_source(ws, source)
    try:
        fc = _read_source(source, budget=budget)
    except PdfProcessingLimitExceeded:
        raise
    except Exception:
        if raw_created:
            raw_copy.unlink(missing_ok=True)
        raise
    if not is_pdf and source.resolve() != raw_copy.resolve() and not raw_copy.exists():
        preserve_raw_source(ws, source)
    _preflight_pdf_work(fc, allow_large_pdf=allow_large_pdf, budget=budget)
    page_md, writer_model = _source_content_to_page(
        ws, source, fc, backend, budget=budget, vision_max_accuracy=vision_max_accuracy
    )

    page_md = normalize_wiki_page(page_md)

    if web_enrich:
        from ..search.web import enrich_wiki_page

        page_md = enrich_wiki_page(page_md, source.stem, backend=backend)
        page_md = normalize_wiki_page(page_md)
        writer_model = getattr(backend, "model", "") or writer_model

    if not first_h1(page_md):
        page_md = f"# {source.stem}\n\n{page_md}".strip()

    title = first_h1(page_md) or source.stem
    # Before tag_page appends its own trailing provenance comment below - that comment isn't
    # wiki-link content, but _neutralize_dangling_links's "## Related" handling matches to the end
    # of the page, and the comment's text would otherwise look like leftover prose in the section
    # and stop a fully-dangling section from being dropped.
    page_md = _neutralize_dangling_links(page_md, ws, self_title=title)

    # Record which model wrote this page so a later model upgrade can find and
    # renovate stale pages (see anthill.lifecycle).
    if writer_model:
        from ..lifecycle.version import tag_page

        pipeline_model = getattr(backend, "model", "") or writer_model
        page_md = tag_page(
            page_md,
            writer_model,
            source=raw_copy.name,
            pipeline_model=pipeline_model,
        )

    if on_write is not None:  # caller routes the page through a review gate
        on_write(title, page_md)
        return None
    path = ws.write_page(title, page_md)
    ws.rebuild_index()
    ws.append_log("ingest", title)
    return path


def source_to_page(
    ws: Workspace,
    source: Path,
    backend: InferenceBackend,
    *,
    model: str | None = None,
    allow_large_pdf: bool = False,
    vision_max_accuracy: bool = False,
) -> tuple[str, str]:
    is_pdf = source.suffix.lower() == ".pdf"
    budget = _ProcessingBudget.start(pdf=is_pdf)
    fc = _read_source(source, budget=budget)
    _preflight_pdf_work(fc, allow_large_pdf=allow_large_pdf, budget=budget)
    return _source_content_to_page(
        ws, source, fc, backend, model=model, budget=budget, vision_max_accuracy=vision_max_accuracy
    )


def _source_content_to_page(
    ws: Workspace,
    source: Path,
    fc,
    backend: InferenceBackend,
    *,
    model: str | None = None,
    budget: _ProcessingBudget,
    vision_max_accuracy: bool = False,
) -> tuple[str, str]:
    fc.source_name = source.name
    model_kwargs = _model_kwargs(backend, model)
    writer_model = model or (getattr(backend, "model", "") or "")

    if fc.mime_type == "application/pdf":
        needs_vision = bool(fc.pdf_preflight and fc.pdf_preflight.vision_required)
        if needs_vision:
            image_batches = fc.iter_image_batches(_VISION_BATCH_SIZE)
            if fc.text.startswith("[PDF:"):
                return _ingest_with_vision(
                    ws,
                    fc,
                    backend,
                    image_batches=image_batches,
                    budget=budget,
                    vision_max_accuracy=vision_max_accuracy,
                )
            vision_config = _vision_backend_config(
                fc, backend, budget=budget, vision_max_accuracy=vision_max_accuracy
            )
            text_summary = _summarize_text(
                ws,
                source.name,
                fc.text,
                backend,
                model_kwargs=model_kwargs,
                budget=budget,
            )
            vision_summary, _vision_model = _ingest_with_vision(
                ws,
                fc,
                backend,
                image_batches=image_batches,
                context_text=(
                    "[Attached PDF images are the only source for this visual pass. "
                    "Extract only facts visible in those images.]"
                ),
                budget=budget,
                vision_config=vision_config,
            )
            combined = _reduce_summaries(
                ws,
                source.name,
                iter((text_summary, vision_summary)),
                backend,
                model_kwargs=model_kwargs,
                budget=budget,
            )
            return _append_visual_facts(combined, vision_summary), writer_model
    elif fc.has_images:
        return _ingest_with_vision(
            ws, fc, backend, budget=budget, vision_max_accuracy=vision_max_accuracy
        )

    return (
        _summarize_text(
            ws,
            source.name,
            fc.text,
            backend,
            model_kwargs=model_kwargs,
            budget=budget,
        ),
        writer_model,
    )


def _read_source(source: Path, *, budget: _ProcessingBudget):
    from ..multimodal.reader import PdfSafetyLimitExceeded

    try:
        if source.suffix.lower() == ".pdf":
            fc = read_file(source, timeout_seconds=budget.remaining_seconds())
        else:
            fc = read_file(source)
    except PdfSafetyLimitExceeded as exc:
        raise PdfProcessingLimitExceeded(str(exc), transient=exc.transient) from exc
    budget.check_time()
    return fc


def _preflight_pdf_work(
    fc,
    *,
    allow_large_pdf: bool,
    budget: _ProcessingBudget,
) -> None:
    budget.check_time()
    if fc.mime_type != "application/pdf" or fc.pdf_preflight is None:
        return

    preflight = fc.pdf_preflight
    text_chunks = 0 if fc.text.startswith("[PDF:") else len(_text_chunks(fc.text))
    vision_batches = (
        ceil(preflight.image_count / _VISION_BATCH_SIZE) if preflight.vision_required else 0
    )
    model_calls = _summary_call_count(text_chunks) + _summary_call_count(vision_batches)
    if text_chunks and vision_batches:
        model_calls += 1
    if text_chunks > _PDF_HARD_MAX_TEXT_CHUNKS:
        raise PdfProcessingLimitExceeded(
            f"PDF expands to {text_chunks} text chunks; the hard safety limit is "
            f"{_PDF_HARD_MAX_TEXT_CHUNKS}."
        )
    if model_calls > _PDF_HARD_MAX_MODEL_CALLS:
        raise PdfProcessingLimitExceeded(
            f"PDF needs {model_calls} model calls; the hard safety limit is "
            f"{_PDF_HARD_MAX_MODEL_CALLS}."
        )

    confirmation_reasons = []
    if preflight.source_bytes > _PDF_AUTO_MAX_SOURCE_BYTES:
        mib = ceil(preflight.source_bytes / (1024 * 1024))
        confirmation_reasons.append(f"{mib} MiB source file")
    if preflight.page_count > _PDF_AUTO_MAX_PAGES:
        confirmation_reasons.append(f"{preflight.page_count} pages")
    if text_chunks > _PDF_AUTO_MAX_TEXT_CHUNKS:
        confirmation_reasons.append(f"{text_chunks} text chunks")
    if preflight.vision_required and preflight.image_count > _PDF_AUTO_MAX_IMAGES:
        confirmation_reasons.append(f"{preflight.image_count} images")
    if preflight.decoded_image_bytes > _PDF_AUTO_MAX_DECODED_IMAGE_BYTES:
        mib = ceil(preflight.decoded_image_bytes / (1024 * 1024))
        confirmation_reasons.append(f"{mib} MiB decoded images")
    if model_calls > _PDF_AUTO_MAX_MODEL_CALLS:
        confirmation_reasons.append(f"{model_calls} model calls")
    if confirmation_reasons and not allow_large_pdf:
        raise PdfConfirmationRequired(
            "PDF needs confirmation before processing: " + ", ".join(confirmation_reasons) + "."
        )
    budget.calls_remaining = model_calls


def _summary_call_count(items: int) -> int:
    return 0 if items == 0 else items * 2 - 1


def preserve_raw_source(ws: Workspace, source: Path) -> Path:
    """Copy a received source into the immutable content-addressed raw store."""
    raw_copy = _raw_source_path(ws, source)
    if source.resolve() != raw_copy.resolve() and not raw_copy.exists():
        ws.raw.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, raw_copy)
    return raw_copy


def _raw_source_path(ws: Workspace, source: Path) -> Path:
    if source.parent.resolve() == ws.raw.resolve():
        return source
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    suffix = source.suffix.lower()
    tail = f"-{digest.hexdigest()[:12]}{suffix}"
    try:
        name_max = os.pathconf(ws.raw, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        name_max = _DEFAULT_FILESYSTEM_NAME_MAX_BYTES
    if name_max <= 0:
        name_max = _DEFAULT_FILESYSTEM_NAME_MAX_BYTES
    stem_max = name_max - len(os.fsencode(tail))
    if stem_max < 0:
        raise ValueError(f"Could not preserve the source filename suffix for {source.name}.")
    stem = source.stem
    while stem and len(os.fsencode(stem)) > stem_max:
        stem = stem[:-1]
    return ws.raw / f"{stem}{tail}"


def _model_kwargs(backend, model: str | None) -> dict:
    if not model:
        return {}
    from ..inference.ollama import OllamaBackend

    return {"model": model} if isinstance(backend, OllamaBackend) else {}


def _summarize_text(
    ws: Workspace,
    source_name: str,
    text: str,
    backend,
    *,
    model_kwargs: dict | None = None,
    budget: _ProcessingBudget,
) -> str:
    schema = ws.schema_md.read_text()
    chunks = _text_chunks(text)
    kwargs = model_kwargs or {}
    if len(chunks) == 1:
        return budget.chat(backend, prompts.summarize_source(schema, source_name, text), **kwargs)

    summaries = (
        budget.chat(
            backend,
            prompts.summarize_source(
                schema,
                f"{source_name} (part {index} of {len(chunks)})",
                chunk,
            ),
            **kwargs,
        )
        for index, chunk in enumerate(chunks, start=1)
    )
    return _reduce_summaries(
        ws, source_name, summaries, backend, model_kwargs=kwargs, budget=budget
    )


def _reduce_summaries(
    ws: Workspace,
    source_name: str,
    summaries: Iterable[str],
    backend,
    *,
    model_kwargs: dict | None = None,
    round_number: int = 1,
    budget: _ProcessingBudget,
) -> str:
    iterator = iter(summaries)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise _NoSourceSummaries("no source summaries to reduce") from exc
    try:
        second = next(iterator)
    except StopIteration:
        return first

    schema = ws.schema_md.read_text()
    kwargs = model_kwargs or {}

    def reduced():
        group = []
        group_number = 1
        for summary in chain((first, second), iterator):
            group.append(summary[:_INTERMEDIATE_SUMMARY_CHARS])
            if len(group) < 2:
                continue
            combined = "\n\n".join(
                f"PART {index}:\n{value}" for index, value in enumerate(group, start=1)
            )
            yield budget.chat(
                backend,
                prompts.summarize_source(
                    schema,
                    f"{source_name} (summary group {group_number}, round {round_number})",
                    combined,
                ),
                **kwargs,
            )
            group = []
            group_number += 1
        if group:
            yield group[0]

    return _reduce_summaries(
        ws,
        source_name,
        reduced(),
        backend,
        model_kwargs=kwargs,
        round_number=round_number + 1,
        budget=budget,
    )


def _append_visual_facts(page: str, vision_summary: str) -> str:
    detail_lines = []
    for line in vision_summary.splitlines():
        if line.lower().startswith("## related"):
            break
        if line.startswith("# "):
            continue
        detail_lines.append(line)
    details = "\n".join(detail_lines).strip()
    if not details:
        return page
    visual_section = f"## Visual facts\n{details}"
    before_related = page.partition("## Related")[0].rstrip()
    related_links = list(
        dict.fromkeys(m.group(0) for m in WIKILINK_RE.finditer(page + vision_summary))
    )
    result = f"{before_related}\n\n{visual_section}"
    if related_links:
        result += "\n\n## Related\n" + " ".join(related_links)
    return result


def _text_chunks(text: str) -> list[str]:
    chunks = []
    current = ""
    for paragraph in text.split("\n\n"):
        pieces = [
            paragraph[start : start + _SOURCE_CHUNK_CHARS]
            for start in range(0, len(paragraph), _SOURCE_CHUNK_CHARS)
        ] or [""]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if current and len(candidate) > _SOURCE_CHUNK_CHARS:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def _ingest_with_vision(
    ws,
    fc,
    backend,
    *,
    image_batches: Iterable[list[str]] | None = None,
    context_text: str | None = None,
    budget: _ProcessingBudget | None = None,
    vision_config: tuple[dict, str] | None = None,
    vision_max_accuracy: bool = False,
) -> tuple[str, str]:
    """Use a vision model to summarise an image or image-rich PDF."""
    budget = budget or _ProcessingBudget.start()
    schema = ws.schema_md.read_text()
    model_kwargs, writer_model = vision_config or _vision_backend_config(
        fc, backend, budget=budget, vision_max_accuracy=vision_max_accuracy
    )

    def summaries():
        batches = image_batches or fc.iter_image_batches(_VISION_BATCH_SIZE)
        for batch_number, images in enumerate(batches, start=1):
            source_name = f"{fc.source_name} (image batch {batch_number})"
            messages = prompts.summarize_source(
                schema, source_name, context_text if context_text is not None else fc.text
            )
            messages[-1].images = images
            yield budget.chat(backend, messages, **model_kwargs)

    try:
        page_md = _reduce_summaries(
            ws,
            fc.source_name,
            summaries(),
            backend,
            model_kwargs=model_kwargs,
            budget=budget,
        )
    except _NoSourceSummaries as exc:
        from ..inference.base import BackendError

        raise BackendError("This scanned PDF contains no extractable page images.") from exc
    return page_md, writer_model


def _vision_backend_config(
    fc, backend, *, budget: _ProcessingBudget | None = None, vision_max_accuracy: bool = False
) -> tuple[dict, str]:
    from ..inference.base import BackendError
    from ..inference.ollama import OllamaBackend
    from ..routing.router import RoutingDeadlineExceeded, TaskRouter, TaskType

    model_kwargs: dict = {}
    writer_model = getattr(backend, "model", "") or ""
    is_pdf = fc.mime_type == "application/pdf"
    is_image = fc.mime_type.startswith("image/")
    if is_pdf and not isinstance(backend, OllamaBackend):
        raise BackendError("PDF images require a local Ollama vision model.")
    if is_image and not isinstance(backend, OllamaBackend):
        raise BackendError("Images require a local Ollama vision model.")
    if isinstance(backend, OllamaBackend):
        if is_pdf and not _is_loopback_url(backend.base_url):
            raise BackendError("PDF images require Ollama on the local loopback interface.")
        try:
            vision_model = TaskRouter(
                backend.base_url,
                allow_cloud=False,
                trust_env=not is_pdf,
                deadline=budget.deadline if is_pdf and budget is not None else None,
                vision_max_accuracy=vision_max_accuracy,
            ).pick(TaskType.VISION, fallback_tag="")
        except RoutingDeadlineExceeded as exc:
            raise PdfProcessingLimitExceeded(
                "PDF processing exceeded the 10 minute hard safety limit.", transient=True
            ) from exc
        if not vision_model:
            source_label = "scanned PDF" if is_pdf else "image"
            raise BackendError(
                f"This {source_label} needs a local vision model. Install granite3.2-vision:2b in Ollama."
            )
        model_kwargs.update(model=vision_model, think=False, num_predict=1_024, temperature=0.0)
        if is_pdf:
            model_kwargs["trust_env"] = False
        writer_model = vision_model
    return model_kwargs, writer_model


def _is_loopback_url(base_url: str) -> bool:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
