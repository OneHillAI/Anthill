from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from enum import Enum

import httpx


class RoutingDeadlineExceeded(TimeoutError):
    pass


class TaskType(str, Enum):
    FAST = "fast"  # simple lookup, short answer - smallest model
    GENERAL = "general"  # everyday chat, summaries, Q&A
    REASON = "reason"  # step-by-step reasoning, math, analysis
    CODE = "code"  # write / explain / debug code
    VISION = "vision"  # input contains an image
    DOCUMENT = "document"  # long document analysis (PDF, large text)
    CREATE = "create"  # generate a document / report / structured output


@dataclass
class ModelSpec:
    tag: str  # ollama pull tag, e.g. "qwen3:8b"
    task_types: list[TaskType]
    size_gb: float
    cloud: bool = False  # True → data leaves the machine
    description: str = ""


# ── model catalogue (verified 2026-06-02) ────────────────────────────────────
# Pull any of these with:  ollama pull <tag>
# Laptop = fits 16 GB RAM.  Order within task_types = preference (best first).

CATALOGUE: list[ModelSpec] = [
    # ── fast / simple ─────────────────────────────────────────────────────────
    ModelSpec(
        "qwen2.5:3b",
        [TaskType.FAST, TaskType.GENERAL],
        1.9,
        description="Fastest local model; good for simple lookups",
    ),
    # ── general chat ──────────────────────────────────────────────────────────
    ModelSpec(
        "qwen3:8b",
        [TaskType.GENERAL, TaskType.CODE, TaskType.CREATE],
        5.2,
        description="Best laptop general model (Qwen 3, 8B)",
    ),
    ModelSpec(
        "qwen3:14b",
        [TaskType.GENERAL, TaskType.CODE, TaskType.REASON, TaskType.CREATE],
        9.3,
        description="Stronger general model; needs 16 GB RAM",
    ),
    # ── code ──────────────────────────────────────────────────────────────────
    ModelSpec(
        "qwen2.5-coder:7b",
        [TaskType.CODE],
        4.7,
        description="Qwen2.5-Coder: dedicated code model (write/debug/refactor)",
    ),
    ModelSpec(
        "qwen2.5-coder:3b", [TaskType.CODE], 1.9, description="Lighter dedicated coder for laptops"
    ),
    # ── reasoning / analysis ──────────────────────────────────────────────────
    ModelSpec(
        "deepseek-r1:8b",
        [TaskType.REASON, TaskType.CODE, TaskType.DOCUMENT],
        5.2,
        description="DeepSeek-R1 chain-of-thought reasoning",
    ),
    ModelSpec(
        "deepseek-r1:14b",
        [TaskType.REASON, TaskType.CODE, TaskType.DOCUMENT],
        9.0,
        description="Stronger reasoning; borderline for 16 GB RAM",
    ),
    # ── vision (licence-clean, non-Chinese first; see docs/specs/local-vision-model-selection.md) ──
    # qwen2.5vl:3b was removed: it is under the Qwen RESEARCH LICENSE (non-commercial only), a licence
    # exposure for this commercial product. See _LICENCE_BLOCKED_TAGS below for the hard backstop.
    ModelSpec(
        "granite3.2-vision:2b",
        [TaskType.VISION, TaskType.DOCUMENT],
        2.4,
        description="IBM Granite vision (Apache-2.0, US-origin); small out-of-the-box default",
    ),
    ModelSpec(
        "mistral-small3.2:24b",
        [TaskType.VISION, TaskType.DOCUMENT],
        15.0,
        description="Mistral Small 3.2 (Apache-2.0, EU-origin); higher-quality vision, 128K context",
    ),
    ModelSpec(
        "qwen3-vl:8b",
        [TaskType.VISION, TaskType.DOCUMENT],
        6.1,
        description="Qwen3-VL 8B (Chinese-origin); opt-in maximum-accuracy mode only",
    ),
    ModelSpec(
        "qwen3-vl:4b",
        [TaskType.VISION, TaskType.DOCUMENT],
        3.3,
        description="Qwen3-VL 4B (Chinese-origin); smaller opt-in maximum-accuracy variant",
    ),
    ModelSpec(
        "qwen2.5vl:7b",
        [TaskType.VISION, TaskType.DOCUMENT],
        6.0,
        description="Qwen2.5-VL 7B (Apache-2.0, Chinese-origin); catalogue-only, not in the default"
        " preference (last-resort capability fallback if a user pulled it)",
    ),
    ModelSpec(
        "qwen3.5:9b",
        [TaskType.VISION, TaskType.DOCUMENT],
        6.6,
        description="Vision-language model (Chinese-origin); catalogue-only, not in the default"
        " preference (last-resort capability fallback if a user pulled it)",
    ),
    # ── cloud-boosted (opt-in, data leaves machine) ───────────────────────────
    ModelSpec(
        "kimi-k2.6:cloud",
        [
            TaskType.GENERAL,
            TaskType.REASON,
            TaskType.CODE,
            TaskType.VISION,
            TaskType.DOCUMENT,
            TaskType.CREATE,
        ],
        0.0,
        cloud=True,
        description="⚠️ CLOUD - routes via Moonshot AI. 1T-param model, "
        "best capability. Only use if privacy is not required.",
    ),
]

# Task-type → ordered list of preferred tags (first available wins)
_PREFERENCE: dict[TaskType, list[str]] = {
    TaskType.FAST: ["qwen2.5:3b", "qwen3:8b"],
    TaskType.GENERAL: ["qwen3:8b", "qwen3:14b", "qwen2.5:3b"],
    TaskType.REASON: ["deepseek-r1:8b", "deepseek-r1:14b", "qwen3:14b", "qwen3:8b"],
    TaskType.CODE: [
        "qwen2.5-coder:7b",
        "qwen2.5-coder:3b",
        "deepseek-r1:8b",
        "qwen3:8b",
        "qwen3:14b",
    ],
    # 100% non-Chinese, licence-clean vision default (the founder's explicit choice): IBM Granite (US)
    # then Mistral (EU). No Chinese-origin model appears here - Qwen3-VL is reached only via the
    # max-accuracy opt-in below, and qwen2.5vl:7b / qwen3.5:9b are catalogue-only last-resort fallbacks.
    TaskType.VISION: ["granite3.2-vision:2b", "mistral-small3.2:24b"],
    TaskType.DOCUMENT: [
        "deepseek-r1:8b",
        "qwen3:14b",
        "granite3.2-vision:2b",
        "mistral-small3.2:24b",
    ],
    TaskType.CREATE: ["qwen3:8b", "qwen3:14b", "deepseek-r1:8b"],
}

# Vision tags preferred FIRST when the opt-in "maximum accuracy" mode is on. Qwen3-VL is Chinese-origin,
# so it is reached only through this opt-in; the sovereign default preference above stays non-Chinese.
_MAX_ACCURACY_VISION_LEAD: list[str] = ["qwen3-vl:8b", "qwen3-vl:4b"]

# Tags the router must NEVER return, regardless of what is installed. qwen2.5vl:3b is under the Qwen
# RESEARCH LICENSE (non-commercial only); it is removed from the catalogue AND hard-blocked here as a
# backstop so the same-family and vision-capability fallbacks cannot silently re-select an installed copy.
_LICENCE_BLOCKED_TAGS: frozenset[str] = frozenset({"qwen2.5vl:3b"})


def _preferred_tags(task: TaskType, *, vision_max_accuracy: bool = False) -> list[str]:
    """Ordered preferred tags for a task. With the opt-in maximum-accuracy mode on, the vision-capable
    tasks (VISION, DOCUMENT) lead with Qwen3-VL and then fall through to the licence-clean non-Chinese
    default tiers; otherwise the non-Chinese tiers lead and Qwen3-VL is never selected."""
    base = _PREFERENCE.get(task, [])
    if not vision_max_accuracy or task not in (TaskType.VISION, TaskType.DOCUMENT):
        return base
    return _MAX_ACCURACY_VISION_LEAD + [t for t in base if t not in _MAX_ACCURACY_VISION_LEAD]


# ── keyword classifiers ───────────────────────────────────────────────────────

_CODE_RE = re.compile(
    r"\b(code|function|class|debug|error|traceback|sql|bash|python|javascript"
    r"|typescript|implement|refactor|unittest|test|api|endpoint|script)\b",
    re.I,
)
_REASON_RE = re.compile(
    r"\b(why|explain|reason|analyse|analyze|compare|pros and cons|trade.?off"
    r"|calculate|formula|proof|step.?by.?step|think through|evaluate|decide"
    # arithmetic: route math (incl. short multi-turn "multiply it by 6") to the REASON model, not FAST
    r"|multiply|multiplied|divide|divided|subtract|modulo|square root|factorial|percentage)\b",
    re.I,
)
_FAST_RE = re.compile(
    r"^.{0,80}$",  # short queries are usually simple
)
_CREATE_RE = re.compile(
    r"\b(write|draft|create|generate|produce|make|compose|format|report|summary"
    r"|document|template|letter|email|slide|pdf)\b",
    re.I,
)
_DOC_RE = re.compile(
    r"\b(summarise|summarize|this document|the attached|the pdf|long text|full text"
    r"|transcript|meeting notes|entire)\b",
    re.I,
)


def classify(prompt: str, has_image: bool = False) -> TaskType:
    """Classify a prompt into a TaskType. Fast heuristic - no model call needed."""
    if has_image:
        return TaskType.VISION
    if _CODE_RE.search(prompt):
        return TaskType.CODE
    if _REASON_RE.search(prompt):
        return TaskType.REASON
    if _DOC_RE.search(prompt):
        return TaskType.DOCUMENT
    if _CREATE_RE.search(prompt):
        return TaskType.CREATE
    if _FAST_RE.match(prompt.strip()):
        return TaskType.FAST
    return TaskType.GENERAL


class TaskRouter:
    """Select the best locally-available model for a given task.

    Queries Ollama's /api/tags to discover what's actually installed.
    Falls back through the preference list until it finds one that exists.
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        allow_cloud: bool = False,
        pinned_model: str | None = None,
        trust_env: bool = True,
        deadline: float | None = None,
        vision_max_accuracy: bool = False,
    ) -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.allow_cloud = allow_cloud
        self.trust_env = trust_env
        self.deadline = deadline
        # Opt-in "maximum accuracy" vision mode: prefer Qwen3-VL for VISION/DOCUMENT (see the spec).
        # Off by default so the sovereign non-Chinese stack holds. Two sources, either turns it on:
        #   1. env ANTHILL_VISION_MAX_ACCURACY - a deployment/test lever that also lets the ingestion
        #      path (which builds its own router without the OrgSettings flag) honor the opt-in, mirroring
        #      the ANTHILL_FORCE_MODEL pattern below.
        #   2. the `vision_max_accuracy` argument - passed from the account's OrgSettings by the chat path.
        env_max = os.environ.get("ANTHILL_VISION_MAX_ACCURACY", "").strip().lower()
        self.vision_max_accuracy = env_max in {"1", "true", "yes", "on"} or bool(
            vision_max_accuracy
        )
        self._available: set[str] | None = None  # lazily populated
        self._capabilities: dict[str, set[str]] = {}
        self._remote_models: set[str] = set()
        # Hard pin: every non-vision task uses exactly this model instead of the largest installed one.
        # Lets a deployment on constrained hardware, or a test that needs a reproducible model, force the
        # configured model rather than have the router load an oversized one (e.g. qwen3:14b when qwen3:8b
        # was configured). Opt-in: no pin -> normal task-based routing.
        #
        # Two sources, in precedence order:
        #   1. env ANTHILL_FORCE_MODEL - a deployment/test HARD override. It wins over the caller's pin
        #      because the chat route passes the account's configured model as `pinned_model` (#413), and
        #      an operator/gate that sets ANTHILL_FORCE_MODEL means "serve exactly this, regardless of the
        #      account model" (e.g. the injection-resistance eval gate pinning qwen3:8b - see #567). If env
        #      won only when no pin was passed, the account model would silently shadow the forced one.
        #   2. the `pinned_model` argument - the account's configured local model (the #413 pin).
        env_force = os.environ.get("ANTHILL_FORCE_MODEL", "").strip()
        self.pinned_model = env_force or (pinned_model or "").strip()

    def _installed_models(self) -> set[str]:
        if self._available is not None:
            return self._available
        try:
            resp = self._get("/api/tags", timeout=self._request_timeout())
            self._check_deadline()
            models = resp.json().get("models", [])
            names = {model["name"] for model in models}
            self._remote_models = {
                model["name"]
                for model in models
                if model.get("remote_host") or model.get("remote_model")
            }
            self._check_deadline()
        except RoutingDeadlineExceeded:
            raise
        except Exception:
            self._check_deadline()
            names = set()
            self._capabilities = {}
            self._remote_models = set()
        self._available = names
        return names

    def _model_capabilities(self, tag: str) -> set[str]:
        if tag in self._capabilities:
            return self._capabilities[tag]
        try:
            resp = self._post(
                "/api/show",
                json={"model": tag},
                timeout=self._request_timeout(),
            )
            self._check_deadline()
            capabilities = set(resp.json().get("capabilities", []))
            self._check_deadline()
        except RoutingDeadlineExceeded:
            raise
        except Exception:
            self._check_deadline()
            capabilities = set()
        self._capabilities[tag] = capabilities
        return capabilities

    def _check_deadline(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RoutingDeadlineExceeded("Model discovery exceeded its processing deadline.")

    def _request_timeout(self) -> float:
        if self.deadline is None:
            return 5
        self._check_deadline()
        return min(5, max(0.001, self.deadline - time.monotonic()))

    def _get(self, path: str, **kwargs):
        url = f"{self.ollama_url}{path}"
        if self.trust_env:
            return httpx.get(url, **kwargs)
        with httpx.Client(trust_env=False) as client:
            return client.get(url, **kwargs)

    def _post(self, path: str, **kwargs):
        url = f"{self.ollama_url}{path}"
        if self.trust_env:
            return httpx.post(url, **kwargs)
        with httpx.Client(trust_env=False) as client:
            return client.post(url, **kwargs)

    def invalidate_cache(self) -> None:
        """Call after pulling a new model so the next lookup re-queries Ollama."""
        self._available = None
        self._capabilities = {}
        self._remote_models = set()

    def _model_allowed(self, tag: str) -> bool:
        if tag in _LICENCE_BLOCKED_TAGS:
            return False  # non-commercial licence - never serve, even if the user pulled it
        spec = next((item for item in CATALOGUE if item.tag == tag), None)
        is_cloud = (
            tag in self._remote_models
            or (spec is not None and spec.cloud)
            or tag.endswith(":cloud")
            or tag.endswith("-cloud")
        )
        return self.allow_cloud or not is_cloud

    def pick(self, task: TaskType, *, fallback_tag: str = "qwen2.5:3b") -> str:
        """Return the best available model tag for `task`.

        Never returns a cloud model unless allow_cloud=True.
        Falls back to fallback_tag if nothing else is installed.
        """
        installed = self._installed_models()
        # A hard pin overrides task routing for everything except vision (a pinned text model can't do
        # vision). Exact-match only: if the pinned model isn't installed, fall through to normal routing
        # rather than silently substitute a different size.
        if (
            self.pinned_model
            and task != TaskType.VISION
            and self.pinned_model in installed
            and self._model_allowed(self.pinned_model)
        ):
            return self.pinned_model
        for tag in _preferred_tags(task, vision_max_accuracy=self.vision_max_accuracy):
            if not self._model_allowed(tag):
                continue
            if tag in installed:
                return tag
            # Exact size absent: accept an INSTALLED same-family model (e.g. want qwen3:14b, have
            # qwen3:8b) - but return the tag Ollama ACTUALLY has, never the requested-but-uninstalled
            # size, or chat 404s with "model not found". Sorted for a deterministic pick.
            same_family = sorted(
                candidate
                for candidate in installed
                if candidate.split(":")[0] == tag.split(":")[0] and self._model_allowed(candidate)
            )
            if same_family:
                return same_family[0]
        if task == TaskType.VISION:
            for tag in sorted(installed):
                if self._model_allowed(tag) and "vision" in self._model_capabilities(tag):
                    return tag
        return fallback_tag if self._model_allowed(fallback_tag) else ""

    def route(
        self, prompt: str, *, has_image: bool = False, fallback_tag: str = "qwen2.5:3b"
    ) -> tuple[str, TaskType]:
        """Classify prompt and return (best_model_tag, task_type)."""
        task = classify(prompt, has_image=has_image)
        return self.pick(task, fallback_tag=fallback_tag), task
