"""Web-search provider selection + Google Custom Search parsing (no network)."""

from anthill.search import web


def _clear_google(monkeypatch):
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_SEARCH_CX", raising=False)


def test_provider_defaults_to_duckduckgo(monkeypatch):
    _clear_google(monkeypatch)
    assert web.provider() == "duckduckgo"


def test_provider_is_google_when_both_keys_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_SEARCH_CX", "cx")
    assert web.provider() == "google"


def test_provider_needs_both_keys(monkeypatch):
    _clear_google(monkeypatch)
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "key")  # cx still missing
    assert web.provider() == "duckduckgo"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_google_search_parses_items_and_uses_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_SEARCH_CX", "cx")

    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp(
            {
                "items": [
                    {
                        "title": "Elvis Presley",
                        "link": "https://en.wikipedia.org/wiki/Elvis",
                        "snippet": "Died August 16, 1977.",
                    },
                    {"title": "Graceland", "link": "https://graceland.com", "snippet": "Memphis."},
                ]
            }
        )

    monkeypatch.setattr(web.httpx, "get", fake_get)

    results = web.web_search("when did Elvis die", max_results=2)

    assert captured["url"] == "https://www.googleapis.com/customsearch/v1"
    assert captured["params"]["key"] == "key" and captured["params"]["cx"] == "cx"
    assert [r.url for r in results] == [
        "https://en.wikipedia.org/wiki/Elvis",
        "https://graceland.com",
    ]
    assert results[0].title == "Elvis Presley"
    assert "1977" in results[0].snippet


def test_google_search_caps_num_at_ten(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_SEARCH_CX", "cx")

    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["num"] = params["num"]
        return _FakeResp({"items": []})

    monkeypatch.setattr(web.httpx, "get", fake_get)
    web.web_search("anything", max_results=50)
    assert captured["num"] == 10


def test_google_search_handles_empty_items(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_SEARCH_CX", "cx")
    monkeypatch.setattr(web.httpx, "get", lambda url, params=None, timeout=None: _FakeResp({}))
    assert web.web_search("nothing here") == []


# ── fetch provider (Firecrawl / Jina / direct) ────────────────────────────────


def _clear_fetch(monkeypatch):
    for var in ("FIRECRAWL_API_KEY", "JINA_API_KEY", "ANTHILL_FETCH_BACKEND"):
        monkeypatch.delenv(var, raising=False)


def test_fetch_provider_defaults_to_direct(monkeypatch):
    _clear_fetch(monkeypatch)
    assert web.fetch_provider() == "direct"


def test_fetch_provider_firecrawl_wins(monkeypatch):
    _clear_fetch(monkeypatch)
    monkeypatch.setenv("JINA_API_KEY", "j")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "f")  # firecrawl takes precedence
    assert web.fetch_provider() == "firecrawl"


def test_fetch_provider_jina_via_flag(monkeypatch):
    _clear_fetch(monkeypatch)
    monkeypatch.setenv("ANTHILL_FETCH_BACKEND", "jina")
    assert web.fetch_provider() == "jina"


def test_fetch_body_uses_firecrawl_markdown(monkeypatch):
    _clear_fetch(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "f")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["auth"] = headers["Authorization"]
        return _FakeResp({"success": True, "data": {"markdown": "# Clean\n\nBody text."}})

    monkeypatch.setattr(web.httpx, "post", fake_post)
    out = web._fetch_body("https://example.com", max_chars=100)
    assert captured["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert captured["auth"] == "Bearer f"
    assert out == "# Clean\n\nBody text."


def test_fetch_body_truncates(monkeypatch):
    _clear_fetch(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "f")
    monkeypatch.setattr(
        web.httpx,
        "post",
        lambda url, headers=None, json=None, timeout=None: _FakeResp(
            {"data": {"markdown": "x" * 500}}
        ),
    )
    assert len(web._fetch_body("https://example.com", max_chars=50)) == 50


def test_fetch_body_falls_back_to_direct_when_firecrawl_fails(monkeypatch):
    _clear_fetch(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "f")

    def boom(*a, **k):
        raise RuntimeError("firecrawl down")

    class _Html:
        text = "<html><body>Hello   world</body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        web.httpx, "post", boom
    )  # firecrawl fails -> falls back to the direct fetch
    # the direct fetch now pins the resolved IP, so stub _safe_get (not httpx.get) to isolate from the network
    monkeypatch.setattr(web, "_safe_get", lambda url, **k: _Html())
    assert web._fetch_body("https://example.com") == "Hello world"


def test_fetch_body_never_raises(monkeypatch):
    _clear_fetch(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("network gone")

    monkeypatch.setattr(web, "_safe_get", boom)
    assert web._fetch_body("https://example.com") == ""
