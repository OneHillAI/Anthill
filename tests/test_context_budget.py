"""Per-model input-context budgets (issue #277): compaction/history budgets scale with the model's
context window, floored at the historical 6000 tokens / 24000 chars so nothing regresses."""

import importlib

context = importlib.import_module("anthill.inference.context")


class _BigWindow:
    def context_window(self, model=None):
        return 131072  # a 128k-window model


class _NoProbe:
    pass  # a backend that doesn't expose context_window


def test_budget_scales_with_a_large_window():
    assert context.window_for(_BigWindow()) == 131072
    assert context.token_budget(_BigWindow()) == int(131072 * 0.6)  # well above the old 6000
    assert context.char_budget(_BigWindow()) == int(131072 * 0.6) * 4


def test_budget_floors_when_window_unknown_or_small():
    b = _NoProbe()
    assert context.window_for(b) == 8192  # safe default
    assert context.token_budget(b) == 6000  # floor (8192*0.6 = 4915 < 6000)
    assert context.char_budget(b) == 24000  # floor


def test_zero_window_falls_back_to_default():
    class _Zero:
        def context_window(self, model=None):
            return 0

    assert context.window_for(_Zero()) == 8192


def test_ollama_context_window_reads_model_info_and_caches(monkeypatch):
    import anthill.inference.ollama as om

    om._CTX_WINDOW_CACHE.clear()
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"model_info": {"general.name": "q", "qwen2.context_length": 32768}}

    monkeypatch.setattr(
        om.httpx, "post", lambda url, **k: (calls.update(n=calls["n"] + 1), _Resp())[1]
    )
    be = om.OllamaBackend("http://x", "qwen3:8b")
    assert be.context_window() == 32768
    assert be.context_window() == 32768  # served from cache
    assert calls["n"] == 1  # probed once


def test_ollama_context_window_zero_on_error(monkeypatch):
    import anthill.inference.ollama as om

    om._CTX_WINDOW_CACHE.clear()

    def _boom(url, **k):
        raise om.httpx.HTTPError("engine down")

    monkeypatch.setattr(om.httpx, "post", _boom)
    assert om.OllamaBackend("http://y", "m").context_window() == 0


def test_executor_derives_budget_from_the_model_window():
    from anthill.agent.executor import AgentExecutor

    ex = AgentExecutor(_BigWindow(), [], model="big")
    assert ex.max_context_tokens == context.token_budget(_BigWindow(), "big")  # not the old 6000
    ex2 = AgentExecutor(_BigWindow(), [], model="big", max_context_tokens=5000)
    assert ex2.max_context_tokens == 5000  # an explicit value still wins
