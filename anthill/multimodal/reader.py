from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

PDF_HARD_MAX_PAGES = 200
PDF_HARD_MAX_IMAGES = 32
PDF_HARD_MAX_IMAGE_OCCURRENCES = PDF_HARD_MAX_PAGES * PDF_HARD_MAX_IMAGES
PDF_HARD_MAX_DECODED_IMAGE_BYTES = 128 * 1024 * 1024
PDF_HARD_MAX_SOURCE_BYTES = 25 * 1024 * 1024
PDF_HARD_PROCESSING_SECONDS = 10 * 60
PDF_HARD_WORKER_MEMORY_BYTES = 1024 * 1024 * 1024
PDF_HARD_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
PDF_HARD_WORKER_STDERR_BYTES = 64 * 1024
_WINDOWS_TRANSIENT_WORKER_EXIT_CODES = frozenset(
    {
        0xC0000017,
        0xC0000044,
        0xC000009A,
    }
)
_TRANSIENT_WORKER_SIGNALS = frozenset(
    signal_number
    for signal_name in ("SIGKILL", "SIGTERM", "SIGXCPU")
    if (signal_number := getattr(signal, signal_name, None)) is not None
)
_PDF_VISUAL_MIN_PAGE_COVERAGE = 0.02
_PDF_REPEATED_VISUAL_MIN_PAGE_COVERAGE = 0.20
_PdfMatrix = tuple[float, float, float, float, float, float]
_PDF_TEXT_OPERATORS = frozenset(
    {
        b"BT",
        b"ET",
        b"Tc",
        b"Tw",
        b"Tz",
        b"TL",
        b"Tf",
        b"Tr",
        b"Ts",
        b"Td",
        b"TD",
        b"Tm",
        b"T*",
        b"Tj",
        b"TJ",
        b"'",
        b'"',
    }
)


class PdfParseError(ValueError):
    """The PDF worker rejected or returned malformed data for a source file."""


class PdfSafetyLimitExceeded(ValueError):
    """A PDF hit a hard safety limit.

    `transient` marks a failure of the host or this run - a wall-clock timeout, a worker
    the OS killed, a memory boundary that could not be established - rather than a
    property of the file. The same file can succeed on the next attempt, so callers
    retry those instead of rejecting the source.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class PdfPreflight:
    page_count: int
    image_count: int
    decoded_image_bytes: int
    vision_required: bool
    source_bytes: int = 0


@dataclass
class _PdfImageCandidate:
    page: Any
    image_key: str | list[str] | None
    identity: tuple[Any, ...]
    width: int
    height: int
    max_page_coverage: float
    page_numbers: set[int]
    inline_settings: Any | None = None
    inline_data: bytes | None = None
    inline_resources: Any | None = None
    render_path: Path | None = None
    render_page_number: int | None = None
    page_bounds: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)
    page_areas: dict[int, float] = field(default_factory=dict)
    vector_segments: dict[int, int] = field(default_factory=dict)
    vector_tokens: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _PdfStructure:
    page_count: int
    images: tuple[_PdfImageCandidate, ...]
    selected_images: tuple[_PdfImageCandidate, ...]
    has_text: bool


@dataclass
class _PdfInspectionState:
    image_occurrences: int = 0
    raster_content_digests: dict[tuple[Any, ...], bytes] = field(default_factory=dict)
    raster_context_digests: dict[tuple[Any, ...], bytes] = field(default_factory=dict)
    raster_identities: dict[tuple[Any, ...], tuple[Any, ...]] = field(default_factory=dict)


@dataclass
class FileContent:
    text: str  # extracted text (always populated)
    images_b64: list[str] = field(default_factory=list)  # base64 PNGs
    mime_type: str = "text/plain"
    source_name: str = ""
    image_batches: Callable[[int], Iterator[list[str]]] | None = field(default=None, repr=False)
    pdf_preflight: PdfPreflight | None = None

    @property
    def has_images(self) -> bool:
        return bool(self.images_b64) or self.image_batches is not None

    def iter_image_batches(self, batch_size: int) -> Iterator[list[str]]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.image_batches is not None:
            yield from self.image_batches(batch_size)
            return
        for start in range(0, len(self.images_b64), batch_size):
            yield self.images_b64[start : start + batch_size]


def read_file(path: Path, *, timeout_seconds: float = PDF_HARD_PROCESSING_SECONDS) -> FileContent:
    """Read any supported file type and return text + images.

    Supported: .md, .txt, .pdf, .docx, .pptx, .xlsx, .html, .htm,
    .png, .jpg, .jpeg, .gif, .webp
    """
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        _check_pdf_source_size(path)
        return _read_pdf_bounded(path, _pdf_timeout_seconds(timeout_seconds))

    if suffix in {".docx", ".pptx", ".xlsx", ".html", ".htm"}:
        return _read_office(path)

    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return _read_image(path)

    # Plain text / markdown
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(
            f"{path.name} isn't UTF-8 text. "
            "Supported formats: .md .txt .pdf .docx .pptx .xlsx .html .htm "
            ".png .jpg .jpeg .gif .webp"
        ) from e
    return FileContent(text=text, mime_type="text/plain", source_name=path.name)


def _pdf_timeout_seconds(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise PdfSafetyLimitExceeded(
            "PDF parsing exceeded the hard processing-time limit.", transient=True
        )
    return min(float(timeout_seconds), PDF_HARD_PROCESSING_SECONDS)


def _read_pdf_bounded(path: Path, timeout_seconds: float) -> FileContent:
    timeout_seconds = _pdf_timeout_seconds(timeout_seconds)
    command = (
        [sys.executable, "--pdf-worker", str(path)]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "anthill.multimodal.pdf_worker", str(path)]
    )
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    overflow = threading.Event()

    def drain(pipe, chunks: list[bytes], limit: int) -> None:
        size = 0
        while True:
            block = pipe.read(64 * 1024)
            if not block:
                return
            if size < limit:
                chunks.append(block[: limit - size])
            size += len(block)
            if size > limit:
                overflow.set()

    readers = [
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout_chunks, PDF_HARD_WORKER_OUTPUT_BYTES),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr_chunks, PDF_HARD_WORKER_STDERR_BYTES),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        if overflow.is_set():
            process.kill()
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            process.kill()
            break
        try:
            process.wait(timeout=min(0.1, remaining))
        except subprocess.TimeoutExpired:
            continue
    process.wait()
    for reader in readers:
        reader.join()
    if timed_out:
        raise PdfSafetyLimitExceeded(
            "PDF parsing exceeded the hard processing-time limit.", transient=True
        )
    if overflow.is_set():
        raise PdfParseError(f"Could not safely parse {path.name}.")
    output = b"".join(stdout_chunks)
    error = b"".join(stderr_chunks)
    if process.returncode != 0:
        if _worker_exit_is_transient(process.returncode):
            raise PdfSafetyLimitExceeded(
                _abnormal_worker_termination_message(process.returncode), transient=True
            )
        if process.returncode < 0:
            raise PdfSafetyLimitExceeded(_abnormal_worker_termination_message(process.returncode))
        detail = error.decode(errors="replace")[-500:].strip()
        raise PdfParseError(detail or f"Could not safely parse {path.name}.")
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PdfParseError(f"Could not safely parse {path.name}.") from exc
    if not isinstance(result, dict) or set(result) != {"version", "status", "payload"}:
        raise PdfParseError(f"Could not safely parse {path.name}.")
    if type(result["version"]) is not int or result["version"] != 1:
        raise PdfParseError(f"Could not safely parse {path.name}.")
    status = result["status"]
    payload = result["payload"]
    if type(status) is not str or status not in {
        "ok",
        "safety",
        "safety-transient",
        "import",
        "error",
    }:
        raise PdfParseError(f"Could not safely parse {path.name}.")
    if status == "ok":
        try:
            return _file_content_from_payload(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise PdfParseError(f"Could not safely parse {path.name}.") from exc
    if type(payload) is not str:
        raise PdfParseError(f"Could not safely parse {path.name}.")
    if status == "safety":
        raise PdfSafetyLimitExceeded(payload)
    if status == "safety-transient":
        raise PdfSafetyLimitExceeded(payload, transient=True)
    if status == "import":
        raise ImportError(payload)
    raise PdfParseError(payload)


def _file_content_from_payload(payload: object) -> FileContent:
    if not isinstance(payload, dict) or set(payload) != {
        "text",
        "images_b64",
        "mime_type",
        "source_name",
        "pdf_preflight",
    }:
        raise ValueError("invalid file content")
    if type(payload["text"]) is not str or type(payload["mime_type"]) is not str:
        raise ValueError("invalid file content")
    if type(payload["source_name"]) is not str or not isinstance(payload["images_b64"], list):
        raise ValueError("invalid file content")
    if any(type(image) is not str for image in payload["images_b64"]):
        raise ValueError("invalid file content")
    preflight = payload["pdf_preflight"]
    parsed_preflight = None
    if preflight is not None:
        if not isinstance(preflight, dict) or set(preflight) != {
            "page_count",
            "image_count",
            "decoded_image_bytes",
            "vision_required",
            "source_bytes",
        }:
            raise ValueError("invalid PDF preflight")
        if (
            any(
                type(preflight[key]) is not int
                for key in ("page_count", "image_count", "decoded_image_bytes", "source_bytes")
            )
            or type(preflight["vision_required"]) is not bool
        ):
            raise ValueError("invalid PDF preflight")
        parsed_preflight = PdfPreflight(**preflight)
    return FileContent(
        text=payload["text"],
        images_b64=payload["images_b64"],
        mime_type=payload["mime_type"],
        source_name=payload["source_name"],
        pdf_preflight=parsed_preflight,
    )


def _file_content_to_payload(content: FileContent) -> dict[str, object]:
    preflight = content.pdf_preflight
    return {
        "text": content.text,
        "images_b64": content.images_b64,
        "mime_type": content.mime_type,
        "source_name": content.source_name,
        "pdf_preflight": None
        if preflight is None
        else {
            "page_count": preflight.page_count,
            "image_count": preflight.image_count,
            "decoded_image_bytes": preflight.decoded_image_bytes,
            "vision_required": preflight.vision_required,
            "source_bytes": preflight.source_bytes,
        },
    }


def _worker_exit_is_transient(returncode: int) -> bool:
    if returncode in _WINDOWS_TRANSIENT_WORKER_EXIT_CODES:
        return True
    return returncode < 0 and -returncode in _TRANSIENT_WORKER_SIGNALS


def _abnormal_worker_termination_message(returncode: int) -> str:
    if returncode in _WINDOWS_TRANSIENT_WORKER_EXIT_CODES:
        name = f"Windows status 0x{returncode:08X}"
    else:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
    return (
        f"PDF parsing worker terminated abnormally ({name}); "
        "the file may be malformed or exceed a system resource limit."
    )


def _read_pdf(path: Path) -> FileContent:
    source_bytes = _check_pdf_source_size(path)
    structure = _inspect_pdf_structure(path)
    selected_images = structure.selected_images
    try:
        from markitdown import MarkItDown, StreamInfo
        from markitdown.converters import PdfConverter
    except ImportError as e:
        raise ImportError("Install the PDF reader: pip install 'markitdown[pdf]>=0.1.6'") from e

    # A local stream and explicit PDF metadata keep conversion on-device. Do not pass a URL or
    # enable third-party plugins: scanned pages are handled by Anthill's local vision path below.
    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    converter.register_converter(PdfConverter())
    with path.open("rb") as stream:
        result = converter.convert_stream(
            stream,
            stream_info=StreamInfo(
                mimetype="application/pdf",
                extension=".pdf",
                filename=path.name,
                local_path=str(path),
            ),
        )

    text = result.markdown.strip()
    images_b64, decoded_image_bytes = _decode_pdf_images(selected_images, path.name)
    if text:
        text = re.sub(r"\s*\f\s*", "\n\n---\n\n", text)
    if not text:
        text = f"[PDF: {path.name} - no extractable text. Images require vision extraction.]"

    return FileContent(
        text=text,
        images_b64=images_b64,
        mime_type="application/pdf",
        source_name=path.name,
        pdf_preflight=PdfPreflight(
            page_count=structure.page_count,
            image_count=len(images_b64),
            decoded_image_bytes=decoded_image_bytes,
            vision_required=not result.markdown.strip() or bool(selected_images),
            source_bytes=source_bytes,
        ),
    )


_OFFICE_MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
    ".htm": "text/html",
}


def _read_office(path: Path) -> FileContent:
    """Convert a .docx/.pptx/.xlsx/.html/.htm file to markdown text via MarkItDown.

    Mirrors `_read_pdf`'s pattern: a locked-down `MarkItDown` instance (no builtins, no
    plugins) with exactly one converter registered, fed a local stream plus explicit
    `StreamInfo` so conversion never needs a URL or third-party plugin.

    Deliberately WITHOUT `_read_pdf`'s safety-limit and image-extraction machinery:
    - No page/image preflight is computed, so `pdf_preflight` stays `None` and
      `wiki.ingest._preflight_pdf_work` (which only acts on `mime_type == "application/pdf"`)
      is a no-op for these files - there is no size/page/image hard-limit gate here.
    - No embedded images are extracted. A picture inside a .docx/.pptx/.xlsx is dropped from
      the converted text, not surfaced to the vision model the way PDF page images are.
      Office-format image extraction is future work, not part of this phase.
    """
    suffix = path.suffix.lower()
    mime_type = _OFFICE_MIME_TYPES[suffix]
    try:
        from markitdown import MarkItDown, StreamInfo
        from markitdown.converters import (
            DocxConverter,
            HtmlConverter,
            PptxConverter,
            XlsxConverter,
        )
    except ImportError as e:
        raise ImportError(
            "Install the Office reader: pip install 'markitdown[docx,pptx,xlsx]>=0.1.6'"
        ) from e

    converter_cls: type = {
        ".docx": DocxConverter,
        ".pptx": PptxConverter,
        ".xlsx": XlsxConverter,
        ".html": HtmlConverter,
        ".htm": HtmlConverter,
    }[suffix]

    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    converter.register_converter(converter_cls())
    with path.open("rb") as stream:
        result = converter.convert_stream(
            stream,
            stream_info=StreamInfo(
                mimetype=mime_type,
                extension=suffix,
                filename=path.name,
            ),
        )

    text = result.markdown.strip()
    if not text:
        text = f"[{suffix.lstrip('.').upper()}: {path.name} - no extractable text.]"

    return FileContent(text=text, mime_type=mime_type, source_name=path.name)


def _check_pdf_source_size(path: Path) -> int:
    source_bytes = path.stat().st_size
    if source_bytes > PDF_HARD_MAX_SOURCE_BYTES:
        limit_mib = PDF_HARD_MAX_SOURCE_BYTES // (1024 * 1024)
        raise PdfSafetyLimitExceeded(
            f"PDF exceeds the {limit_mib} MiB source-file hard safety limit."
        )
    return source_bytes


def _inspect_pdf_structure(path: Path) -> _PdfStructure:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = reader.pages
    page_count = len(pages)
    if page_count > PDF_HARD_MAX_PAGES:
        raise PdfSafetyLimitExceeded(
            f"PDF has {page_count} pages; the hard safety limit is {PDF_HARD_MAX_PAGES}."
        )

    candidates: dict[tuple[Any, ...], _PdfImageCandidate] = {}
    state = _PdfInspectionState()
    text_pages: set[int] = set()
    for page_number, page in enumerate(pages):
        try:
            page_area = abs(float(page.mediabox.width) * float(page.mediabox.height))
            if page_area <= 0:
                raise ValueError("invalid page dimensions")
            resources = _resolved(page.get("/Resources", {}))
            _inspect_content_images(
                page.get_contents(),
                resources,
                reader,
                page=page,
                page_number=page_number,
                page_area=page_area,
                path=(),
                matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                candidates=candidates,
                form_stack=set(),
                text_pages=text_pages,
                source_path=path,
                vector_identity=("render-page", page_number),
                state=state,
            )
        except PdfSafetyLimitExceeded:
            raise
        except Exception as exc:
            raise ValueError(f"Could not safely inspect every image in {path.name}.") from exc

    images = _deduplicate_vector_candidates(tuple(candidates.values()))
    return _PdfStructure(
        page_count=page_count,
        images=images,
        selected_images=_select_pdf_images(images, path.name, include_all=not text_pages),
        has_text=bool(text_pages),
    )


def _inspect_content_images(
    content,
    resources,
    reader,
    *,
    page,
    page_number: int,
    page_area: float,
    path: tuple[str, ...],
    matrix: _PdfMatrix,
    candidates: dict[tuple[Any, ...], _PdfImageCandidate],
    form_stack: set[tuple[Any, ...]],
    text_pages: set[int],
    source_path: Path,
    vector_identity: tuple[Any, ...],
    state: _PdfInspectionState,
) -> None:
    if content is None:
        return
    from pypdf.generic import ContentStream

    operations = (
        content.operations
        if hasattr(content, "operations")
        else ContentStream(content, reader).operations
    )
    xobjects = _resolved(resources.get("/XObject", {})) if resources else {}
    matrix_stack: list[_PdfMatrix] = []
    initial_matrix = matrix
    path_points: list[tuple[float, float]] = []
    path_segments = 0
    content_digest = hashlib.sha256() if vector_identity[0] == "render-page" else None
    marked_content_stack: list[bool] = []
    for operands, operator in operations:
        if content_digest is not None:
            token = _pdf_vector_content_token(operator, operands, resources, marked_content_stack)
        else:
            token = None
        if token is not None and content_digest is not None:
            content_digest.update(repr(token).encode())
            content_digest.update(b"\n")
        if operator in {b"Tj", b"TJ", b"'", b'"'} and operands:
            text_pages.add(page_number)
        if operator == b"q":
            matrix_stack.append(matrix)
        elif operator == b"Q":
            matrix = matrix_stack.pop() if matrix_stack else initial_matrix
        elif operator == b"cm" and len(operands) == 6:
            operand_matrix = cast(_PdfMatrix, tuple(float(value) for value in operands))
            matrix = _concatenate_pdf_matrix(operand_matrix, matrix)
        elif operator in {b"m", b"l"} and len(operands) >= 2:
            path_points.append(_transform_pdf_point(matrix, operands[-2], operands[-1]))
            path_segments += operator == b"l"
        elif operator == b"c" and len(operands) >= 6:
            path_points.extend(
                _transform_pdf_point(matrix, operands[index], operands[index + 1])
                for index in (0, 2, 4)
            )
            path_segments += 1
        elif operator in {b"v", b"y"} and len(operands) >= 4:
            path_points.extend(
                _transform_pdf_point(matrix, operands[index], operands[index + 1])
                for index in (0, 2)
            )
            path_segments += 1
        elif operator == b"re" and len(operands) >= 4:
            x, y, width, height = (float(value) for value in operands[:4])
            path_points.extend(
                _transform_pdf_point(matrix, point_x, point_y)
                for point_x, point_y in (
                    (x, y),
                    (x + width, y),
                    (x, y + height),
                    (x + width, y + height),
                )
            )
            path_segments += 4
        elif operator in {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}:
            if path_points:
                _record_pdf_vector(
                    candidates,
                    vector_identity,
                    page,
                    source_path,
                    page_number,
                    page_area,
                    path_points,
                    max(1, path_segments),
                    operator,
                )
            path_points = []
            path_segments = 0
        elif operator == b"n":
            path_points = []
            path_segments = 0
        elif operator == b"INLINE IMAGE":
            settings = operands.get("settings", {})
            data = operands.get("data")
            if not isinstance(data, bytes):
                raise ValueError("invalid inline image data")
            width = _positive_int(settings.get("/W", settings.get("/Width")))
            height = _positive_int(settings.get("/H", settings.get("/Height")))
            _record_pdf_image_occurrence(state)
            identity = _inline_image_identity(data, settings, resources)
            _record_pdf_image(
                candidates,
                identity,
                page,
                None,
                width,
                height,
                abs(_pdf_matrix_determinant(matrix)) / page_area,
                page_number,
                inline_settings=settings,
                inline_data=data,
                inline_resources=resources,
            )
        elif operator == b"Do" and operands:
            name = operands[0]
            if name not in xobjects:
                continue
            xobject = _resolved(xobjects[name])
            subtype = xobject.get("/Subtype")
            image_path = (*path, str(name))
            if subtype == "/Image":
                _record_pdf_image_occurrence(state)
                identity = _pdf_raster_identity(xobject, resources, state)
                key: str | list[str] = image_path[0] if len(image_path) == 1 else list(image_path)
                _record_pdf_image(
                    candidates,
                    identity,
                    page,
                    key,
                    _positive_int(xobject.get("/Width")),
                    _positive_int(xobject.get("/Height")),
                    abs(_pdf_matrix_determinant(matrix)) / page_area,
                    page_number,
                )
            elif subtype == "/Form":
                identity = _pdf_object_identity(xobject)
                if identity in form_stack:
                    continue
                xobject_matrix = xobject.get("/Matrix", (1, 0, 0, 1, 0, 0))
                form_matrix = matrix
                if isinstance(xobject_matrix, Sequence) and len(xobject_matrix) == 6:
                    form_matrix = _concatenate_pdf_matrix(
                        cast(_PdfMatrix, tuple(float(value) for value in xobject_matrix)),
                        form_matrix,
                    )
                form_resources = _resolved(xobject.get("/Resources", resources))
                _inspect_content_images(
                    ContentStream(xobject, reader),
                    form_resources,
                    reader,
                    page=page,
                    page_number=page_number,
                    page_area=page_area,
                    path=image_path,
                    matrix=form_matrix,
                    candidates=candidates,
                    form_stack=form_stack | {identity},
                    text_pages=text_pages,
                    source_path=source_path,
                    vector_identity=("render-form", *identity),
                    state=state,
                )
    candidate = candidates.get(vector_identity)
    if candidate is not None and content_digest is not None:
        candidate.vector_tokens.append(f"content:{content_digest.hexdigest()}")


def _concatenate_pdf_matrix(first: _PdfMatrix, second: _PdfMatrix) -> _PdfMatrix:
    a, b, c, d, e, f = first
    aa, bb, cc, dd, ee, ff = second
    return (
        a * aa + b * cc,
        a * bb + b * dd,
        c * aa + d * cc,
        c * bb + d * dd,
        e * aa + f * cc + ee,
        e * bb + f * dd + ff,
    )


def _transform_pdf_point(matrix: _PdfMatrix, x, y) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x_value = float(x)
    y_value = float(y)
    return (x_value * a + y_value * c + e, x_value * b + y_value * d + f)


def _pdf_matrix_determinant(matrix: _PdfMatrix) -> float:
    return matrix[0] * matrix[3] - matrix[1] * matrix[2]


def _resolved(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _positive_int(value) -> int:
    number = int(_resolved(value))
    if number <= 0:
        raise ValueError("invalid image dimensions")
    return number


def _pdf_object_identity(value) -> tuple[Any, ...]:
    reference = getattr(value, "indirect_reference", None)
    if reference is not None:
        return ("indirect", reference.idnum, reference.generation)
    return ("direct", id(value))


def _inline_image_identity(data: bytes, settings, resources) -> tuple[Any, ...]:
    from pypdf._page import _INLINE_IMAGE_KEY_MAPPING

    normalized = []
    for raw_key, raw_value in settings.items():
        key = str(raw_key)
        if key in {"/Length", "/L"}:
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        translated_values = tuple(
            _translate_inline_image_value(value, resources) for value in values
        )
        translated = translated_values if isinstance(raw_value, list) else translated_values[0]
        normalized.append(
            (_INLINE_IMAGE_KEY_MAPPING.get(key, key), _canonical_pdf_value(translated))
        )
    digest = _incremental_pdf_raster_digest(data)
    _update_pdf_fingerprint(digest, sorted(normalized), set())
    return ("inline", digest.hexdigest())


def _pdf_raster_identity(value, resources, state: _PdfInspectionState) -> tuple[Any, ...]:
    content_key = _pdf_cache_identity(value)
    color_space = _pdf_raster_color_space(value, resources)
    context_key = _pdf_cache_identity(color_space)
    cache_key = (content_key, context_key)
    cached = state.raster_identities.get(cache_key)
    if cached is not None:
        return cached
    data = getattr(value, "_data", None)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        identity: tuple[Any, ...] = ("raster-object", content_key, context_key)
        state.raster_identities[cache_key] = identity
        return identity
    content_digest = state.raster_content_digests.get(content_key)
    if content_digest is None:
        digest = hashlib.sha256()
        _update_pdf_fingerprint(digest, value, set())
        content_digest = digest.digest()
        state.raster_content_digests[content_key] = content_digest
    context_digest = state.raster_context_digests.get(context_key)
    if context_digest is None:
        digest = hashlib.sha256()
        _update_pdf_fingerprint(digest, color_space, set())
        context_digest = digest.digest()
        state.raster_context_digests[context_key] = context_digest
    digest = hashlib.sha256()
    digest.update(content_digest)
    digest.update(context_digest)
    identity = ("raster", digest.hexdigest())
    state.raster_identities[cache_key] = identity
    return identity


def _pdf_raster_color_space(value, resources):
    color_space = value.get("/ColorSpace")
    if isinstance(color_space, str) and resources:
        color_spaces = _resolved(resources.get("/ColorSpace", {}))
        if color_space in color_spaces:
            return color_spaces[color_space]
    return color_space


def _pdf_cache_identity(value) -> tuple[Any, ...]:
    reference = getattr(value, "indirect_reference", None)
    if reference is not None:
        return ("indirect", reference.idnum, reference.generation)
    idnum = getattr(value, "idnum", None)
    generation = getattr(value, "generation", None)
    if idnum is not None and generation is not None:
        return ("indirect", idnum, generation)
    return ("direct", id(value))


def _incremental_pdf_raster_digest(data: bytes | bytearray | memoryview):
    digest = hashlib.sha256()
    _update_pdf_fingerprint_bytes(digest, data)
    return digest


def _update_pdf_fingerprint_bytes(digest, data: bytes | bytearray | memoryview) -> None:
    view = memoryview(data)
    for offset in range(0, len(view), 64 * 1024):
        digest.update(view[offset : offset + 64 * 1024])


def _update_pdf_fingerprint(digest, value, seen: set[int]) -> None:
    value = _resolved(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        digest.update(b"bytes:")
        _update_pdf_fingerprint_bytes(digest, value)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            digest.update(b"cycle")
            return
        seen.add(identity)
        digest.update(b"dict:")
        stream_data = getattr(value, "_data", None)
        if isinstance(stream_data, (bytes, bytearray, memoryview)):
            _update_pdf_fingerprint_bytes(digest, stream_data)
        for key in sorted(value, key=str):
            if str(key) == "/Length":
                continue
            _update_pdf_fingerprint(digest, str(key), seen)
            _update_pdf_fingerprint(digest, value[key], seen)
        seen.remove(identity)
        return
    if isinstance(value, Sequence) and not isinstance(value, str):
        identity = id(value)
        if identity in seen:
            digest.update(b"cycle")
            return
        seen.add(identity)
        digest.update(b"sequence:")
        for item in value:
            _update_pdf_fingerprint(digest, item, seen)
        seen.remove(identity)
        return
    digest.update(type(value).__name__.encode())
    digest.update(b":")
    digest.update(str(value).encode())


def _record_pdf_image_occurrence(state: _PdfInspectionState) -> None:
    state.image_occurrences += 1
    if state.image_occurrences > PDF_HARD_MAX_IMAGE_OCCURRENCES:
        raise PdfSafetyLimitExceeded(
            f"PDF has more than {PDF_HARD_MAX_IMAGE_OCCURRENCES} image occurrences, "
            "which exceeds the hard safety limit."
        )


def _canonical_pdf_value(value):
    value = _resolved(value)
    if isinstance(value, bytes):
        return ("bytes", value.hex())
    if isinstance(value, dict):
        return tuple(sorted((str(key), _canonical_pdf_value(item)) for key, item in value.items()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_canonical_pdf_value(item) for item in value)
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def _pdf_vector_content_token(operator, operands, resources, marked_content_stack):
    if operator in _PDF_TEXT_OPERATORS:
        return None
    if operator in {b"MP", b"DP"}:
        return None
    if operator in {b"BMC", b"BDC"}:
        optional_content = bool(operands and str(operands[0]) == "/OC")
        marked_content_stack.append(optional_content)
        if not optional_content:
            return None
        normalized_operands = list(operands)
        if operator == b"BDC" and len(normalized_operands) > 1:
            properties = _resolved(resources.get("/Properties", {})) if resources else {}
            property_name = normalized_operands[1]
            if isinstance(property_name, str) and property_name in properties:
                normalized_operands[1] = _resolved(properties[property_name])
        return (operator.decode("ascii"), _canonical_pdf_value(normalized_operands))
    if operator == b"EMC":
        optional_content = marked_content_stack.pop() if marked_content_stack else False
        return ("EMC", ()) if optional_content else None
    return (operator.decode("latin-1"), _canonical_pdf_value(operands))


def _record_pdf_image(
    candidates: dict[tuple[Any, ...], _PdfImageCandidate],
    identity: tuple[Any, ...],
    page,
    image_key: str | list[str] | None,
    width: int,
    height: int,
    coverage: float,
    page_number: int,
    *,
    inline_settings=None,
    inline_data: bytes | None = None,
    inline_resources=None,
) -> None:
    existing = candidates.get(identity)
    if existing is None:
        candidates[identity] = _PdfImageCandidate(
            page=page,
            image_key=image_key,
            identity=identity,
            width=width,
            height=height,
            max_page_coverage=coverage,
            page_numbers={page_number},
            inline_settings=inline_settings,
            inline_data=inline_data,
            inline_resources=inline_resources,
        )
        return
    existing.max_page_coverage = max(existing.max_page_coverage, coverage)
    existing.page_numbers.add(page_number)


def _record_pdf_vector(
    candidates: dict[tuple[Any, ...], _PdfImageCandidate],
    identity: tuple[Any, ...],
    page,
    source_path: Path,
    page_number: int,
    page_area: float,
    points: Sequence[tuple[float, float]],
    segments: int,
    operator: bytes,
) -> None:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    width = max(1, round(abs(float(page.mediabox.width))))
    height = max(1, round(abs(float(page.mediabox.height))))
    token = repr(
        (
            operator.decode("ascii"),
            tuple((round(x, 4), round(y, 4)) for x, y in points),
        )
    )
    existing = candidates.get(identity)
    if existing is None:
        existing = _PdfImageCandidate(
            page=page,
            image_key=None,
            identity=identity,
            width=width,
            height=height,
            max_page_coverage=0.0,
            page_numbers={page_number},
            render_path=source_path,
            render_page_number=page_number,
        )
        candidates[identity] = existing
    existing.page_numbers.add(page_number)
    existing.page_areas[page_number] = page_area
    existing.vector_segments[page_number] = existing.vector_segments.get(page_number, 0) + segments
    existing.vector_tokens.append(token)
    current = existing.page_bounds.get(page_number)
    if current is not None:
        bounds = (
            min(current[0], bounds[0]),
            min(current[1], bounds[1]),
            max(current[2], bounds[2]),
            max(current[3], bounds[3]),
        )
    existing.page_bounds[page_number] = bounds
    significance = _vector_page_significance(
        bounds,
        page_area,
        existing.vector_segments[page_number],
        width,
        height,
    )
    if significance > existing.max_page_coverage:
        existing.page = page
        existing.width = width
        existing.height = height
        existing.max_page_coverage = significance
        existing.render_page_number = page_number


def _vector_page_significance(
    bounds: tuple[float, float, float, float],
    page_area: float,
    segments: int,
    page_width: int,
    page_height: int,
) -> float:
    width = max(0.0, bounds[2] - bounds[0])
    height = max(0.0, bounds[3] - bounds[1])
    coverage = width * height / page_area
    span = max(width / page_width, height / page_height)
    if segments >= 3 and span >= 0.25:
        return max(coverage, _PDF_VISUAL_MIN_PAGE_COVERAGE)
    return coverage


def _deduplicate_vector_candidates(
    candidates: Sequence[_PdfImageCandidate],
) -> tuple[_PdfImageCandidate, ...]:
    result: list[_PdfImageCandidate] = []
    page_vectors: dict[str, _PdfImageCandidate] = {}
    for candidate in candidates:
        if not candidate.vector_tokens or candidate.identity[0] != "render-page":
            result.append(candidate)
            continue
        digest = hashlib.sha256("\n".join(candidate.vector_tokens).encode()).hexdigest()
        existing = page_vectors.get(digest)
        if existing is None:
            page_vectors[digest] = candidate
            result.append(candidate)
            continue
        existing.page_numbers.update(candidate.page_numbers)
        if candidate.max_page_coverage > existing.max_page_coverage:
            existing.page = candidate.page
            existing.width = candidate.width
            existing.height = candidate.height
            existing.max_page_coverage = candidate.max_page_coverage
            existing.render_page_number = candidate.render_page_number
    return tuple(result)


def _select_pdf_images(
    candidates: Sequence[_PdfImageCandidate],
    source_name: str,
    *,
    include_all: bool = False,
) -> tuple[_PdfImageCandidate, ...]:
    selected: list[_PdfImageCandidate] = []
    rendered_pages: set[int] = set()
    for image in candidates:
        repeated = len(image.page_numbers) > 1
        threshold = (
            _PDF_REPEATED_VISUAL_MIN_PAGE_COVERAGE if repeated else _PDF_VISUAL_MIN_PAGE_COVERAGE
        )
        if not include_all and image.max_page_coverage < threshold:
            continue
        if image.render_page_number is not None:
            if image.render_page_number in rendered_pages:
                continue
            rendered_pages.add(image.render_page_number)
        selected.append(image)
        if (
            image.image_key is None
            and image.inline_data is None
            and image.render_page_number is None
        ):
            raise ValueError(f"Could not safely decode an embedded image in {source_name}.")
    if rendered_pages:
        selected = [
            image
            for image in selected
            if image.render_page_number is not None or rendered_pages.isdisjoint(image.page_numbers)
        ]
    if len(selected) > PDF_HARD_MAX_IMAGES:
        raise PdfSafetyLimitExceeded(
            f"PDF has more than {PDF_HARD_MAX_IMAGES} significant unique images, "
            "which exceeds the hard safety limit."
        )
    decoded_image_bytes = 0
    for image in selected:
        decoded_image_bytes += image.width * image.height * 4
        if decoded_image_bytes > PDF_HARD_MAX_DECODED_IMAGE_BYTES:
            limit_mib = PDF_HARD_MAX_DECODED_IMAGE_BYTES // (1024 * 1024)
            raise PdfSafetyLimitExceeded(
                f"PDF images exceed the {limit_mib} MiB decoded-image hard safety limit."
            )
    return tuple(selected)


def _decode_pdf_images(
    candidates: Sequence[_PdfImageCandidate], source_name: str
) -> tuple[list[str], int]:
    images_b64 = []
    decoded_image_bytes = 0
    fingerprints: set[str] = set()
    render_candidates = [
        candidate for candidate in candidates if candidate.render_page_number is not None
    ]
    document = None
    if render_candidates:
        import pypdfium2

        render_path = render_candidates[0].render_path
        if render_path is None:
            raise ValueError("missing PDF page rendering source")
        document = pypdfium2.PdfDocument(str(render_path))
    try:
        for candidate in candidates:
            if candidate.render_page_number is not None:
                image_data, pil_image = _render_pdf_page(candidate, document)
            elif candidate.inline_data is not None:
                image_data, pil_image = _decode_inline_pdf_image(candidate)
            else:
                if candidate.image_key is None:
                    raise ValueError(f"Could not decode an embedded image in {source_name}.")
                image = candidate.page.images[candidate.image_key]
                image_data, pil_image = image.data, image.image
            if pil_image is None:
                raise ValueError(f"Could not decode an embedded image in {source_name}.")
            fingerprint = hashlib.sha256(image_data).hexdigest()
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            width, height = pil_image.size
            channels = max(1, len(pil_image.getbands()))
            decoded_image_bytes += width * height * channels
            if decoded_image_bytes > PDF_HARD_MAX_DECODED_IMAGE_BYTES:
                limit_mib = PDF_HARD_MAX_DECODED_IMAGE_BYTES // (1024 * 1024)
                raise PdfSafetyLimitExceeded(
                    f"PDF images exceed the {limit_mib} MiB decoded-image hard safety limit."
                )
            images_b64.append(base64.b64encode(image_data).decode())
    finally:
        if document is not None:
            document.close()
    return images_b64, decoded_image_bytes


def _render_pdf_page(candidate: _PdfImageCandidate, document):
    if candidate.render_path is None or candidate.render_page_number is None or document is None:
        raise ValueError("missing PDF page rendering source")
    page = document[candidate.render_page_number]
    try:
        bitmap = page.render(scale=1)
        try:
            pil_image = bitmap.to_pil().copy()
        finally:
            bitmap.close()
    finally:
        page.close()
    output = io.BytesIO()
    pil_image.save(output, format="PNG")
    return output.getvalue(), pil_image


def _decode_inline_pdf_image(candidate: _PdfImageCandidate):
    from pypdf._page import _INLINE_IMAGE_KEY_MAPPING
    from pypdf.generic import ArrayObject, EncodedStreamObject, NameObject
    from pypdf.generic._image_xobject import _xobj_to_image

    data = candidate.inline_data
    if data is None:
        raise ValueError("missing inline image data")
    settings = candidate.inline_settings or {}
    image_object_data: dict[str, Any] = {
        "__streamdata__": data,
        "/Length": len(data),
    }
    for raw_key, raw_value in settings.items():
        key = str(raw_key)
        if key in {"/Length", "/L"}:
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        translated = [
            _translate_inline_image_value(value, candidate.inline_resources) for value in values
        ]
        value = ArrayObject(translated) if isinstance(raw_value, list) else translated[0]
        mapped_key = NameObject(_INLINE_IMAGE_KEY_MAPPING.get(key, key))
        if mapped_key not in image_object_data:
            image_object_data[mapped_key] = value
    image_object = EncodedStreamObject.initialize_from_dictionary(image_object_data)
    _, image_data, pil_image = _xobj_to_image(image_object)
    return image_data, pil_image


def _translate_inline_image_value(value, resources):
    from pypdf._page import _INLINE_IMAGE_VALUE_MAPPING
    from pypdf.generic import NameObject

    try:
        return NameObject(_INLINE_IMAGE_VALUE_MAPPING[str(value)])
    except KeyError:
        if not isinstance(value, NameObject):
            return value
        color_spaces = _resolved(resources.get("/ColorSpace", {})) if resources else {}
        return _resolved(color_spaces[value]) if value in color_spaces else value


def _read_image(path: Path) -> FileContent:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode()
    ext = path.suffix.lstrip(".").lower()
    mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/png")
    return FileContent(
        text=f"[Image: {path.name}]",
        images_b64=[b64],
        mime_type=mime,
        source_name=path.name,
    )
