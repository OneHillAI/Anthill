"""`/lite` - the in-browser inference prototype (Phase 0).

A small open model runs entirely in the browser tab via WebLLM; nothing runs server-side. The route is
flag-gated (`ANTHILL_LITE`) and unauthenticated (a zero-install demo). WebGPU + a model download can't
run in CI, so these tests assert the page is served only behind the flag and is wired correctly
(WebLLM import, the model id, WebGPU detection); the actual inference is validated manually in a
WebGPU browser.
"""

from fastapi.testclient import TestClient

import anthill.web.app as app_mod


def test_lite_is_404_without_the_flag(monkeypatch):
    monkeypatch.delenv("ANTHILL_LITE", raising=False)
    r = TestClient(app_mod.app).get("/lite")
    assert r.status_code == 404  # not exposed by default


def test_lite_serves_a_wired_page_with_the_flag(monkeypatch):
    monkeypatch.setenv("ANTHILL_LITE", "1")
    r = TestClient(app_mod.app).get("/lite")
    assert r.status_code == 200
    body = r.text
    assert "@mlc-ai/web-llm" in body  # WebLLM is imported
    assert "CreateMLCEngine" in body  # the engine is created in-tab
    assert "Qwen2.5-1.5B" in body  # the small model id is embedded
    assert "navigator.gpu" in body  # WebGPU is detected client-side
    assert "stream: true" in body  # the reply streams
    # it is a zero-install, in-tab surface (no server-side inference)
    assert "in this browser tab" in body
