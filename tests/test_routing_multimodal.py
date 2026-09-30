"""Model-free tests for task routing, multimodal reader/writer, and search."""

import pytest

from anthill.routing.router import CATALOGUE, TaskRouter, TaskType, classify

# ── task classifier ──────────────────────────────────────────────────────────


def test_classify_code():
    assert classify("write a python function to sort a list") == TaskType.CODE


def test_classify_reason():
    assert (
        classify("why did we choose postgres over mysql, compare the tradeoffs") == TaskType.REASON
    )


def test_classify_arithmetic_routes_to_reason():
    # A short arithmetic ask (incl. the multi-turn "multiply it by 6") must go to REASON, not FAST -
    # the small FAST model gets these wrong (7 x 6 -> 36).
    for q in [
        "Multiply it by 6",
        "divide 100 by 4",
        "subtract 12 from 30",
        "what is the square root of 144",
    ]:
        assert classify(q) == TaskType.REASON, q


def test_classify_arithmetic_words_do_not_over_trigger_reason():
    # common non-math uses of nearby words must NOT route to REASON
    assert classify("add a user to the team") != TaskType.REASON
    assert classify("times up everyone") != TaskType.REASON


def test_classify_vision():
    assert classify("what is in this diagram", has_image=True) == TaskType.VISION


def test_classify_create():
    assert classify("draft a report on our Q2 architecture decisions") == TaskType.CREATE


def test_classify_fast():
    # Short query → FAST
    assert classify("what is kafka?") == TaskType.FAST


def test_classify_general_fallback():
    # A longer query with no specific keywords → GENERAL
    long_q = (
        "tell me about the billing database we chose and give me full context "
        "about all the options we considered and the final rationale behind it"
    )
    assert classify(long_q) == TaskType.GENERAL


# ── router (offline - no Ollama required) ────────────────────────────────────


def test_router_falls_back_when_nothing_installed(monkeypatch):
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: set())
    tag = router.pick(TaskType.GENERAL, fallback_tag="qwen2.5:3b")
    assert tag == "qwen2.5:3b"


def test_router_picks_installed_model(monkeypatch):
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: {"qwen3:8b", "qwen2.5:3b"})
    tag = router.pick(TaskType.GENERAL)
    assert tag == "qwen3:8b"  # qwen3:8b is preferred over qwen2.5:3b for GENERAL


def test_router_code_prefers_dedicated_coder(monkeypatch):
    router = TaskRouter()
    monkeypatch.setattr(
        router, "_installed_models", lambda: {"qwen2.5-coder:7b", "deepseek-r1:8b", "qwen3:8b"}
    )
    assert router.pick(TaskType.CODE) == "qwen2.5-coder:7b"  # coder beats reasoner for code


def test_router_code_falls_back_to_reasoner_without_coder(monkeypatch):
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: {"deepseek-r1:8b", "qwen3:8b"})
    assert router.pick(TaskType.CODE) == "deepseek-r1:8b"  # coder absent → next preference


def test_router_never_returns_an_uninstalled_family_tag(monkeypatch):
    # The 404 bug: pref wants qwen3:14b but only qwen3:8b is installed. pick() must return the
    # INSTALLED tag, never the requested-but-absent size (which makes Ollama 404 "model not found").
    router = TaskRouter()
    installed = {"qwen3:8b", "qwen2.5:3b"}  # note: qwen3:14b, deepseek-r1 NOT installed
    monkeypatch.setattr(router, "_installed_models", lambda: installed)
    for task in (TaskType.DOCUMENT, TaskType.REASON, TaskType.CREATE):
        tag = router.pick(task)
        assert tag in installed, f"{task} -> {tag} is not installed (would 404)"


def test_router_vision_prefers_the_licence_clean_default(monkeypatch):
    # VISION default preference leads with the non-Chinese, licence-clean Granite model; with it
    # installed the router picks it directly (never the removed non-commercial qwen2.5vl:3b).
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: {"granite3.2-vision:2b"})
    assert router.pick(TaskType.VISION) == "granite3.2-vision:2b"


def test_router_accepts_an_installed_model_with_vision_capability(monkeypatch):
    class TagsResponse:
        def json(self):
            return {
                "models": [
                    {"name": "qwen2.5:3b"},
                    {"name": "qwen3.5:9b"},
                ]
            }

    class ShowResponse:
        def __init__(self, capabilities):
            self._capabilities = capabilities

        def json(self):
            return {"capabilities": self._capabilities}

    def show_model(url, *, json, timeout):
        assert url.endswith("/api/show")
        assert timeout == 5
        capabilities = ["vision", "completion"] if json["model"] == "qwen3.5:9b" else []
        return ShowResponse(capabilities)

    monkeypatch.setattr("anthill.routing.router.httpx.get", lambda *args, **kwargs: TagsResponse())
    monkeypatch.setattr("anthill.routing.router.httpx.post", show_model)

    assert TaskRouter().pick(TaskType.VISION, fallback_tag="") == "qwen3.5:9b"


def test_router_caps_cumulative_capability_discovery_to_deadline(monkeypatch):
    from anthill.routing import router as router_module

    class ShowResponse:
        def json(self):
            return {"capabilities": []}

    clock = [0.0]
    timeouts = []

    def show_model(url, *, json, timeout):
        timeouts.append(timeout)
        clock[0] += 0.5 if len(timeouts) == 1 else 0.4
        return ShowResponse()

    monkeypatch.setattr(router_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(router_module.httpx, "post", show_model)
    router = TaskRouter(deadline=0.8)
    router._available = {"custom-a", "custom-b", "custom-c"}

    with pytest.raises(router_module.RoutingDeadlineExceeded):
        router.pick(TaskType.VISION, fallback_tag="")

    assert len(timeouts) == 2
    assert timeouts[0] == pytest.approx(0.8)
    assert timeouts[1] == pytest.approx(0.3)


def test_router_prefers_clean_default_over_installed_chinese_model(monkeypatch):
    # Policy: the licence-clean, non-Chinese default wins. A Chinese-origin vision model is never
    # preferred by default - it is at most a last-resort capability fallback (see the vision policy).
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: {"granite3.2-vision:2b", "qwen3.5:9b"})
    monkeypatch.setattr(router, "_model_capabilities", lambda tag: {"vision", "completion"})

    assert router.pick(TaskType.VISION, fallback_tag="") == "granite3.2-vision:2b"


def test_router_rejects_remote_vision_model_metadata(monkeypatch):
    class TagsResponse:
        def json(self):
            return {
                "models": [
                    {
                        "name": "qwen2.5vl:3b",
                        "remote_host": "https://ollama.com",
                        "remote_model": "qwen2.5vl:3b",
                    },
                    {"name": "qwen3.5:9b"},
                ]
            }

    class ShowResponse:
        def json(self):
            return {"capabilities": ["vision", "completion"]}

    monkeypatch.setattr("anthill.routing.router.httpx.get", lambda *args, **kwargs: TagsResponse())
    monkeypatch.setattr("anthill.routing.router.httpx.post", lambda *args, **kwargs: ShowResponse())

    assert TaskRouter(allow_cloud=False).pick(TaskType.VISION, fallback_tag="") == "qwen3.5:9b"


def test_router_rejects_remote_same_family_cloud_tag(monkeypatch):
    class TagsResponse:
        def json(self):
            return {
                "models": [
                    {
                        "name": "qwen2.5vl:9b-cloud",
                        "remote_host": "https://ollama.com",
                    }
                ]
            }

    monkeypatch.setattr("anthill.routing.router.httpx.get", lambda *args, **kwargs: TagsResponse())

    assert TaskRouter(allow_cloud=False).pick(TaskType.VISION, fallback_tag="") == ""


def test_router_skips_cloud_without_permission(monkeypatch):
    router = TaskRouter(allow_cloud=False)
    monkeypatch.setattr(router, "_installed_models", lambda: set())
    tag = router.pick(TaskType.REASON, fallback_tag="qwen2.5:3b")
    assert "cloud" not in tag


def test_router_allows_cloud_when_opted_in(monkeypatch):
    router = TaskRouter(allow_cloud=True)
    # Only cloud model available
    monkeypatch.setattr(router, "_installed_models", lambda: set())
    # With nothing installed, falls back; cloud model has no local install
    tag = router.pick(TaskType.REASON, fallback_tag="kimi-k2.6:cloud")
    # Fallback is returned since cloud is "available" only as fallback
    assert tag == "kimi-k2.6:cloud"


def test_catalogue_has_no_duplicate_tags():
    tags = [s.tag for s in CATALOGUE]
    assert len(tags) == len(set(tags))


def test_all_catalogue_cloud_models_flagged():
    for spec in CATALOGUE:
        if "cloud" in spec.tag:
            assert spec.cloud, f"{spec.tag} has 'cloud' in tag but cloud=False"


# ── multimodal reader ────────────────────────────────────────────────────────


def test_nested_form_inline_image_decodes_after_preflight(tmp_path):
    import base64
    from io import BytesIO

    from PIL import Image
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
    )

    from anthill.multimodal.reader import (
        _decode_pdf_images,
        _inspect_pdf_structure,
        _select_pdf_images,
    )

    pdf = tmp_path / "nested-inline.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    form = DecodedStreamObject()
    form.set_data(b"q 80 0 0 80 10 10 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00 EI Q")
    form.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject(
                [FloatObject(0), FloatObject(0), FloatObject(100), FloatObject(100)]
            ),
            NameObject("/Resources"): DictionaryObject(),
        }
    )
    form_reference = writer._add_object(form)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/NestedForm"): form_reference})}
    )
    content = DecodedStreamObject()
    content.set_data(b"/NestedForm Do")
    page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(pdf)
    selected = _select_pdf_images(structure.images, pdf.name, include_all=True)
    images, decoded_bytes = _decode_pdf_images(selected, pdf.name)

    assert len(selected) == 1
    assert selected[0].image_key is None
    assert decoded_bytes == 3
    with Image.open(BytesIO(base64.b64decode(images[0]))) as decoded:
        assert decoded.size == (1, 1)
        assert decoded.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


def test_repeated_inline_images_deduplicate_across_pages(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from anthill.multimodal.reader import _inspect_pdf_structure, _select_pdf_images

    pdf = tmp_path / "repeated-inline.pdf"
    writer = PdfWriter()
    for _ in range(33):
        page = writer.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(
            b"BT (Page text) Tj ET q 30 0 0 30 5 5 cm "
            b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00 EI Q"
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(pdf)

    assert len(structure.images) == 1
    assert len(structure.images[0].page_numbers) == 33
    assert _select_pdf_images(structure.images, pdf.name) == ()


def test_native_vector_chart_renders_selected_page_for_vision(tmp_path):
    import base64
    from io import BytesIO

    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import read_file

    pdf = tmp_path / "vector-chart.pdf"
    canvas = Canvas(str(pdf), pagesize=(400, 400))
    canvas.drawString(40, 360, "Quarterly revenue")
    canvas.rect(50, 80, 280, 220, stroke=1, fill=0)
    canvas.line(80, 110, 160, 250)
    canvas.line(160, 250, 290, 150)
    canvas.drawString(70, 90, "East")
    canvas.drawString(270, 130, "West")
    canvas.save()

    content = read_file(pdf)

    assert content.pdf_preflight.vision_required is True
    assert content.pdf_preflight.image_count == 1
    with Image.open(BytesIO(base64.b64decode(content.images_b64[0]))) as rendered:
        assert rendered.size == (400, 400)


def test_repeated_small_vector_form_is_treated_as_decorative(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import _inspect_pdf_structure, _select_pdf_images

    pdf = tmp_path / "vector-logo.pdf"
    canvas = Canvas(str(pdf), pagesize=(400, 400))
    canvas.beginForm("logo", 0, 0, 20, 20)
    canvas.rect(1, 1, 18, 18, stroke=1, fill=0)
    canvas.line(2, 2, 18, 18)
    canvas.endForm()
    for page_number in range(33):
        canvas.drawString(40, 360, f"Page {page_number + 1} findings")
        canvas.saveState()
        canvas.translate(370, 370)
        canvas.doForm("logo")
        canvas.restoreState()
        canvas.showPage()
    canvas.save()

    structure = _inspect_pdf_structure(pdf)

    vector_images = [image for image in structure.images if image.render_page_number is not None]
    assert len(vector_images) == 1
    assert len(vector_images[0].page_numbers) == 33
    assert _select_pdf_images(structure.images, pdf.name) == ()


def test_identical_native_vector_pages_deduplicate_before_rendering(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from anthill.multimodal.reader import (
        _decode_pdf_images,
        _inspect_pdf_structure,
        _select_pdf_images,
    )

    pdf = tmp_path / "repeated-vector-pages.pdf"
    writer = PdfWriter()
    for _ in range(2):
        page = writer.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(b"10 10 80 80 re S")
        page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(pdf)
    selected = _select_pdf_images(structure.images, pdf.name, include_all=True)
    images, _ = _decode_pdf_images(selected, pdf.name)

    assert len(structure.images) == 1
    assert len(structure.images[0].page_numbers) == 2
    assert len(selected) == 1
    assert len(images) == 1


def test_native_vector_pages_deduplicate_independently_of_text(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from anthill.multimodal.reader import _inspect_pdf_structure, _select_pdf_images

    pdf = tmp_path / "text-varying-vector-pages.pdf"
    writer = PdfWriter()
    for page_number in range(33):
        page = writer.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(
            f"BT /F{page_number} 12 Tf 1 0 0 1 {page_number} 90 Tm "
            f"(Page {page_number}) Tj ET 2 w 10 10 80 80 re S".encode()
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(pdf)
    selected = _select_pdf_images(structure.images, pdf.name)

    assert len(structure.images) == 1
    assert structure.images[0].page_numbers == set(range(33))
    assert len(selected) == 1


def test_native_vector_pages_deduplicate_independently_of_marked_content(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from anthill.multimodal.reader import _inspect_pdf_structure, _select_pdf_images

    pdf = tmp_path / "tagged-vector-pages.pdf"
    writer = PdfWriter()
    for page_number in range(33):
        page = writer.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(
            f"/Artifact{page_number} BMC EMC /Span << /MCID {page_number} >> DP "
            f"/Span << /MCID {page_number} >> BDC "
            f"BT (Page {page_number}) Tj ET EMC 2 w 10 10 80 80 re S".encode()
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(pdf)
    selected = _select_pdf_images(structure.images, pdf.name)

    assert len(structure.images) == 1
    assert structure.images[0].page_numbers == set(range(33))
    assert len(selected) == 1


def test_native_vector_pages_keep_optional_content_in_drawing_identity(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from anthill.multimodal.reader import _inspect_pdf_structure

    pdf = tmp_path / "optional-content-vector-pages.pdf"
    writer = PdfWriter()
    for layer in ("VisibleLayer", "HiddenLayer"):
        page = writer.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(f"/OC /{layer} BDC 10 10 80 80 re S EMC".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    structure = _inspect_pdf_structure(pdf)

    assert len(structure.images) == 2


def test_reused_vector_form_renders_its_most_significant_occurrence(tmp_path):
    import base64
    from io import BytesIO

    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import (
        _decode_pdf_images,
        _inspect_pdf_structure,
        _select_pdf_images,
    )

    pdf = tmp_path / "resized-vector-form.pdf"
    canvas = Canvas(str(pdf), pagesize=(200, 200))
    canvas.beginForm("chart", 0, 0, 100, 100)
    canvas.rect(0, 0, 100, 100)
    canvas.line(0, 0, 100, 100)
    canvas.endForm()
    canvas.saveState()
    canvas.scale(0.1, 0.1)
    canvas.doForm("chart")
    canvas.restoreState()
    canvas.showPage()
    canvas.setPageSize((300, 300))
    canvas.saveState()
    canvas.scale(2, 2)
    canvas.doForm("chart")
    canvas.restoreState()
    canvas.save()

    structure = _inspect_pdf_structure(pdf)
    selected = _select_pdf_images(structure.images, pdf.name)
    form = next(image for image in selected if image.identity[0] == "render-form")
    images, _ = _decode_pdf_images((form,), pdf.name)

    assert form.render_page_number == 1
    assert (form.width, form.height) == (300, 300)
    with Image.open(BytesIO(base64.b64decode(images[0]))) as rendered:
        assert rendered.size == (300, 300)


def test_read_text_file(tmp_path):
    from anthill.multimodal.reader import read_file

    f = tmp_path / "note.md"
    f.write_text("# Test\nHello world.")
    fc = read_file(f)
    assert "Hello world" in fc.text
    assert fc.images_b64 == []


def test_read_unsupported_raises(tmp_path):
    from anthill.multimodal.reader import read_file

    f = tmp_path / "data.bin"
    f.write_bytes(bytes(range(256)))
    with pytest.raises(ValueError, match="isn't UTF-8"):
        read_file(f)


def test_read_pdf_preserves_table_structure(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import read_file

    pdf = tmp_path / "results.pdf"
    canvas = Canvas(str(pdf), pagesize=letter)
    for y, row in zip(
        (700, 680, 660),
        (
            ("Region", "Revenue", "Growth"),
            ("Europe", "$120", "8%"),
            ("Americas", "$95", "5%"),
        ),
        strict=True,
    ):
        for x, value in zip((72, 250, 420), row, strict=True):
            canvas.drawString(x, y, value)
    canvas.save()

    content = read_file(pdf)

    assert "| Region" in content.text
    assert "| Europe" in content.text
    assert "| Americas" in content.text


def test_read_pdf_preserves_page_boundaries(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import read_file

    pdf = tmp_path / "two-pages.pdf"
    canvas = Canvas(str(pdf))
    canvas.drawString(72, 720, "First page finding")
    canvas.showPage()
    canvas.drawString(72, 720, "Second page finding")
    canvas.save()

    content = read_file(pdf)

    assert "First page finding\n\n---\n\nSecond page finding" in content.text


def test_read_text_pdf_marks_small_decorative_image_as_text_sufficient(tmp_path):
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal import reader

    pdf = tmp_path / "illustrated-report.pdf"
    image = tmp_path / "chart.png"
    Image.new("RGB", (20, 20), "black").save(image)
    canvas = Canvas(str(pdf))
    canvas.drawString(72, 720, "Quarterly results")
    canvas.drawImage(str(image), 72, 650, width=20, height=20)
    canvas.save()

    content = reader.read_file(pdf)

    assert "Quarterly results" in content.text
    assert content.images_b64 == []
    assert content.pdf_preflight.image_count == 0
    assert content.pdf_preflight.vision_required is False


def test_read_scanned_pdf_preflights_images_before_inference(tmp_path):
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal import reader

    pdf = tmp_path / "scan.pdf"
    image = tmp_path / "page.png"
    Image.new("RGB", (20, 20), "black").save(image)
    canvas = Canvas(str(pdf))
    canvas.drawImage(str(image), 72, 650, width=20, height=20)
    canvas.save()
    content = reader.read_file(pdf)

    assert content.has_images
    assert len(list(content.iter_image_batches(4))) == 1
    assert content.pdf_preflight.image_count == 1
    assert content.pdf_preflight.decoded_image_bytes == 20 * 20 * 3
    assert content.pdf_preflight.vision_required is True


def test_text_pdf_with_substantial_image_requires_vision_without_cue_words(tmp_path):
    from PIL import Image, ImageDraw
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import read_file

    image_path = tmp_path / "regional-data.png"
    image = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(image).text((80, 130), "EAST REVENUE 777 EUR", fill="black")
    image.save(image_path)
    pdf = tmp_path / "regional-report.pdf"
    canvas = Canvas(str(pdf))
    canvas.drawString(72, 720, "Quarterly note: West revenue was 500 EUR.")
    canvas.drawImage(str(image_path), 54, 450, width=504, height=168)
    canvas.save()

    content = read_file(pdf)

    assert content.pdf_preflight.vision_required is True


def test_text_pdf_with_textless_image_page_requires_vision(tmp_path):
    from PIL import Image, ImageDraw
    from reportlab.pdfgen.canvas import Canvas

    from anthill.multimodal.reader import read_file

    image_path = tmp_path / "visual.png"
    image = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(image).text((80, 130), "ALPHA -> BETA", fill="black")
    image.save(image_path)
    pdf = tmp_path / "mixed-pages.pdf"
    canvas = Canvas(str(pdf))
    canvas.drawString(72, 720, "This first page contains sufficient structured text.")
    canvas.showPage()
    canvas.drawImage(str(image_path), 54, 450, width=504, height=168)
    canvas.save()

    content = read_file(pdf)

    assert "sufficient structured text" in content.text
    assert content.pdf_preflight.vision_required is True


def test_bounded_pdf_reader_terminates_worker_on_timeout(tmp_path, monkeypatch):
    from anthill.multimodal import reader

    class Process:
        def __init__(self):
            from io import BytesIO

            self.stdout = BytesIO()
            self.stderr = BytesIO()
            self.timeout = None
            self.killed = False
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                self.timeout = timeout
                raise reader.subprocess.TimeoutExpired("pdf-worker", timeout)
            if self.killed:
                self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True

    process = Process()
    monkeypatch.setattr(reader.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(reader.PdfSafetyLimitExceeded, match="processing-time limit"):
        reader._read_pdf_bounded(tmp_path / "slow.pdf", 0.05)

    assert process.timeout is not None and 0 < process.timeout <= 0.05
    assert process.killed is True


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_read_file_rejects_invalid_pdf_timeout(tmp_path, timeout):
    from anthill.multimodal.reader import PdfSafetyLimitExceeded, read_file

    source = tmp_path / "timeout.pdf"
    source.write_bytes(b"not parsed")

    with pytest.raises(PdfSafetyLimitExceeded, match="processing-time limit"):
        read_file(source, timeout_seconds=timeout)


def test_read_file_clamps_pdf_timeout_to_hard_limit(tmp_path, monkeypatch):
    from anthill.multimodal import reader

    source = tmp_path / "clamped.pdf"
    source.write_bytes(b"not parsed")
    observed = []
    monkeypatch.setattr(
        reader,
        "_read_pdf_bounded",
        lambda path, timeout: observed.append(timeout) or reader.FileContent(text="ok"),
    )

    reader.read_file(source, timeout_seconds=reader.PDF_HARD_PROCESSING_SECONDS * 2)

    assert observed == [reader.PDF_HARD_PROCESSING_SECONDS]


def test_bounded_pdf_reader_reports_signal_death_without_claiming_memory_limit(
    tmp_path, monkeypatch
):
    import signal

    from anthill.multimodal import reader

    class Process:
        from io import BytesIO

        stdout = BytesIO()
        stderr = BytesIO()
        returncode = -signal.SIGSEGV

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(reader.subprocess, "Popen", lambda *args, **kwargs: Process())

    with pytest.raises(reader.PdfSafetyLimitExceeded) as excinfo:
        reader._read_pdf_bounded(tmp_path / "crash.pdf", 5.0)

    message = str(excinfo.value)
    assert "SIGSEGV" in message
    assert "terminated abnormally" in message
    assert "memory safety limit" not in message


# ── PDF writer ───────────────────────────────────────────────────────────────


def test_markdown_to_pdf(tmp_path):
    from anthill.multimodal.writer import markdown_to_pdf

    md = "# Test Report\nThis is a summary.\n\n- Item one\n- Item two\n\n## Related\n[[foo]]"
    out = tmp_path / "test.pdf"
    result = markdown_to_pdf(md, out, title="Test")
    assert result.exists()
    assert result.stat().st_size > 1000  # non-trivial PDF
