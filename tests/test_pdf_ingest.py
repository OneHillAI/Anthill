import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from anthill.inference.ollama import OllamaBackend as _OllamaBackend
from anthill.wiki import prompts
from anthill.wiki.ingest import ingest
from anthill.wiki.workspace import Workspace

_REAL_OLLAMA_CHAT = _OllamaBackend.chat


class RecordingBackend:
    model = "test-model"

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return "# Scanned invoice\nInvoice 451 was received.\n\n## Related\n[[invoices]]"


def _mock_local_vision_discovery(monkeypatch):
    class TagsResponse:
        def json(self):
            return {"models": [{"name": "granite3.2-vision:2b"}]}

    class Client:
        def __init__(self, *, trust_env):
            assert trust_env is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, *, timeout):
            assert url == "http://localhost:11434/api/tags"
            assert timeout == 5
            return TagsResponse()

    monkeypatch.setattr("anthill.routing.router.httpx.Client", Client)


def _local_vision_backend(monkeypatch):
    from anthill.inference.ollama import OllamaBackend

    backend = OllamaBackend("http://localhost:11434", "qwen2.5:3b")
    backend.calls = []
    _mock_local_vision_discovery(monkeypatch)
    return backend


def _run_worker_payload(pdf_worker, source: Path):
    """Run the worker in-process over real pipes and return its JSON payload."""
    import json
    import os

    payload_read, payload_write = os.pipe()
    noise_read, noise_write = os.pipe()
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    try:
        os.dup2(payload_write, 1)
        os.dup2(noise_write, 2)
        assert pdf_worker.main([str(source)]) == 0
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        for fd in (saved_stdout, saved_stderr, payload_write, noise_write):
            os.close(fd)
    payload = json.loads(os.read(payload_read, 1 << 20))
    os.close(payload_read)
    os.close(noise_read)
    return payload


class _FakeWorkerProcess:
    def __init__(self, output=b"", error=b"", returncode=0):
        import io

        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO(error)
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        return None


def _raise_worker_payload(output: bytes, source: Path):
    """Drive the parent's side of the worker protocol over a real subprocess result."""
    from unittest.mock import patch

    from anthill.multimodal import reader

    process = _FakeWorkerProcess(output=output)
    with patch.object(reader.subprocess, "Popen", return_value=process):
        return reader._read_pdf_bounded(source, 60.0)


def _drain_inbox_with(monkeypatch, failures: dict, files: list):
    """Drive the scheduler's real per-file inbox handling against seeded ingest failures."""
    from anthill.web import scheduler

    def ingest_raising(_ws, source, _backend, **_kwargs):
        raise failures[source]

    monkeypatch.setattr("anthill.wiki.ingest.ingest", ingest_raising)
    surfaced = []
    for f in files:
        scheduler._ingest_inbox_file(
            None,
            f,
            None,
            on_write=None,
            identity_name="scheduler",
            hook=lambda _i, _t, scope, _a: surfaced.append(scope),
        )
    return surfaced


def _scanned_pdf(path: Path) -> None:
    image_path = path.with_suffix(".png")
    image = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(image).text((10, 40), "SCANNED INVOICE 451", fill="black")
    image.save(image_path)

    canvas = Canvas(str(path), pagesize=letter)
    canvas.drawImage(str(image_path), 72, 600, width=400, height=100)
    canvas.save()


def _pdf_image_xobject(writer):
    from pypdf.generic import DecodedStreamObject, NameObject, NumberObject

    image = DecodedStreamObject()
    image.set_data(b"\x00\x00\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/ColorSpace"): NameObject("/CS1"),
        }
    )
    return writer._add_object(image)


def _pdf_page_with_image(writer, image_reference, color_space: str, repetitions: int = 1):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    page = writer.add_blank_page(width=100, height=100)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject({NameObject("/Image"): image_reference}),
            NameObject("/ColorSpace"): DictionaryObject(
                {NameObject("/CS1"): NameObject(color_space)}
            ),
        }
    )
    content = DecodedStreamObject()
    content.set_data(b" ".join([b"q 50 0 0 50 0 0 cm /Image Do Q"] * repetitions))
    page[NameObject("/Contents")] = writer._add_object(content)
    return page


def test_summary_prompt_separates_wiki_conventions_from_source_data():
    messages = prompts.summarize_source(
        "# Wiki schema\nInstructions for maintaining the wiki.",
        "results.pdf",
        "| Region | Revenue |\n| Europe | 120000 |",
    )

    assert "<wiki_conventions>" in messages[-1].content
    assert '<source_document name="results.pdf">' in messages[-1].content
    assert "Summarize only SOURCE_DOCUMENT" in messages[0].content


def test_ingest_adds_source_title_when_model_omits_h1(tmp_path):
    from anthill.common.text import first_h1

    source = tmp_path / "quarterly-report.txt"
    source.write_text("Revenue increased by eight percent.")
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()
    backend.chat = lambda messages, **kwargs: "Revenue increased by eight percent."

    page = ingest(workspace, source, backend)

    assert first_h1(page.read_text()) == "quarterly-report"


def test_ingest_records_content_addressed_raw_source(tmp_path):
    from anthill.lifecycle.version import read_provenance

    source = tmp_path / "quarterly report.txt"
    source.write_text("Revenue increased by eight percent.")
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()

    page = ingest(workspace, source, RecordingBackend())

    provenance = read_provenance(page.read_text())
    raw_source = workspace.raw / provenance["source"]
    assert raw_source.is_file()
    assert raw_source.name.startswith("quarterly report-")
    assert raw_source.read_text() == source.read_text()


@pytest.mark.parametrize("stem", ["a" * 250, "界" * 80])
def test_content_addressed_raw_source_name_fits_filesystem_component(tmp_path, stem):
    import hashlib
    import os
    import shutil

    from anthill.wiki.ingest import _raw_source_path

    source = tmp_path / f"{stem}.txt"
    source.write_text("Revenue increased by eight percent.")
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()

    raw_source = _raw_source_path(workspace, source)
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    shutil.copy2(source, raw_source)

    assert len(os.fsencode(raw_source.name)) <= os.pathconf(workspace.raw, "PC_NAME_MAX")
    assert raw_source.name.endswith(f"-{expected_hash}.txt")
    assert raw_source.read_text() == source.read_text()


def test_scanned_pdf_rejects_non_local_backend(tmp_path):
    from anthill.inference.base import BackendError

    source = tmp_path / "scan.pdf"
    _scanned_pdf(source)
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()

    with pytest.raises(BackendError, match="local Ollama"):
        ingest(workspace, source, RecordingBackend())

    assert len(list(workspace.raw.glob("*.pdf"))) == 1


def test_scanned_pdf_batches_every_image(tmp_path, monkeypatch):
    from anthill.multimodal.reader import FileContent
    from anthill.wiki.ingest import _ingest_with_vision

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = _local_vision_backend(monkeypatch)

    def chat(messages, **kwargs):
        backend.calls.append(messages)
        return "# Scanned report\nBatch facts.\n\n## Related\n[[scans]]"

    monkeypatch.setattr(backend, "chat", chat)
    images = [f"image-{index}" for index in range(9)]
    content = FileContent(
        text="[PDF: scan.pdf - no extractable text. Images: 9]",
        images_b64=images,
        mime_type="application/pdf",
        source_name="scan.pdf",
    )

    _ingest_with_vision(workspace, content, backend)

    image_calls = [call for call in backend.calls if call[-1].images]
    assert len(image_calls) == 3
    assert all(1 <= len(call[-1].images) <= 4 for call in image_calls)
    assert [image for call in image_calls for image in call[-1].images] == images
    assert backend.calls[-1][-1].images is None


def test_scanned_pdf_reduces_batch_summaries_with_bounded_prompts(tmp_path, monkeypatch):
    from anthill.multimodal.reader import FileContent
    from anthill.wiki.ingest import _ingest_with_vision

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = _local_vision_backend(monkeypatch)
    images = [f"image-{index}" for index in range(40)]
    content = FileContent(
        text="[PDF: scan.pdf - no extractable text. Images require vision extraction.]",
        images_b64=images,
        mime_type="application/pdf",
        source_name="scan.pdf",
    )

    def large_summary(messages, **kwargs):
        backend.calls.append(messages)
        return "S" * 6_000

    backend.chat = large_summary

    _ingest_with_vision(workspace, content, backend)

    image_calls = [call for call in backend.calls if call[-1].images]
    assert [image for call in image_calls for image in call[-1].images] == images
    assert all(len(call[-1].content) < 15_000 for call in backend.calls)


def test_scanned_pdf_selects_an_installed_local_vision_model(tmp_path, monkeypatch):
    from anthill.inference.ollama import OllamaBackend

    source = tmp_path / "scan.pdf"
    _scanned_pdf(source)
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = OllamaBackend("http://localhost:11434", "qwen2.5:3b")
    calls = []

    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return "# Scanned invoice\nInvoice 451 was received.\n\n## Related\n[[invoices]]"

    monkeypatch.setattr(backend, "chat", chat)
    _mock_local_vision_discovery(monkeypatch)

    page = ingest(workspace, source, backend)

    messages, kwargs = calls[0]
    assert kwargs["model"] == "granite3.2-vision:2b"
    assert kwargs["think"] is False
    assert kwargs["num_predict"] == 1_024
    assert kwargs["temperature"] == 0.0
    assert kwargs["trust_env"] is False
    assert messages[-1].images
    from anthill.lifecycle.version import read_provenance

    assert prompts.UNTRUSTED_DATA_RULE in messages[0].content
    assert "<source_document" in messages[-1].content
    provenance = read_provenance(page.read_text())
    assert provenance["model"] == "granite3.2-vision:2b"
    assert provenance["pipeline_model"] == "qwen2.5:3b"


def test_repeated_decorative_image_does_not_consume_pdf_image_limit(tmp_path):
    source = tmp_path / "branded-report.pdf"
    logo_path = tmp_path / "logo.png"
    Image.new("RGB", (20, 20), "black").save(logo_path)
    canvas = Canvas(str(source), pagesize=letter)
    for page in range(33):
        canvas.drawImage(str(logo_path), 550, 750, width=20, height=20)
        for line in range(45):
            text = f"Page {page + 1} finding {line + 1}: " + "measured operational result " * 4
            canvas.drawString(36, 730 - line * 16, text)
        canvas.showPage()
    canvas.save()

    from anthill.multimodal.reader import _inspect_pdf_structure, _select_pdf_images

    structure = _inspect_pdf_structure(source)

    assert _select_pdf_images(structure.images, source.name) == ()
    assert structure.selected_images == ()


def test_pdf_vision_rejects_remote_ollama_before_model_discovery(tmp_path, monkeypatch):
    from anthill.inference.base import BackendError
    from anthill.inference.ollama import OllamaBackend
    from anthill.multimodal.reader import FileContent
    from anthill.wiki.ingest import _ingest_with_vision

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = OllamaBackend("http://model.internal:11434", "qwen2.5:3b")
    content = FileContent(
        text="[PDF: remote.pdf - no extractable text. Images require vision extraction.]",
        images_b64=["image"],
        mime_type="application/pdf",
        source_name="remote.pdf",
    )
    monkeypatch.setattr(
        "anthill.routing.router.httpx.Client",
        lambda *args, **kwargs: pytest.fail("remote Ollama must not be queried"),
    )

    with pytest.raises(BackendError, match="loopback"):
        _ingest_with_vision(workspace, content, backend)


def test_pdf_vision_discovery_and_chat_ignore_environment_proxies(tmp_path, monkeypatch):
    from anthill.inference.ollama import OllamaBackend
    from anthill.multimodal.reader import FileContent
    from anthill.wiki.ingest import _ingest_with_vision

    clients = []
    requests = []

    class Response:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *, trust_env):
            clients.append(trust_env)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, **kwargs):
            requests.append(("GET", url, kwargs))
            return Response({"models": [{"name": "granite3.2-vision:2b"}]})

        def post(self, url, **kwargs):
            requests.append(("POST", url, kwargs))
            return Response(
                {
                    "message": {"content": "# Local scan\nOnly local visual facts."},
                    "eval_count": 4,
                }
            )

    monkeypatch.setattr("anthill.routing.router.httpx.Client", Client)
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = OllamaBackend("http://localhost:11434", "qwen2.5:3b")
    monkeypatch.setattr(
        backend,
        "chat",
        lambda messages, **kwargs: _REAL_OLLAMA_CHAT(backend, messages, **kwargs),
    )
    content = FileContent(
        text="[PDF: local.pdf - no extractable text. Images require vision extraction.]",
        images_b64=["image"],
        mime_type="application/pdf",
        source_name="local.pdf",
    )

    page, model = _ingest_with_vision(workspace, content, backend)

    assert model == "granite3.2-vision:2b"
    assert "Only local visual facts" in page
    assert clients == [False, False]
    assert requests[0][:2] == ("GET", "http://localhost:11434/api/tags")
    assert requests[1][0:2] == ("POST", "http://localhost:11434/api/chat")
    assert requests[1][2]["json"]["messages"][-1]["images"] == ["image"]


def test_pdf_backend_timeout_cause_chain_becomes_transient_limit():
    from anthill.inference.base import BackendError
    from anthill.wiki.ingest import PdfProcessingLimitExceeded, _ProcessingBudget

    class Backend:
        def chat(self, messages, **kwargs):
            import httpx

            raise BackendError("model timed out") from httpx.ReadTimeout("deadline")

    with pytest.raises(PdfProcessingLimitExceeded) as raised:
        _ProcessingBudget.start(pdf=True).chat(Backend(), [])
    assert raised.value.transient


def test_non_pdf_backend_timeout_keeps_original_error():
    from anthill.wiki.ingest import _ProcessingBudget

    class Backend:
        def chat(self, messages, **kwargs):
            raise TimeoutError("deadline")

    with pytest.raises(TimeoutError):
        _ProcessingBudget.start(pdf=False).chat(Backend(), [])


def test_pdf_vision_discovery_deadline_uses_pdf_limit_error():
    from anthill.inference.ollama import OllamaBackend
    from anthill.multimodal.reader import FileContent
    from anthill.wiki.ingest import (
        PdfProcessingLimitExceeded,
        _ProcessingBudget,
        _vision_backend_config,
    )

    backend = OllamaBackend("http://localhost:11434", "qwen2.5:3b")
    content = FileContent(
        text="[PDF: local.pdf - no extractable text. Images require vision extraction.]",
        images_b64=["image"],
        mime_type="application/pdf",
        source_name="local.pdf",
    )

    with pytest.raises(PdfProcessingLimitExceeded, match="hard safety limit"):
        _vision_backend_config(content, backend, budget=_ProcessingBudget(deadline=0.0))


def test_pdf_structure_limit_runs_before_markitdown(tmp_path):
    from anthill.multimodal.reader import PdfSafetyLimitExceeded, _read_pdf

    source = tmp_path / "too-many-pages.pdf"
    canvas = Canvas(str(source), pagesize=letter)
    for _ in range(201):
        canvas.showPage()
    canvas.save()

    with pytest.raises(PdfSafetyLimitExceeded, match="200"):
        _read_pdf(source)


@pytest.mark.parametrize(
    "returncode",
    [0xC0000044],
)
def test_pdf_worker_memory_termination_is_retryable(monkeypatch, tmp_path, returncode):
    from anthill.multimodal import reader
    from anthill.multimodal.reader import PdfSafetyLimitExceeded

    process = _FakeWorkerProcess(returncode=returncode)
    monkeypatch.setattr(reader.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(PdfSafetyLimitExceeded, match="Windows status") as raised:
        reader._read_pdf_bounded(tmp_path / "source.pdf", 60.0)
    assert raised.value.transient


def test_maintenance_quarantines_permanent_pdf_parse_failures(tmp_path, monkeypatch):
    from anthill.multimodal.reader import PdfParseError
    from anthill.wiki import agent

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    source = workspace.inbox / "broken.pdf"
    source.write_bytes(b"broken")

    def fail_ingest(*args, **kwargs):
        raise PdfParseError("invalid PDF")

    monkeypatch.setattr(agent, "ingest", fail_ingest)

    report = agent.run_maintenance(workspace, RecordingBackend())

    assert not source.exists()
    assert (workspace.inbox / "rejected" / "broken.pdf").exists()
    assert any(item.startswith("ingest-error broken.pdf") for item in report.lint_findings)


def test_pdf_worker_parser_exit_is_not_retryable(monkeypatch, tmp_path):
    from anthill.multimodal import reader

    process = _FakeWorkerProcess(error=b"parser rejected input", returncode=1)
    monkeypatch.setattr(reader.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(ValueError, match="parser rejected input"):
        reader._read_pdf_bounded(tmp_path / "source.pdf", 60.0)


def test_pdf_worker_applies_hard_address_space_limit(monkeypatch):
    import sys
    from types import SimpleNamespace

    from anthill.multimodal.pdf_worker import _apply_memory_limit
    from anthill.multimodal.reader import PDF_HARD_WORKER_MEMORY_BYTES

    calls = []
    fake_resource = SimpleNamespace(
        RLIMIT_AS=9,
        RLIM_INFINITY=-1,
        getrlimit=lambda kind: (-1, -1),
        setrlimit=lambda kind, limits: calls.append((kind, limits)),
    )
    monkeypatch.setitem(sys.modules, "resource", fake_resource)

    baseline = 256 * 1024 * 1024
    _apply_memory_limit(lambda: baseline)

    expected = baseline + PDF_HARD_WORKER_MEMORY_BYTES
    assert calls == [(9, (expected, expected))]


def test_pdf_worker_applies_windows_job_memory_limit(monkeypatch):
    import ctypes

    from anthill.multimodal import pdf_worker
    from anthill.multimodal.reader import PDF_HARD_WORKER_MEMORY_BYTES

    class Function:
        def __init__(self, implementation):
            self.implementation = implementation

        def __call__(self, *args):
            return self.implementation(*args)

    baseline = 256 * 1024 * 1024
    configured = []

    def get_process_memory_info(process, counters_pointer, size):
        counters = ctypes.cast(
            counters_pointer, ctypes.POINTER(pdf_worker._WindowsProcessMemoryCounters)
        ).contents
        assert process == 123
        assert size == ctypes.sizeof(counters)
        counters.PrivateUsage = baseline
        return 1

    def set_job_limit(job, information_class, limits_pointer, size):
        limits = ctypes.cast(
            limits_pointer, ctypes.POINTER(pdf_worker._WindowsJobExtendedLimitInformation)
        ).contents
        configured.append(
            (
                job,
                information_class,
                limits.BasicLimitInformation.LimitFlags,
                limits.ProcessMemoryLimit,
                size,
            )
        )
        return 1

    kernel32 = type(
        "Kernel32",
        (),
        {
            "CreateJobObjectW": Function(lambda security, name: 456),
            "SetInformationJobObject": Function(set_job_limit),
            "AssignProcessToJobObject": Function(
                lambda job, process: job == 456 and process == 123
            ),
            "GetCurrentProcess": Function(lambda: 123),
            "CloseHandle": Function(lambda handle: 1),
        },
    )()
    psapi = type("Psapi", (), {"GetProcessMemoryInfo": Function(get_process_memory_info)})()
    monkeypatch.setattr(pdf_worker, "_WINDOWS_JOB_HANDLE", None)

    pdf_worker._apply_windows_memory_limit(kernel32, psapi)

    assert configured == [
        (
            456,
            pdf_worker._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            pdf_worker._JOB_OBJECT_LIMIT_PROCESS_MEMORY,
            baseline + PDF_HARD_WORKER_MEMORY_BYTES,
            ctypes.sizeof(pdf_worker._WindowsJobExtendedLimitInformation),
        )
    ]
    assert pdf_worker._WINDOWS_JOB_HANDLE == 456


def test_pdf_worker_fails_closed_when_windows_job_limit_cannot_be_installed(monkeypatch):
    from anthill.multimodal import pdf_worker
    from anthill.multimodal.reader import PdfSafetyLimitExceeded

    monkeypatch.setattr(pdf_worker.sys, "platform", "win32")

    def fail_to_install():
        raise OSError("job unavailable")

    monkeypatch.setattr(pdf_worker, "_apply_windows_memory_limit", fail_to_install)

    with pytest.raises(PdfSafetyLimitExceeded, match="cannot establish"):
        pdf_worker._apply_memory_limit()


def test_distinct_byte_identical_xobjects_share_pdf_image_limit(tmp_path):
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.utils import ImageReader

    from anthill.multimodal.reader import _inspect_pdf_structure

    image = Image.new("RGB", (400, 100), "white")
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    image_payload = image_bytes.getvalue()
    writer = PdfWriter()
    for _ in range(33):
        page_bytes = BytesIO()
        canvas = Canvas(page_bytes, pagesize=letter)
        canvas.drawImage(ImageReader(BytesIO(image_payload)), 72, 600, width=400, height=100)
        canvas.save()
        writer.add_page(PdfReader(BytesIO(page_bytes.getvalue())).pages[0])
    source = tmp_path / "repeated-letterhead.pdf"
    with source.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(source)

    assert len(structure.images) == 1
    assert len(structure.images[0].page_numbers) == 33


def test_raster_identity_includes_contextual_color_space(tmp_path):
    from pypdf import PdfWriter

    from anthill.multimodal.reader import _inspect_pdf_structure

    writer = PdfWriter()
    image_reference = _pdf_image_xobject(writer)
    _pdf_page_with_image(writer, image_reference, "/DeviceRGB")
    _pdf_page_with_image(writer, image_reference, "/DeviceGray")
    source = tmp_path / "contextual-color-spaces.pdf"
    with source.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(source)

    assert len(structure.images) == 2
    assert structure.images[0].identity != structure.images[1].identity


def test_reused_indirect_xobject_hashes_raster_content_once(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    from anthill.multimodal import reader

    writer = PdfWriter()
    image_reference = _pdf_image_xobject(writer)
    _pdf_page_with_image(writer, image_reference, "/DeviceRGB", repetitions=64)
    source = tmp_path / "reused-image.pdf"
    with source.open("wb") as stream:
        writer.write(stream)
    hashed_bytes = 0
    original = reader._update_pdf_fingerprint_bytes

    def count_bytes(digest, data):
        nonlocal hashed_bytes
        hashed_bytes += len(data)
        original(digest, data)

    monkeypatch.setattr(reader, "_update_pdf_fingerprint_bytes", count_bytes)

    structure = reader._inspect_pdf_structure(source)

    assert len(structure.images) == 1
    assert hashed_bytes == 3


def test_pdf_image_occurrence_limit_is_independent_of_unique_images(monkeypatch):
    from anthill.multimodal import reader

    state = reader._PdfInspectionState(image_occurrences=2)
    monkeypatch.setattr(reader, "PDF_HARD_MAX_IMAGE_OCCURRENCES", 2)

    with pytest.raises(reader.PdfSafetyLimitExceeded, match="image occurrences"):
        reader._record_pdf_image_occurrence(state)


def test_mixed_pdf_recovers_fact_present_only_in_raster_diagram(tmp_path, monkeypatch):
    from anthill.multimodal.reader import read_file

    source = Path(__file__).parents[1] / "benchmarks" / "pdf" / "mixed-diagram.pdf"
    assert "Queue" not in read_file(source).text

    backend = _local_vision_backend(monkeypatch)

    def chat(messages, **kwargs):
        backend.calls.append(messages)
        content = messages[-1].content
        if messages[-1].images:
            return "# Deployment topology\nQueue routes work through Worker to Archive."
        if "Queue routes work" in content:
            return "# Deployment topology\nQueue routes work through Worker to Archive."
        return "# Deployment topology\nThe report contains a component diagram."

    backend.chat = chat
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()

    page = ingest(workspace, source, backend)

    assert "Queue routes work through Worker to Archive" in page.read_text()
    assert any(call[-1].images for call in backend.calls)
    assert "QUEUE" not in next(
        call[-1].content for call in backend.calls if call[-1].images is None
    )


def test_mixed_pdf_fails_before_text_inference_without_local_vision(tmp_path):
    from anthill.inference.base import BackendError

    source = Path(__file__).parents[1] / "benchmarks" / "pdf" / "mixed-diagram.pdf"
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    with pytest.raises(BackendError, match="local Ollama"):
        ingest(workspace, source, backend)

    assert backend.calls == []
    assert len(list(workspace.raw.glob("*.pdf"))) == 1


def test_mixed_pdf_keeps_visual_facts_when_final_reducer_omits_them(tmp_path, monkeypatch):
    from anthill.multimodal.reader import FileContent, PdfPreflight

    source = tmp_path / "mixed.pdf"
    source.write_bytes(b"valid-pdf-placeholder")
    content = FileContent(
        text="Quarterly note: West revenue was 500 EUR.",
        images_b64=["chart-image"],
        mime_type="application/pdf",
        source_name=source.name,
        pdf_preflight=PdfPreflight(
            page_count=1,
            image_count=1,
            decoded_image_bytes=1_000_000,
            vision_required=True,
            source_bytes=100,
        ),
    )
    monkeypatch.setattr("anthill.wiki.ingest.read_file", lambda path, **kwargs: content)
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = _local_vision_backend(monkeypatch)

    def chat(messages, **kwargs):
        if messages[-1].images:
            return "# East revenue\nThe chart records East revenue as 777 EUR."
        if "PART 2" in messages[-1].content:
            return (
                "# West revenue\nWest revenue was 500 EUR.\n\n## Related\n"
                "[[revenue]]\n# Leaked reducer detail\nshould not remain"
            )
        return "# West revenue\nWest revenue was 500 EUR."

    monkeypatch.setattr(backend, "chat", chat)

    page = ingest(workspace, source, backend)
    written = page.read_text()

    assert "West revenue was 500 EUR" in written
    assert "East revenue as 777 EUR" in written
    assert "## Visual facts" in written
    assert "should not remain" not in written


def test_long_pdf_reduces_large_chunk_summaries_in_bounded_groups(tmp_path):
    source = tmp_path / "very-long-report.pdf"
    canvas = Canvas(str(source), pagesize=letter)
    for page in range(8):
        for line in range(45):
            canvas.drawString(36, 750 - line * 16, f"Finding {page}-{line}: " + "evidence " * 20)
        canvas.showPage()
    canvas.save()
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    def large_summary(messages, **kwargs):
        backend.calls.append(messages)
        return "S" * 6_000

    backend.chat = large_summary

    ingest(workspace, source, backend)

    assert all(len(call[-1].content) < 15_000 for call in backend.calls)


def test_long_pdf_is_summarized_in_bounded_chunks(tmp_path):
    source = tmp_path / "long-report.pdf"
    canvas = Canvas(str(source), pagesize=letter)
    for page in range(10):
        for line in range(45):
            text = f"Page {page + 1} finding {line + 1}: " + "measured operational result " * 4
            canvas.drawString(36, 750 - line * 16, text)
        canvas.showPage()
    canvas.save()
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    ingest(workspace, source, backend)

    assert len(backend.calls) > 2
    assert all(len(call[-1].content) < 15_000 for call in backend.calls)


def test_failed_source_validation_does_not_copy_raw_source(tmp_path, monkeypatch):
    source = tmp_path / "invalid.pdf"
    source.write_bytes(b"not a pdf")
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()

    def reject_source(path, **kwargs):
        raise ValueError(f"invalid source: {path.name}")

    monkeypatch.setattr("anthill.wiki.ingest.read_file", reject_source)

    with pytest.raises(ValueError, match="invalid source"):
        ingest(workspace, source, RecordingBackend())

    assert list(workspace.raw.iterdir()) == []


def test_valid_source_is_preserved_before_inference_failure(tmp_path):
    source = tmp_path / "validated.txt"
    source.write_text("Validated source content.")
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    def fail(messages, **kwargs):
        raise RuntimeError("inference unavailable")

    backend.chat = fail

    with pytest.raises(RuntimeError, match="inference unavailable"):
        ingest(workspace, source, backend)

    raw_source = next(workspace.raw.glob("*.txt"))
    assert raw_source.read_text() == source.read_text()


def test_pdf_preflight_requires_confirmation_before_model_calls(tmp_path, monkeypatch):
    from anthill.multimodal.reader import FileContent, PdfPreflight
    from anthill.wiki.ingest import PdfConfirmationRequired

    source = tmp_path / "large.pdf"
    source.write_bytes(b"validated PDF")
    content = FileContent(
        text="Structured report.",
        mime_type="application/pdf",
        pdf_preflight=PdfPreflight(
            page_count=41,
            image_count=0,
            decoded_image_bytes=0,
            vision_required=False,
        ),
    )
    monkeypatch.setattr("anthill.wiki.ingest.read_file", lambda path, **kwargs: content)
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    with pytest.raises(PdfConfirmationRequired, match="41 pages"):
        ingest(workspace, source, backend)

    assert backend.calls == []
    assert len(list(workspace.raw.glob("*.pdf"))) == 1

    ingest(workspace, source, backend, allow_large_pdf=True)

    assert len(backend.calls) == 1
    assert len(list(workspace.raw.glob("*.pdf"))) == 1


def test_pdf_preflight_rejects_hard_model_call_limit_without_partial_inference(
    tmp_path, monkeypatch
):
    from anthill.multimodal.reader import FileContent, PdfPreflight
    from anthill.wiki.ingest import PdfProcessingLimitExceeded

    source = tmp_path / "unsafe.pdf"
    source.write_bytes(b"validated PDF")
    text = "\n\n".join("x" * 10_000 for _ in range(33))
    content = FileContent(
        text=text,
        mime_type="application/pdf",
        pdf_preflight=PdfPreflight(
            page_count=1,
            image_count=0,
            decoded_image_bytes=0,
            vision_required=False,
        ),
    )
    monkeypatch.setattr("anthill.wiki.ingest.read_file", lambda path, **kwargs: content)
    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = RecordingBackend()

    with pytest.raises(PdfProcessingLimitExceeded, match="hard safety limit"):
        ingest(workspace, source, backend, allow_large_pdf=True)

    assert backend.calls == []
    assert len(list(workspace.raw.glob("*.pdf"))) == 1


def test_standalone_image_reports_image_vision_requirement():
    from anthill.inference.base import BackendError
    from anthill.multimodal.reader import FileContent
    from anthill.wiki.ingest import _vision_backend_config

    with pytest.raises(BackendError, match="Images require a local Ollama vision model"):
        _vision_backend_config(FileContent(text="", mime_type="image/png"), RecordingBackend())


def test_pdf_worker_payload_survives_stray_writes_to_stdout(tmp_path, monkeypatch):
    """Native parsers under markitdown can write to fd 1 directly. The JSON payload
    must reach the parent uncorrupted, and the noise must land on stderr."""
    import json
    import os

    from anthill.multimodal import pdf_worker
    from anthill.multimodal.reader import FileContent

    def noisy_read(path):
        os.write(1, b"pdfium: native diagnostic\n")
        return FileContent(text="clean text", mime_type="application/pdf", source_name=path.name)

    monkeypatch.setattr(pdf_worker, "_apply_memory_limit", lambda: None)
    monkeypatch.setattr(pdf_worker, "_read_pdf", noisy_read)

    payload_read, payload_write = os.pipe()
    noise_read, noise_write = os.pipe()
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    try:
        os.dup2(payload_write, 1)
        os.dup2(noise_write, 2)
        exit_code = pdf_worker.main([str(tmp_path / "source.pdf")])
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        for fd in (saved_stdout, saved_stderr, payload_write, noise_write):
            os.close(fd)

    assert exit_code == 0
    payload = json.loads(os.read(payload_read, 1 << 20))
    os.close(payload_read)
    assert payload["status"] == "ok"
    assert payload["payload"]["text"] == "clean text"
    assert b"pdfium: native diagnostic" in os.read(noise_read, 1 << 20)
    os.close(noise_read)


def test_mixed_pdf_merges_with_the_configured_text_model(tmp_path, monkeypatch):
    """The vision model extracts visual facts; the configured text model authors the
    merged page under its normal bounded reduction settings."""
    from anthill.multimodal.reader import FileContent, PdfPreflight
    from anthill.wiki.ingest import _ProcessingBudget, _source_content_to_page

    workspace = Workspace(tmp_path / "wiki")
    workspace.init()
    backend = _local_vision_backend(monkeypatch)

    calls = []

    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        if messages[-1].images:
            return "# Diagram\nThe chart shows 40% growth.\n\n## Related\n[[charts]]"
        return "# Report\nRevenue rose.\n\n## Related\n[[reports]]"

    monkeypatch.setattr(backend, "chat", chat)
    content = FileContent(
        text="Revenue rose sharply across every region this quarter.",
        images_b64=["image-0"],
        mime_type="application/pdf",
        source_name="report.pdf",
        pdf_preflight=PdfPreflight(
            page_count=4,
            image_count=1,
            decoded_image_bytes=1024,
            vision_required=True,
        ),
    )

    page, writer_model = _source_content_to_page(
        workspace,
        tmp_path / "report.pdf",
        content,
        backend,
        budget=_ProcessingBudget.start(),
    )

    merge_kwargs = calls[-1][1]
    assert merge_kwargs.get("model") != "granite3.2-vision:2b"
    assert "num_predict" not in merge_kwargs
    assert writer_model == "qwen2.5:3b"
    image_kwargs = [kwargs for messages, kwargs in calls if messages[-1].images]
    assert image_kwargs and all(k["model"] == "granite3.2-vision:2b" for k in image_kwargs)
    assert "## Visual facts" in page


def test_worker_transient_failure_reaches_the_parent_as_retryable(tmp_path, monkeypatch):
    """A host-level failure inside the worker must keep its transient marking across the
    JSON boundary, so the caller retries the file instead of rejecting it."""
    import json

    from anthill.multimodal import pdf_worker
    from anthill.multimodal.reader import PdfSafetyLimitExceeded

    def cannot_establish_limit():
        raise PdfSafetyLimitExceeded(
            "PDF parsing cannot establish the worker memory safety limit.", transient=True
        )

    monkeypatch.setattr(pdf_worker, "_apply_memory_limit", cannot_establish_limit)
    payload = _run_worker_payload(pdf_worker, tmp_path / "any.pdf")
    assert payload["status"] == "safety-transient"

    with pytest.raises(PdfSafetyLimitExceeded) as raised:
        _raise_worker_payload(json.dumps(payload).encode(), tmp_path / "any.pdf")
    assert raised.value.transient is True


def test_worker_structural_failure_reaches_the_parent_as_deterministic(tmp_path, monkeypatch):
    """A limit that is a property of the file must arrive un-marked, so it is rejected
    rather than retried forever."""
    import json

    from anthill.multimodal import pdf_worker
    from anthill.multimodal.reader import PdfSafetyLimitExceeded

    def too_many_pages(path):
        raise PdfSafetyLimitExceeded("PDF has 500 pages; the hard safety limit is 200.")

    monkeypatch.setattr(pdf_worker, "_apply_memory_limit", lambda: None)
    monkeypatch.setattr(pdf_worker, "_read_pdf", too_many_pages)
    payload = _run_worker_payload(pdf_worker, tmp_path / "long.pdf")
    assert payload["status"] == "safety"

    with pytest.raises(PdfSafetyLimitExceeded) as raised:
        _raise_worker_payload(json.dumps(payload).encode(), tmp_path / "long.pdf")
    assert raised.value.transient is False


def test_transient_pdf_failure_is_retried_and_structural_one_is_rejected(tmp_path, monkeypatch):
    """The scheduler quarantines a file only when retrying it could never succeed."""
    from anthill.wiki.ingest import PdfProcessingLimitExceeded

    inbox = tmp_path / "inbox"
    inbox.mkdir()

    transient = inbox / "host-broke.pdf"
    transient.write_bytes(b"%PDF-1.4")
    structural = inbox / "too-long.pdf"
    structural.write_bytes(b"%PDF-1.4")
    failures = {
        transient: PdfProcessingLimitExceeded("worker memory safety limit.", transient=True),
        structural: PdfProcessingLimitExceeded("500 pages; hard safety limit is 200."),
    }
    surfaced = _drain_inbox_with(monkeypatch, failures, sorted(failures))

    assert transient.exists()  # still queued: the next tick retries it
    assert not (inbox / "rejected" / "host-broke.pdf").exists()
    assert not structural.exists()
    assert (inbox / "rejected" / "too-long.pdf").exists()
    assert any("retrying" in scope and "host-broke.pdf" in scope for scope in surfaced)
    assert any("rejected" in scope and "too-long.pdf" in scope for scope in surfaced)


def test_pdf_hard_rejection_preserves_raw_source(tmp_path, monkeypatch):
    import anthill.wiki.ingest as ingest_module
    from anthill.multimodal.reader import PdfSafetyLimitExceeded
    from anthill.wiki.ingest import PdfProcessingLimitExceeded

    workspace = Workspace(tmp_path / "wiki")
    source = tmp_path / "oversize.pdf"
    source.write_bytes(b"%PDF-1.4 oversized")
    monkeypatch.setattr(
        ingest_module,
        "read_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PdfSafetyLimitExceeded("source exceeds limit")
        ),
    )

    with pytest.raises(PdfProcessingLimitExceeded):
        ingest_module.ingest(workspace, source, RecordingBackend())
    assert list(workspace.raw.glob("*.pdf"))


def test_pdf_backend_timeout_is_transient():
    from anthill.wiki.ingest import PdfProcessingLimitExceeded, _ProcessingBudget

    class Backend:
        def chat(self, messages, **kwargs):
            raise TimeoutError("deadline")

    with pytest.raises(PdfProcessingLimitExceeded) as raised:
        _ProcessingBudget(time.monotonic() + 10).chat(Backend(), [])
    assert raised.value.transient


def test_malformed_pdf_is_set_aside(tmp_path, monkeypatch):
    from anthill.multimodal.reader import PdfParseError
    from anthill.web import scheduler

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "broken.pdf"
    source.write_bytes(b"bad")
    monkeypatch.setattr(
        "anthill.wiki.ingest.ingest",
        lambda *args, **kwargs: (_ for _ in ()).throw(PdfParseError("invalid PDF")),
    )
    surfaced = []
    scheduler._ingest_inbox_file(
        None,
        source,
        None,
        on_write=None,
        identity_name="scheduler",
        hook=lambda _i, _t, scope, _a: surfaced.append(scope),
    )
    assert not source.exists()
    assert (inbox / "rejected" / "broken.pdf").exists()
    assert surfaced


def test_set_aside_does_not_delete_existing_artifact(tmp_path):
    from anthill.web import scheduler

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "same.pdf"
    source.write_bytes(b"new")
    rejected = inbox / "rejected"
    rejected.mkdir()
    (rejected / "same.pdf").write_bytes(b"old")
    scheduler._set_aside(source, "rejected", "scheduler", lambda *args: None, "invalid")
    assert (rejected / "same.pdf").read_bytes() == b"old"
    assert (rejected / "same-1.pdf").read_bytes() == b"new"
