"""Hardware-aware model sizing.

Given the memory of the box the org model will run on, recommend the largest open model that runs *well* -
not blindly the most parameters - and annotate the whole catalog as recommended / fits / too large.

The memory a served model needs is roughly:

    weights  +  KV cache (grows with context length x concurrent streams)  +  OS/serving overhead

Two hardware kinds differ enough to model separately:

  - "apple"  (Apple Silicon, unified memory): macOS + the display share the same pool, so only a fraction is
               realistically usable for weights.
  - "gpu"    (a dedicated CUDA GPU): a small fixed overhead, then the rest is yours.

The constants below are deliberate approximations calibrated to a real-world envelope - a 32 GB Mac -> ~32B,
an 80 GB GPU -> 70B, two 80 GB GPUs -> a 235B-class MoE. Tune them; do not treat them as exact. The picker
should surface the recommendation and the trade (a clean 32B beats a mangled 70B at 2-bit), not just the
biggest number.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass

from ..platform_layer import hidden_window_kwargs, memory_gb

_GB_PER_B_Q4 = 0.55  # GB of memory per billion params at 4-bit (weights, plus a little overhead)
_GB_PER_B_FP16 = (
    2.0  # GB per billion params at full precision (bf16/fp16) - vLLM's default on a cloud GPU
)
_GB_PER_B_AWQ = (
    0.6  # GB per billion params at 4-bit AWQ/GPTQ (~a quarter of fp16) - vLLM serves it directly
)
_KV_PER_K_PER_STREAM = 0.1  # GB of KV cache per 1k context tokens per concurrent stream (rough)
_GPU_OVERHEAD_GB = 5.0  # fixed OS/serving overhead on a dedicated GPU box
_APPLE_USABLE = 0.66  # fraction of unified memory usable for weights on Apple Silicon
_LOCAL_RUNNER_GB = (
    2.5  # runner + serving working set a local model needs BEYOND its weights and KV cache
)
# (the model loader, compute buffers, etc.). Sizing against this resident footprint - not raw weights -
# is what stops a too-large "recommended" model from freezing a small Mac: a 16 GB box was previously told
# to run a 14B (weights ~7.7 GB but resident ~10 GB) and froze (issue #490).
_COMFORT_FREE_GB = (
    7.0  # free system memory a model should LEAVE (after its resident footprint) to run
)
# well on Apple Silicon: the OS + WebView + Python backend, KV/context growth, and possibly a second
# (different-family verifier) model loaded concurrently. A model that fits but leaves less than this
# "runs, but tight" rather than "recommended" - the 12-14B-on-16GB case that froze the box (issue #416).
_DISK_COMFORT_FREE_GB = (
    20.0  # free disk space a model download should LEAVE, beyond its own on-disk size
)
# the OS needs room for swap/logs/temp files/updates, and a download can use transient extra space
# before its final file is committed - the disk equivalent of _COMFORT_FREE_GB above.


@dataclass(frozen=True)
class Model:
    name: str  # human-friendly display name shown in the picker
    params_b: float
    hf_id: str = (
        ""  # Hugging Face repo id for vLLM / cloud serving (meta-llama/Llama-3.3-70B-Instruct)
    )
    ollama_tag: str = ""  # Ollama tag for on-prem serving (llama3.3:70b)
    gated: bool = (
        False  # HF-gated (needs license acceptance + a token before a cloud GPU can pull it)
    )
    family: str = ""  # "Qwen" | "GLM" | "DeepSeek" | ... - groups the local picker
    origin: str = ""  # who built it, shown as-is in the picker (e.g. "Meta, US" / "Alibaba, China")
    quant_hf_id: str = (
        ""  # a verified 4-bit AWQ repo, so a big model fits a smaller cloud GPU ("" = none)
    )
    intelligence: float = 0.0  # an intelligence-index score (leaderboard-style); higher is smarter.
    # Drives ranking: the picker offers the SMARTEST model that fits, not merely the largest (a 3B-active
    # MoE can outrank a dense 32B). 0 = unscored, sorts below any scored model.
    active_b: float = (
        0.0  # params that compute per token (speed driver). For a dense model == params_b;
    )
    # for an MoE it is far smaller (Qwen3.6 35B-A3B: 35B resident, 3B active). params_b still drives memory.
    license: str = (
        ""  # indicative licence from the catalog ("Apache-2.0" | "MIT" | "Llama Community" ...)
    )
    num_attention_heads: int = 0  # 0 = unknown/uncurated. Used only to validate a tensor-parallel
    # gpu_count (docs/specs/multi-gpu-tensor-parallel-serving.md); vLLM requires gpu_count to evenly
    # divide this. Not populated for model_catalog.json's current seed/placeholder rows - do not invent
    # a value here without a verified model card; 0 is treated as "not checked", not "one head."


# The catalog is DATA, not code: a bundled JSON (hosting/model_catalog.json) the picker ranks by
# intelligence x what fits the hardware, refreshable in-app (a Refresh button rewrites an override copy in
# the Anthill home). This tiny hardcoded set is only a safety net for a missing/corrupt file - it spans the
# size range so every machine still gets a sensible pick offline.
_FALLBACK_CATALOG: tuple[Model, ...] = (
    Model(
        "Qwen3.5 4B",
        4,
        "Qwen/Qwen3.5-4B",
        "qwen3.5:4b",
        family="Qwen",
        origin="Alibaba, China",
        intelligence=34,
        active_b=4,
    ),
    Model(
        "gpt-oss 20B",
        20,
        "openai/gpt-oss-20b",
        "gpt-oss:20b",
        family="gpt-oss",
        origin="OpenAI, US",
        intelligence=48,
        active_b=3.6,
    ),
    Model(
        "Qwen3.6 35B-A3B",
        35,
        "Qwen/Qwen3.6-35B-A3B",
        "qwen3.6",
        family="Qwen",
        origin="Alibaba, China",
        intelligence=58,
        active_b=3,
    ),
    Model(
        "Llama 3.3 70B",
        70,
        "meta-llama/Llama-3.3-70B-Instruct",
        "llama3.3:70b",
        gated=True,
        family="Llama",
        origin="Meta, US",
        intelligence=46,
        active_b=70,
        quant_hf_id="casperhansen/llama-3.3-70b-instruct-awq",
    ),
)


def _catalog_paths() -> list:
    """Where the catalog JSON may live, most-authoritative last: the bundled seed, then a refresh override
    in the Anthill home. Refresh writes the override; the bundle is read-only."""
    import os
    from pathlib import Path

    home = os.environ.get("ANTHILL_HOME") or os.path.expanduser("~/.anthill")
    return [Path(__file__).parent / "model_catalog.json", Path(home) / "model_catalog.json"]


# --- catalog trust boundary ---------------------------------------------------------------------------
# The catalog is refreshable over the network, and its identity fields are not metadata - they are
# instructions: `ollama_tag` is handed to `ollama pull`, and `hf_id` is handed to vLLM as MODEL_NAME on the
# org's GPU (which fetches and LOADS that repo - a repo can carry pickled weights, so it is a code-execution
# surface, not just a bad-answers surface). A row that arrived over the wire is therefore untrusted input.
#
# These predicates constrain the shapes an attacker could use to redirect a pull/serve at their own
# artifact: a scheme, a registry/host segment ("hf.co/user/repo", "evil.com/model"), traversal, whitespace.
# They are a shape gate, not authenticity - see docs/specs/model-catalog-trust.md for what they do and do
# not stop. An admin typing a tag into the /models "Advanced" field is a human decision and stays
# unrestricted; this gate is only for data that arrived over the wire.

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_safe_ollama_tag(tag: str) -> bool:
    """A plain Ollama library reference: ``name[:version]`` or ``namespace/name[:version]``.

    Rejects anything that could point the pull at a host the publisher does not control. Ollama happily
    resolves remote refs (the app's own Advanced field advertises ``hf.co/...``), so an unconstrained tag
    from the network is a download instruction for arbitrary weights.
    """
    t = (tag or "").strip()
    if not t or len(t) > 128 or "://" in t or ".." in t or "\\" in t:
        return False
    if any(ch.isspace() for ch in t):
        return False
    name, sep, version = t.partition(":")
    if sep and not _SEGMENT.match(version):
        return False
    parts = name.split("/")
    if len(parts) > 2:  # "hf.co/user/repo" - a registry ref, never a library tag
        return False
    if len(parts) == 2 and "." in parts[0]:  # "evil.com/model" - first segment is a host
        return False
    return all(bool(_SEGMENT.match(p)) for p in parts)


def is_safe_hf_id(hf_id: str) -> bool:
    """A Hugging Face repo id: exactly ``org/repo``. Empty is allowed (an Ollama-only row)."""
    v = (hf_id or "").strip()
    if not v:
        return True
    if len(v) > 128 or "://" in v or ".." in v or "\\" in v or any(ch.isspace() for ch in v):
        return False
    parts = v.split("/")
    return len(parts) == 2 and all(bool(_SEGMENT.match(p)) for p in parts)


def is_safe_row(row: object) -> bool:
    """Whether a catalog row is safe to offer. Only the identity fields decide: a wrong `intelligence`
    misranks the picker, but a wrong `ollama_tag`/`hf_id` downloads and runs someone else's artifact."""
    if not isinstance(row, dict):
        return False
    name = row.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        return False
    if not is_safe_ollama_tag(row.get("ollama_tag", "")):
        return False
    return is_safe_hf_id(row.get("hf_id", "")) and is_safe_hf_id(row.get("quant_hf_id", ""))


def _model_from_row(row: dict) -> Model:
    return Model(
        name=row["name"],
        params_b=float(row.get("params_b") or 0),
        hf_id=row.get("hf_id", ""),
        ollama_tag=row.get("ollama_tag", ""),
        gated=bool(row.get("gated", False)),
        family=row.get("family", ""),
        origin=row.get("origin", ""),
        quant_hf_id=row.get("quant_hf_id", ""),
        intelligence=float(row.get("intelligence") or 0),
        active_b=float(row.get("active_b") or 0),
        license=row.get("license", ""),
        num_attention_heads=int(row.get("num_attention_heads") or 0),
    )


def load_catalog() -> tuple[Model, ...]:
    """The frontier catalog from JSON (bundled seed, then a refreshed override - last that parses wins).
    Falls back to a tiny hardcoded set if nothing parses. Never raises.

    Rows are gated by ``is_safe_row``: the override is written by a network refresh, so it is untrusted at
    rest too, and a file that is unsafe rather than merely malformed must not silently become the catalog.
    A source containing ANY unsafe row is rejected WHOLESALE (not row-filtered): a tampered catalog is
    evidence about the source, so keeping its "good" rows would be trusting an attacker's leftovers.
    """
    import json

    models: tuple[Model, ...] = ()
    for path in _catalog_paths():
        try:
            data = json.loads(path.read_text())
            rows = data.get("models") if isinstance(data, dict) else data
            rows = rows if isinstance(rows, list) else []
            if not rows or not all(is_safe_row(r) for r in rows):
                continue  # missing, malformed, or tampered -> fall back to the previous source
            models = tuple(_model_from_row(r) for r in rows)
        except Exception:
            continue
    return models or _FALLBACK_CATALOG


def families_by_intelligence(catalog: tuple[Model, ...]) -> tuple[str, ...]:
    """Family display order for the picker: the family with the strongest model first."""
    best: dict[str, float] = {}
    for m in catalog:
        if m.family:
            best[m.family] = max(best.get(m.family, 0.0), m.intelligence)
    return tuple(sorted(best, key=lambda f: best[f], reverse=True))


# ── region-aware council suggestions ──────────────────────────────────────────────
#
# A council reviewer picker benefits from suggesting a same-region, family-diverse set (comparable
# strength + different training lineage is the condition under which mixing models actually helps - see
# the product-council-setup-review). But region and family diversity don't always coexist: some regions
# genuinely have far fewer distinct model families than others in the catalog at any given time. This
# must be surfaced honestly rather than silently working around it or implying diversity that isn't
# there.


def _region_of(origin: str) -> str | None:
    """A broad region bucket ("US" | "EU" | "China") from a catalog Model's `origin` field (e.g. "Meta,
    US" -> "US", "Mistral AI, France (EU)" -> "EU", "Alibaba, China" -> "China"), or None if it doesn't
    match a known pattern. A display/suggestion heuristic, not an exhaustive geopolitical classifier -
    an unmatched origin is simply omitted from every region's suggestion, not misclassified."""
    o = (origin or "").strip()
    if not o:
        return None
    if o.endswith("China"):
        return "China"
    if o.endswith("US"):
        return "US"
    if o.endswith("(EU)") or o.endswith("EU"):
        return "EU"
    return None


@dataclass
class RegionalCouncilSuggestion:
    region: str
    families: tuple[str, ...]  # distinct families available in this region, strongest first
    diverse: bool  # True only if 3+ distinct families exist - a genuine same-region diverse council
    note: str  # human-readable: what's actually achievable, honest about scarcity


def suggest_regional_council(
    region: str, *, catalog: tuple[Model, ...] | None = None
) -> RegionalCouncilSuggestion:
    """What's actually achievable for a same-region, family-diverse council of 3, given the real catalog -
    not an idealized assumption. `region` is "US" | "EU" | "China" (case-insensitive)."""
    if catalog is None:
        catalog = load_catalog()
    region_norm = region.strip().upper()
    regional = [m for m in catalog if (_region_of(m.origin) or "").upper() == region_norm]
    best_by_family: dict[str, float] = {}
    for m in regional:
        if m.family:
            best_by_family[m.family] = max(best_by_family.get(m.family, 0.0), m.intelligence)
    families = tuple(sorted(best_by_family, key=lambda f: best_by_family[f], reverse=True))
    diverse = len(families) >= 3
    if diverse:
        note = (
            f"{region_norm} has {len(families)} distinct model families in the catalog - a diverse "
            "same-region council of 3 is achievable."
        )
    elif families:
        plural = "y" if len(families) == 1 else "ies"
        note = (
            f"{region_norm} only has {len(families)} distinct model famil{plural} in the catalog "
            f"({', '.join(families)}) - a genuinely diverse same-region council isn't achievable today. "
            "Consider a single strong same-region lead plus reviewers from another region for real "
            "diversity, or accept a less diverse same-region council."
        )
    else:
        note = f"No models from {region_norm} are in the catalog."
    return RegionalCouncilSuggestion(
        region=region_norm, families=families, diverse=diverse, note=note
    )


CATALOG: tuple[Model, ...] = load_catalog()
# One frontier catalog serves both surfaces; the sizing engine filters per hardware (local) or GPU (cloud).
DEFAULT_CATALOG: tuple[Model, ...] = CATALOG


@dataclass
class ModelFit:
    model: Model
    fits: bool  # memory-only: does the model fit in the available RAM/VRAM
    recommended: bool
    too_slow: bool = False  # fits in memory, but estimated below the usable-speed floor


@dataclass
class Sizing:
    recommended: (
        Model | None
    )  # the largest model that fits, or None if even the smallest is too big
    max_params_b: float  # the theoretical envelope (billions of params)
    fits: list[ModelFit]  # every catalog model annotated for this hardware


def _kv_budget_gb(context_k: float, concurrency: int) -> float:
    return max(0.0, context_k) * max(1, concurrency) * _KV_PER_K_PER_STREAM


def usable_gb(
    mem_gb: float, *, kind: str, context_k: float, concurrency: int, gpu_count: int = 1
) -> float:
    """Memory left for weights after OS/serving overhead, the runner working set, and the KV budget.

    On Apple Silicon the OS/display share unified memory (the ``_APPLE_USABLE`` fraction), and a served
    model's resident footprint is weights + KV + a runner working set - so the runner floor is reserved
    too. Sizing against that resident footprint (not raw weights) is the #490 fix.

    ``gpu_count`` (cloud GPU only) aggregates a single-node multi-GPU tensor-parallel serve: ``mem_gb`` is
    still the PER-GPU VRAM, aggregated here as ``gpu_count * mem_gb``, with the fixed overhead paid once
    per GPU (``gpu_count * _GPU_OVERHEAD_GB``) - the KV budget is not multiplied, since it is one shared
    pool across the tensor-parallel group. Defaults to 1 (today's single-GPU behavior, unchanged for
    every existing caller) - meaningless for "apple" and ignored there.
    """
    kv = _kv_budget_gb(context_k, concurrency)
    if kind == "apple":
        return max(0.0, mem_gb * _APPLE_USABLE - kv - _LOCAL_RUNNER_GB)
    return max(0.0, (gpu_count * mem_gb - gpu_count * _GPU_OVERHEAD_GB) - kv)


def resident_gb(params_b: float, *, context_k: float = 8.0, concurrency: int = 1) -> float:
    """Approx memory a locally-served 4-bit model occupies while running: weights + KV cache + the
    runner working set. This is what actually competes with the OS/app for unified memory (issue #416)."""
    return params_b * _GB_PER_B_Q4 + _kv_budget_gb(context_k, concurrency) + _LOCAL_RUNNER_GB


def onprem_council_fits(
    member_params_b: list[float],
    *,
    context_k: float = 8.0,
    concurrency: int = 1,
) -> bool:
    """Do all on-prem council members fit together in ONE shared local-hardware budget?

    Every on-prem member serves via Ollama on the SAME machine, sharing its RAM/VRAM - unlike a VPC
    member, which gets its own dedicated cloud instance (see ``servable_on_gpu``, unaffected by
    this function). So this sums only the PER-MEMBER cost (weights + its own KV budget) and compares
    against the machine's usable budget, with the OS/serving overhead and the runner working set
    reserved ONCE for the box, not once per member. Reuses the same 4-bit-local convention
    ``resident_gb`` uses; no new memory-per-billion-params constant.
    """
    if not member_params_b:
        return True
    mem_gb, kind = local_hardware()
    if mem_gb <= 0:
        return True  # hardware could not be probed; do not block the save on a bad probe
    # usable_gb(..., context_k=0) already reserves the OS/serving overhead once; on "apple" it ALSO
    # reserves _LOCAL_RUNNER_GB once internally, but the "gpu" branch does not - so top that up only
    # for "gpu", never both, so the runner working set is reserved exactly once regardless of kind.
    budget = usable_gb(mem_gb, kind=kind, context_k=0.0, concurrency=1)
    if kind != "apple":
        budget -= _LOCAL_RUNNER_GB
    total = sum(
        max(0.0, pb) * _GB_PER_B_Q4 + _kv_budget_gb(context_k, concurrency)
        for pb in member_params_b
    )
    return total <= budget


_CLUSTER_MIN_NODES = 2
_CLUSTER_MAX_NODES = 4
# NOT empirically calibrated - unlike _MOE_OVERHEAD_FACTOR below (which cites real published
# benchmarks), no rpc-server multi-machine benchmark exists in this codebase or session. This is a
# deliberately conservative placeholder applied to the naive sum of each node's own usable_gb(), standing
# in for llama.cpp rpc-server's real per-hop network overhead, which is not modeled here.
# Anchor check only (not a derivation): 4 pooled 64GB-unified-memory Apple Silicon boxes each have
# usable_gb(64, apple, context_k=8, concurrency=1) = 64*0.66 - 0.8(kv) - 2.5(runner) = 38.94GB, summing
# to 155.76GB raw; at this factor that is 155.76*0.5 = 77.88 effective GB, / _GB_PER_B_Q4 (0.55) = ~142B -
# in the neighborhood of the roadmap's own "~150B for 2-4 machines" framing, not a validated measurement.
# Revisit once real hands-on rpc-server numbers exist (same spirit as the still-open Tier 1 MoE spike).
_CLUSTER_NETWORK_EFFICIENCY = 0.5


@dataclass(frozen=True)
class ClusterSizing:
    """An estimate of pooled local-serving capacity across an org's own LAN machines (Tier 5). Every
    field here rests on self-reported node memory (Anthill cannot probe a remote box) and an
    unvalidated network-efficiency discount - ``note`` always carries that caveat."""

    max_params_b: float
    node_count: int
    raw_sum_gb: float  # naive sum before the discount, for a transparent before/after display
    note: (
        str  # honesty caveat; stronger wording when node_count is outside the anchor-checked range
    )


def cluster_max_params_b(
    node_mem_gb: list[float],
    *,
    kind: str = "apple",
    context_k: float = 8.0,
    concurrency: int = 1,
    efficiency: float = _CLUSTER_NETWORK_EFFICIENCY,
) -> ClusterSizing:
    """Estimate the largest model (billions of params) an org's pooled LAN machines could serve
    together via llama.cpp's rpc-server. Sums each node's OWN ``usable_gb()`` (the KV/runner overhead
    is reserved once per node, since each node runs its own OS and rpc-server process), then applies
    the conservative, unvalidated ``efficiency`` discount for network-hop overhead. This is an estimate
    for display only - never a gate that blocks a save (see ``model_fits_cluster``'s caller)."""
    node_count = len(node_mem_gb)
    raw_sum_gb = sum(
        usable_gb(gb, kind=kind, context_k=context_k, concurrency=concurrency) for gb in node_mem_gb
    )
    effective_gb = raw_sum_gb * efficiency
    max_params = effective_gb / _GB_PER_B_Q4
    note = (
        "Estimate only. Node memory is self-reported (Anthill cannot probe a remote LAN machine), and "
        "the network-efficiency discount is a conservative placeholder, not a measured benchmark."
    )
    if node_count < _CLUSTER_MIN_NODES or node_count > _CLUSTER_MAX_NODES:
        note += (
            f" This estimate is only anchor-checked for {_CLUSTER_MIN_NODES}-{_CLUSTER_MAX_NODES} "
            f"nodes; with {node_count} node(s) it is especially unreliable."
        )
    return ClusterSizing(
        max_params_b=max_params, node_count=node_count, raw_sum_gb=raw_sum_gb, note=note
    )


def model_fits_cluster(
    params_b: float,
    node_mem_gb: list[float],
    *,
    kind: str = "apple",
    efficiency: float = _CLUSTER_NETWORK_EFFICIENCY,
) -> bool:
    """Whether ``params_b`` fits the pooled cluster estimate. Unlike ``model_fits_vram`` (a hard
    provisioning gate - real money is about to be spent), callers must treat a False result here as a
    warning only: the estimate rests on self-reported, unmeasured numbers, so a save is never refused
    on it (see docs/specs/661-tier5-distributed-pooling.md)."""
    return (
        params_b <= cluster_max_params_b(node_mem_gb, kind=kind, efficiency=efficiency).max_params_b
    )


def fit_tier(
    params_b: float,
    mem_gb: float,
    *,
    kind: str = "apple",
    context_k: float = 8.0,
    concurrency: int = 1,
    active_b: float = 0.0,
    system_ram_gb: float | None = None,
) -> str:
    """How well a model runs on this box: ``"recommended"`` (fits with comfortable free RAM),
    ``"tight"`` (fits but leaves little headroom - warn before running), ``"offload"`` (overflows VRAM,
    but a modest MoE-only CPU-RAM spill still runs at usable speed - GPU boxes only), or ``"too_large"``
    (won't fit, or would be too slow even offloaded).

    On a dedicated GPU there is no OS/app contention, so it is normally the binary fits check - EXCEPT a
    Mixture-of-Experts model (``active_b`` less than ``params_b``) that overflows VRAM by a modest slice
    can still serve at usable speed via Ollama's existing auto-offload to system RAM (see
    ``docs/specs/local-moe-offload-fit.md``). ``system_ram_gb`` is required to even consider this - unknown
    RAM (the default) safely degrades to today's ``too_large`` verdict, never a guess. On Apple Silicon
    unified memory is shared, so a model that only just fits still starves the app: it is ``"tight"``
    unless it leaves at least ``_COMFORT_FREE_GB`` free after its resident footprint (issue #416)."""
    if kind != "apple":
        ceiling = max_params_b(mem_gb, kind=kind, context_k=context_k, concurrency=concurrency)
        if params_b <= ceiling:
            return "recommended"
        is_moe = active_b > 0.0 and active_b < params_b
        if is_moe and system_ram_gb:
            offload_model = Model(name="", params_b=params_b, active_b=active_b)
            est = estimate_tokens_per_second(
                offload_model,
                kind=kind,
                vram_gb=mem_gb,
                system_ram_gb=system_ram_gb,
                context_k=context_k,
                concurrency=concurrency,
            )
            if est is not None and est >= _MIN_USABLE_TOK_S:
                return "offload"
        return "too_large"
    resident = resident_gb(params_b, context_k=context_k, concurrency=concurrency)
    if resident > mem_gb * _APPLE_USABLE:
        return "too_large"
    return "recommended" if (mem_gb - resident) >= _COMFORT_FREE_GB else "tight"


def max_params_b(
    mem_gb: float,
    *,
    kind: str = "gpu",
    context_k: float = 8.0,
    concurrency: int = 4,
    gb_per_b: float = _GB_PER_B_Q4,
    gpu_count: int = 1,
) -> float:
    """The largest model (in billions of params) the box can hold, given context + concurrency.

    ``gb_per_b`` is the memory per billion params: the 4-bit default suits quantized on-prem serving;
    pass ``_GB_PER_B_FP16`` for a cloud GPU running vLLM at full precision. ``gpu_count`` aggregates a
    single-node multi-GPU tensor-parallel serve (see ``usable_gb``); defaults to 1 (unchanged).
    """
    return (
        usable_gb(
            mem_gb, kind=kind, context_k=context_k, concurrency=concurrency, gpu_count=gpu_count
        )
        / gb_per_b
    )


@dataclass(frozen=True)
class GpuTier:
    """A selectable cloud GPU size. ``vram_gb`` drives which models fit (via the sizer); ``runpod_pool``
    is the RunPod serverless GPU-group id provisioning uses (tunable / overridable)."""

    key: str  # stable id stored on OrgSettings, e.g. "24"
    label: str  # shown in the picker
    vram_gb: int
    runpod_pool: str  # RunPod gpuIds group (AMPERE_24 is the known-good default)


# Cloud GPU sizes offered in the org picker. The VRAM is what gates the model list; the RunPod pool id is
# what provisioning sends (AMPERE_* are RunPod's serverless GPU groups; AMPERE_24 is the proven default).
GPU_TIERS: tuple[GpuTier, ...] = (
    GpuTier("24", "24 GB - entry GPU (A10 / RTX 4090 class)", 24, "AMPERE_24"),
    GpuTier("48", "48 GB - A6000 / L40S class", 48, "AMPERE_48"),
    GpuTier("80", "80 GB - A100 / H100 class", 80, "AMPERE_80"),
    GpuTier("141", "141 GB - H200 class", 141, "HOPPER_141"),
)


def gpu_tier(key: str) -> GpuTier | None:
    """Look up a GPU tier by its stored key; None if unset/unknown."""
    k = (key or "").strip()
    return next((t for t in GPU_TIERS if t.key == k), None)


def cloud_gpu_max_params(vram_gb: float, *, gpu_count: int = 1) -> float:
    """Largest model (billions) a cloud GPU of ``vram_gb`` can serve with vLLM at its default precision.

    vLLM loads weights at full precision (bf16/fp16) unless a quantized build is used, so this is the
    honest ceiling for a single serverless worker (1 concurrent stream, 8k context). It is much stricter
    than the on-prem 4-bit envelope - e.g. an 80 GB GPU serves ~37B at fp16, not ~130B at 4-bit. To run a
    bigger model on one GPU, point at a quantized (AWQ/GPTQ 4-bit) repo via the model picker's "Other".

    ``gpu_count`` > 1 sizes against a single-node multi-GPU tensor-parallel serve instead (``vram_gb``
    stays the PER-GPU size; see ``usable_gb``).
    """
    return max_params_b(
        vram_gb,
        kind="gpu",
        context_k=8.0,
        concurrency=1,
        gb_per_b=_GB_PER_B_FP16,
        gpu_count=gpu_count,
    )


def model_fits_vram(params_b: float, vram_gb: float) -> bool:
    """Whether a model of ``params_b`` billions fits a cloud GPU of ``vram_gb`` at full precision."""
    return params_b <= cloud_gpu_max_params(vram_gb)


def cloud_gpu_max_params_quantized(vram_gb: float, *, gpu_count: int = 1) -> float:
    """Largest model (billions) a cloud GPU of ``vram_gb`` can serve at **4-bit** (AWQ/GPTQ) - roughly
    4x the full-precision ceiling, so a 70B (~35 GB at 4-bit) fits a 48 GB GPU. Used for the gating when
    the org opts to serve a quantized build (the catalog's ``quant_hf_id``). ``gpu_count`` > 1 sizes
    against a single-node multi-GPU tensor-parallel serve instead (see ``cloud_gpu_max_params``)."""
    return max_params_b(
        vram_gb,
        kind="gpu",
        context_k=8.0,
        concurrency=1,
        gb_per_b=_GB_PER_B_AWQ,
        gpu_count=gpu_count,
    )


def servable_on_gpu(
    params_b: float,
    vram_gb: float,
    *,
    quantized: bool = False,
    has_quant: bool = False,
    gpu_count: int = 1,
) -> bool:
    """Whether ``gpu_count`` cloud GPU(s) of ``vram_gb`` each can actually serve a model of ``params_b``.

    This is the honest capability gate for the org / VPC picker. ``gpu_count`` == 1 (default) is a
    single GPU; > 1 is a single-node multi-GPU tensor-parallel serve (vLLM's ``--tensor-parallel-size``,
    docs/specs/multi-gpu-tensor-parallel-serving.md) - a model above either ceiling provisions a pod that
    then fails to load: money spent, no server. The picker greys such rows in the browser, but a direct
    POST bypasses that; this predicate is the server-side check so a doomed selection is refused rather
    than saved.

    4-bit roughly triples the ceiling, but only helps when the model has a real quant build
    (``quant_hf_id``); without one, ``servable_id`` falls back to fp16, so ``quantized`` without
    ``has_quant`` is judged at full precision - matching how the save route normalises the flag.
    """
    if params_b <= 0 or vram_gb <= 0:
        return False
    if quantized and has_quant:
        return params_b <= cloud_gpu_max_params_quantized(vram_gb, gpu_count=gpu_count)
    return params_b <= cloud_gpu_max_params(vram_gb, gpu_count=gpu_count)


# ── frontier models with no self-serve path ───────────────────────────────────────
#
# A few catalog entries (DeepSeek V4-Pro, GLM-5.x, Kimi K2.7/K3, ...) are too large for EITHER path
# Anthill can provision today: no local machine, and no cloud GPU node Anthill itself launches. Showing
# these as a permanently-disabled "needs more memory" row in the picker is dead clutter regardless of
# what hardware the user has - this classification lets the picker surface them differently (a
# "bring your own bigger infrastructure" note) instead.

_MAX_SELF_SERVE_GPU_COUNT = (
    8  # the largest genuine multi-GPU node Anthill provisions today - Lambda's
)
# NVLink-connected "8x" SKUs (docs/specs/multi-gpu-tensor-parallel-serving.md's own reference point:
# "8x H200 = 1128GB"). Not a hard platform ceiling, just today's largest wired-up config; revisit if a
# bigger node type is ever added.
_MAX_SELF_SERVE_VRAM_GB = 141.0  # the largest GPU_TIERS entry (H200-class).
_MAX_SELF_SERVE_LOCAL_MEM_GB = (
    512.0  # the largest unified-memory Apple Silicon config available today
)
# (Mac Studio, top configuration) - a generous stand-in for "the biggest realistic local machine",
# independent of any specific user's actual box (fit_tier already handles that per-machine question).


def largest_self_servable_params_b(model: Model) -> float:
    """The largest model size (billions of params) that COULD be self-served for `model` on the biggest
    path Anthill wires up today - whichever is more generous of (a) a maxed-out local machine
    (``_MAX_SELF_SERVE_LOCAL_MEM_GB`` at 4-bit) or (b) Anthill's largest cloud GPU node
    (``_MAX_SELF_SERVE_VRAM_GB`` x ``_MAX_SELF_SERVE_GPU_COUNT``, quantized when `model` has a verified
    ``quant_hf_id`` else the stricter fp16 ceiling ``servable_on_gpu`` itself falls back to - matching
    that function's own precision rule). Independent of any one user's actual hardware; see
    ``has_any_self_serve_path`` for the per-model yes/no this feeds."""
    local_ceiling = max_params_b(
        _MAX_SELF_SERVE_LOCAL_MEM_GB, kind="apple", context_k=8.0, concurrency=1
    )
    cloud_ceiling = (
        cloud_gpu_max_params_quantized(_MAX_SELF_SERVE_VRAM_GB, gpu_count=_MAX_SELF_SERVE_GPU_COUNT)
        if model.quant_hf_id
        else cloud_gpu_max_params(_MAX_SELF_SERVE_VRAM_GB, gpu_count=_MAX_SELF_SERVE_GPU_COUNT)
    )
    return max(local_ceiling, cloud_ceiling)


def has_any_self_serve_path(model: Model) -> bool:
    """Whether ANY path Anthill provisions today - some local machine, or Anthill's own largest cloud GPU
    node - could realistically serve `model` at all. False means the only route in is the user separately
    connecting their own externally-hosted endpoint (self-provisioned AWS/GCP/Azure, or a third-party API)
    - the picker surfaces these under a distinct "frontier models" section rather than as a dead,
    permanently-disabled row in the regular list."""
    return model.params_b <= largest_self_servable_params_b(model)


def gpu_count_divides_heads(num_attention_heads: int, gpu_count: int) -> bool:
    """Whether ``gpu_count`` is a valid vLLM tensor-parallel size for a model with
    ``num_attention_heads`` attention heads - vLLM requires it to divide evenly. ``num_attention_heads``
    <= 0 means unknown/uncurated (see ``Model.num_attention_heads``) and is treated as non-blocking:
    vLLM's own runtime refusal is the backstop when this pre-flight check can't verify it. ``gpu_count``
    <= 1 (no tensor-parallel) is always fine."""
    if num_attention_heads <= 0 or gpu_count <= 1:
        return True
    return num_attention_heads % gpu_count == 0


def recommend(
    mem_gb: float,
    *,
    kind: str = "gpu",
    context_k: float = 8.0,
    concurrency: int = 4,
    catalog: tuple[Model, ...] | None = None,
) -> Sizing:
    """Recommend the SMARTEST catalog model that runs well on a box with `mem_gb` of memory.

    kind: "apple" (unified memory) or "gpu" (dedicated VRAM).
    context_k: target context window in thousands of tokens. concurrency: expected concurrent streams.
    Bigger context or concurrency shrinks the recommendation (the KV cache eats memory).
    catalog defaults to the live (refreshable) catalog, so a Refresh takes effect without a restart.
    """
    if catalog is None:
        catalog = load_catalog()
    ceiling = max_params_b(mem_gb, kind=kind, context_k=context_k, concurrency=concurrency)
    best: Model | None = None
    for m in catalog:
        fits_mem = m.params_b <= ceiling
        if (
            fits_mem
            and is_usable_speed(m, kind=kind)
            and (best is None or (m.intelligence, m.params_b) > (best.intelligence, best.params_b))
        ):
            best = m  # smartest that fits AND is fast enough (intelligence first, larger as the tiebreak)
    fits = [
        ModelFit(
            model=m,
            fits=(m.params_b <= ceiling),
            recommended=(best is not None and m.name == best.name),
            too_slow=(m.params_b <= ceiling and not is_usable_speed(m, kind=kind)),
        )
        for m in catalog
    ]
    return Sizing(recommended=best, max_params_b=ceiling, fits=fits)


# ── local (on-device, single-user) model picker ──────────────────────────────────
# One frontier catalog serves both surfaces (CATALOG, from the bundled/refreshable JSON). The local
# picker groups it by family, ordered by intelligence (strongest family first), and offers the SMARTEST
# model that fits this machine per family - a 3B-active MoE can beat a dense 32B. All run on-device via the
# bundled Ollama (4-bit); the choice is about preference/provenance, not data (open weights send nothing).
LOCAL_CATALOG: tuple[Model, ...] = CATALOG
# Display order: strongest family first, by its best model's intelligence. The default family tab is the
# strongest overall - intelligence-first, origin-blind (the old non-Chinese default is retired).
LOCAL_FAMILIES: tuple[str, ...] = families_by_intelligence(CATALOG)
DEFAULT_LOCAL_FAMILY = LOCAL_FAMILIES[0] if LOCAL_FAMILIES else "Qwen"


@dataclass
class FamilyPick:
    """The best on-device choice within one model family for this machine."""

    family: str
    origin: str
    recommended: Model | None  # largest model in the family that fits, or None if none fit
    download_gb: float  # approx 4-bit download/disk size of the recommended model (0 if none)


def family_download_gb(params_b: float) -> float:
    """Approximate 4-bit on-disk / download size (GB) for a model of ``params_b`` billions."""
    return round(params_b * _GB_PER_B_Q4, 1)


def free_disk_gb(path: str | None = None) -> float | None:
    """Best-effort free disk space (GB) for the volume a local model would be stored on.

    Defaults to ``anthill.backup.ollama_models_dir()`` - the same OLLAMA_MODELS-aware path this
    codebase already uses for backups - rather than re-deriving the same env-var lookup here. Read
    lazily (not cached at import time) so a test that sets OLLAMA_MODELS still sees it. None on any
    failure, following ``free_mem_gb()``'s convention: never raise, never block a save on a bad probe.
    """
    from ..backup import ollama_models_dir

    target = path if path is not None else str(ollama_models_dir())
    try:
        return shutil.disk_usage(target).free / (1024**3)
    except Exception:
        return None


def onprem_council_fits_on_disk(member_params_b: list[float], *, path: str | None = None) -> bool:
    """Do all proposed on-prem models fit on local disk together, with comfortable headroom?

    Mirrors ``onprem_council_fits``'s shape (a list of params_b in, a bool out) and its fail-open
    behavior, but is deliberately a SEPARATE function rather than folded into it: disk and memory are
    independent preconditions, and keeping them separate means neither existing function's callers or
    tests change. The onboarding flow (a later change) calls both gates together.
    """
    if not member_params_b:
        return True
    available_gb = free_disk_gb(path)
    if available_gb is None:
        return True  # could not be probed; do not block on a bad probe
    required_gb = sum(family_download_gb(max(0.0, pb)) for pb in member_params_b)
    return required_gb + _DISK_COMFORT_FREE_GB <= available_gb


def recommend_by_family(
    mem_gb: float,
    *,
    kind: str = "apple",
    context_k: float = 8.0,
    concurrency: int = 1,
    catalog: tuple[Model, ...] | None = None,
    families: tuple[str, ...] | None = None,
) -> list[FamilyPick]:
    """For each family, the SMARTEST model that fits this machine (the local picker).

    Local serving is single-user (concurrency 1) and 4-bit. Returns one FamilyPick per family in
    ``families`` order; ``recommended`` is None for a family whose smallest model still does not fit.
    Catalog + family order default to the live (refreshable) catalog.
    """
    if catalog is None:
        catalog = load_catalog()
    if families is None:
        families = families_by_intelligence(catalog)
    ceiling = max_params_b(mem_gb, kind=kind, context_k=context_k, concurrency=concurrency)
    picks: list[FamilyPick] = []
    for fam in families:
        members = [m for m in catalog if m.family == fam]
        best: Model | None = None
        for m in members:
            if (
                m.params_b <= ceiling
                and is_usable_speed(m, kind=kind)
                and (
                    best is None
                    or (m.intelligence, m.params_b) > (best.intelligence, best.params_b)
                )
            ):
                best = m  # smartest that fits AND is fast enough, within the family
        origin = members[0].origin if members else ""
        picks.append(
            FamilyPick(
                family=fam,
                origin=origin,
                recommended=best,
                download_gb=family_download_gb(best.params_b) if best else 0.0,
            )
        )
    return picks


@dataclass
class LocalSetupSuggestion:
    """What to suggest for THIS machine's local setup: a family-diverse council, or one model."""

    mode: str  # "council" | "single"
    members: list[FamilyPick]  # 3 for council, 1 for single (0 only if nothing fits at all)
    hw_label: str


def suggest_local_setup(
    mem_gb: float, kind: str, *, catalog: tuple[Model, ...] | None = None
) -> LocalSetupSuggestion:
    """Try a 3-member family-diverse local council first (the reached-for default); fall back to the
    single smartest model only when the council does not fit - in memory OR on disk. This is the ONE
    place this decision is made; do not duplicate the fallback logic elsewhere.

    Built on ``recommend_by_family`` (already family-diverse, strongest-family-first, fit+speed
    checked) - NOT ``suggest_regional_council``, which picks a geographically-diverse REMOTE council
    (US/EU/China) and is unrelated to local hardware capacity.
    """
    picks = recommend_by_family(mem_gb, kind=kind, catalog=catalog)
    fitting = [p for p in picks if p.recommended is not None]
    hw_label = f"{mem_gb:.0f} GB {kind}"
    if not fitting:
        return LocalSetupSuggestion(mode="single", members=[], hw_label=hw_label)

    top3 = fitting[:3]
    if len(top3) == 3:
        params = [p.recommended.params_b for p in top3 if p.recommended is not None]
        if onprem_council_fits(params) and onprem_council_fits_on_disk(params):
            return LocalSetupSuggestion(mode="council", members=top3, hw_label=hw_label)

    return LocalSetupSuggestion(mode="single", members=[fitting[0]], hw_label=hw_label)


def _macos_mem_gb() -> float | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip()) / (1024**3)
    except Exception:
        pass
    return None


# ── MoE speed-awareness ──────────────────────────────────────────────────────────
#
# A model's MEMORY footprint scales with its total params (params_b) - every expert must be resident,
# whether or not it's used this token. Its SPEED does not: a Mixture-of-Experts model only computes its
# active experts per token (active_b), so it can run far faster than a dense model of the same total
# size. Neither params_b nor intelligence captures this - a model can be memory-feasible and smart enough
# yet still too slow to be a usable product, dense or MoE alike. This is a real, general concern (see
# _MIN_USABLE_TOK_S below): a large dense model on modest hardware is just as capable of being "too slow
# to work" as an under-provisioned MoE model - the fix here applies to both, not just MoE.
#
# The speed floor is a HARD exclusion, not a soft preference: a model estimated below it is not a real
# option, independent of how smart it is. This deliberately avoids trading intelligence against speed
# (no formula, no user setting needed) - it only answers "is this usable at all," then the existing
# intelligence-based ranking (see `recommend`/`recommend_by_family`) is unchanged among whatever survives.

_MIN_USABLE_TOK_S = 10.0
# ~10 tokens/sec (100ms/token) is where streamed text stops feeling like it's halting and starts feeling
# smooth - the floor below which an interactive chat model is friction, not a real option, regardless of
# intelligence. (~6 tok/s is roughly average human reading speed - the even-more-conservative floor below
# which the model is literally slower than a person reads; ~40 tok/s is where the experience separately
# transforms from "smooth" to "fast", with diminishing returns beyond that as reading speed itself becomes
# the bottleneck.)

_BYTES_PER_PARAM_Q4 = 0.5  # 4-bit quantization, bytes per parameter

_MOE_OVERHEAD_FACTOR = 0.45
# The naive estimate (bandwidth / (active_b * bytes_per_param)) overestimates a MoE model's real-world
# throughput specifically - expert routing/gather has a real cost dense models don't pay, on top of the
# attention compute and KV cache reads a pure memory-bandwidth model doesn't capture either. Calibrated
# against a real, independently-published MoE data point: DeepSeek-V3 (active_b=37B, 4-bit) on an Apple
# M3 Ultra (819 GB/s) measures ~17-21 tok/s in third-party benchmarks (VentureBeat, MacRumors, Hardware
# Corner, MacStories). Naive estimate: 819 / (37 * 0.5) = 44.3 tok/s. 44.3 * 0.45 = 19.9 tok/s - within
# the measured range.
#
# This factor does NOT apply to dense models - verified against real published dense-model benchmarks
# (llama.cpp/M-series, multiple sources): Llama 3.3 70B (4-bit) measures ~12.5 tok/s on an M4 Max (546
# GB/s) and ~7.5-11.2 tok/s on an M3 Max (400 GB/s). The naive, UNCALIBRATED estimate (546/35=15.6,
# 400/35=11.4) already lands close to these measured ranges - applying the MoE overhead factor on top
# would have UNDER-estimated real dense-model speed by roughly 2x (a mistake caught during review: doing
# so wrongly excluded Llama 3.3 70B as "too slow" on hardware where it actually runs acceptably). This is
# still a first-order approximation for either case, not a guarantee.

# ── GPU-offload speed-awareness (docs/specs/local-moe-offload-fit.md) ────────────────────────────────
#
# Ollama (llama.cpp) already auto-offloads layers/experts to system RAM when a model exceeds a discrete
# GPU's VRAM. For a MoE model this can still be usable (decode only touches the ACTIVE experts per
# token), but the physics differ enough from Apple's unified memory that neither `_MOE_OVERHEAD_FACTOR`
# nor a plain, unscaled `active_b / bandwidth` estimate is safe to reuse - see the three constants below,
# calibrated against the two real hands-on data points in the spec (from the 2026 build guide's
# `Open Model Index/running-big-models-on-small-hardware-2026.md`).

_GPU_OFFLOAD_RAM_BANDWIDTH_GBPS = 60.0
# Conservative default for a modern discrete-GPU box's system RAM (DDR5 dual-channel spans roughly
# 60-90 GB/s depending on speed grade/channel population). Unlike Apple Silicon (`_APPLE_CHIP_BANDWIDTH_GBPS`,
# looked up per exact chip), there is no portable, unprivileged way to read the real DDR generation or
# channel count (dmidecode needs root) - so this is a single fixed, deliberately LOW-end constant.
# Under-predicting only wrongly excludes a marginal offload case (safe); over-predicting would offer a
# model that stutters in practice (the failure mode this whole change exists to avoid). VERIFY against a
# couple of real DDR5 measurements before leaning on this for a stronger claim than "a conservative
# default" - same caution as the Apple chip-bandwidth table below.

_GPU_OFFLOAD_RAM_HEADROOM_GB = 8.0
# Free system RAM the OS + Ollama's own runner overhead should keep beyond the offloaded weight slice -
# the RAM-side analogue of `_GPU_OVERHEAD_GB` (VRAM) / `_LOCAL_RUNNER_GB` (Apple unified memory). A
# deliberately round, conservative reservation, not a measured figure.

_MODEST_OFFLOAD_FRACTION_MAX = 0.70
# The largest fraction of a model's 4-bit weight bytes allowed to spill to system RAM before it is
# refused outright (`too_large`) rather than estimated. Calibrated to separate the two real data points
# in the spec: a Qwen3.6 35B-A3B on a 12 GB card + 32 GB RAM (offloaded_fraction ~= 0.68 by this file's
# own `usable_gb` math: weight_gb=35*0.55=19.25, vram_for_weights=usable_gb(12,"gpu",8,1)=6.2,
# offloaded=13.05, 13.05/19.25=0.678) measures ~50 tok/s and must be offered; a gpt-oss-120B on a 24 GB
# 3090 (offloaded_fraction ~= 0.72: weight_gb=66.0, vram_for_weights=usable_gb(24,"gpu",8,1)=18.2,
# offloaded=47.8, 47.8/66.0=0.724) measures only ~1.6-10 tok/s (PCIe/GPU-generation bound, NOT a bandwidth
# problem this estimate can capture) and must stay `too_large`. 0.70 sits near the midpoint of the two -
# a coarse, product-level cutoff, not a precise physical derivation (the real bottleneck past this point
# is expert-scatter/PCIe overhead, not modeled here at all).

_GPU_OFFLOAD_CALIBRATION_FACTOR = 0.8
# Applied to the naive `bandwidth / (active_b * offloaded_fraction * _BYTES_PER_PARAM_Q4)` estimate.
# Deliberately NOT the Apple `_MOE_OVERHEAD_FACTOR` (0.45) - that figure is unified-memory-calibrated
# (attention/router/shared-experts/KV all share the SAME bus as the offloaded weights), whereas on GPU
# offload those stay resident on fast VRAM and only the routed experts stream from system RAM, so 0.45
# would under-predict GPU offload by roughly 2.4x. Worked check against the 35B-A3B anchor above: naive =
# 60 / (3 * 0.678 * 0.5) ~= 59.0 tok/s; 59.0 * 0.8 ~= 47.2 tok/s - safely under (never over) the ~50 tok/s
# measured, and comfortably clears `_MIN_USABLE_TOK_S`.

# Apple Silicon chip -> memory bandwidth (GB/s). Gathered from public benchmarks, NOT Apple's own spec
# sheets - verify against support.apple.com before adding a chip or trusting this for a new product
# claim. An unrecognized chip (including any not yet listed here) deliberately returns no bandwidth, so
# it never trips the speed floor - see `_apple_chip_bandwidth_gbps`.
_APPLE_CHIP_BANDWIDTH_GBPS: dict[str, float] = {
    "M1": 70.0,
    "M2": 102.0,
    "M3 Pro": 154.0,
    "M3 Max": 400.0,
    "M3 Ultra": 819.0,
    "M4": 120.0,
    "M4 Pro": 273.0,
    "M4 Max": 546.0,
    "M5": 154.0,
    "M5 Pro": 307.0,
}


def _apple_chip_brand() -> str | None:
    """The exact Apple Silicon chip name (e.g. "Apple M3 Ultra") via sysctl, or None off Apple Silicon or
    if the probe fails."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return None
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _apple_chip_bandwidth_gbps() -> float | None:
    """This machine's Apple Silicon memory bandwidth (GB/s), or None if not Apple Silicon or the exact
    chip isn't in `_APPLE_CHIP_BANDWIDTH_GBPS`. Deliberately returns None for an unrecognized chip - a
    caller must treat that as "cannot estimate speed", never guess a bandwidth figure."""
    brand = _apple_chip_brand()
    if not brand:
        return None
    # Longest key first: "M3 Ultra" must match before a hypothetical bare "M3" entry would.
    for name in sorted(_APPLE_CHIP_BANDWIDTH_GBPS, key=len, reverse=True):
        if name in brand:
            return _APPLE_CHIP_BANDWIDTH_GBPS[name]
    return None


def estimate_tokens_per_second(
    model: Model,
    *,
    kind: str,
    vram_gb: float = 0.0,
    system_ram_gb: float | None = None,
    context_k: float = 8.0,
    concurrency: int = 1,
) -> float | None:
    """First-order tokens/sec estimate for serving `model` locally on THIS machine, or None if it can't
    be estimated - callers must not apply a speed floor when this returns None (unrecognized/non-Apple
    hardware degrades to today's memory-only behavior, never an optimistic or pessimistic guess).

    Uses `active_b` (falling back to `params_b` for a dense model, where `active_b` is 0 or unset) and
    this machine's memory bandwidth. The MoE overhead factor applies ONLY when the model is genuinely a
    MoE (`active_b` less than `params_b`) - a dense model uses the raw bandwidth-bound estimate directly,
    which matches real dense-model benchmarks much more closely than the MoE-calibrated figure would (see
    `_MOE_OVERHEAD_FACTOR`'s comment).

    On a discrete GPU (`kind == "gpu"`), this only ever estimates the CPU-offload case: a MoE model whose
    weights overflow VRAM (`vram_gb`) but spill a MODEST slice to system RAM (`system_ram_gb`) - see
    `docs/specs/local-moe-offload-fit.md`. Returns None (cannot estimate, never guess) unless ALL of:
    `vram_gb`/`system_ram_gb` are known, the model is genuinely a MoE, its weights actually overflow VRAM
    (a model that fits outright isn't an offload case), the combined VRAM+RAM budget holds it with
    headroom, and the offloaded fraction stays under `_MODEST_OFFLOAD_FRACTION_MAX` - past that point real
    speed is PCIe/GPU-generation bound, which this bandwidth-only estimate cannot capture.
    """
    if kind == "apple":
        bandwidth = _apple_chip_bandwidth_gbps()
        if bandwidth is None:
            return None
        is_moe = model.active_b > 0.0 and model.active_b < model.params_b
        active_b = model.active_b if model.active_b > 0.0 else model.params_b
        if active_b <= 0:
            return None
        naive = bandwidth / (active_b * _BYTES_PER_PARAM_Q4)
        return naive * _MOE_OVERHEAD_FACTOR if is_moe else naive

    if kind != "gpu" or vram_gb <= 0 or not system_ram_gb or system_ram_gb <= 0:
        return None
    is_moe = model.active_b > 0.0 and model.active_b < model.params_b
    if not is_moe or model.params_b <= 0:
        return None  # dense overflow: no offload path exists, nothing to estimate
    weight_gb = model.params_b * _GB_PER_B_Q4
    vram_for_weights = usable_gb(vram_gb, kind="gpu", context_k=context_k, concurrency=concurrency)
    if weight_gb <= vram_for_weights:
        return None  # fits VRAM outright - not an offload case
    offloaded_gb = weight_gb - vram_for_weights
    usable_ram = max(0.0, system_ram_gb - _GPU_OFFLOAD_RAM_HEADROOM_GB)
    if offloaded_gb > usable_ram:
        return None  # doesn't fit VRAM + system RAM combined even with headroom reserved
    offloaded_fraction = offloaded_gb / weight_gb
    if offloaded_fraction > _MODEST_OFFLOAD_FRACTION_MAX:
        return None  # mostly offloaded: PCIe/GPU-generation bound, not reliably estimable
    denom = model.active_b * offloaded_fraction * _BYTES_PER_PARAM_Q4
    if denom <= 0:
        return None
    return (_GPU_OFFLOAD_RAM_BANDWIDTH_GBPS / denom) * _GPU_OFFLOAD_CALIBRATION_FACTOR


def is_usable_speed(model: Model, *, kind: str) -> bool:
    """Whether `model` is estimated to run fast enough to be worth recommending on THIS machine - applies
    to every model, dense or MoE (a large dense model on modest hardware can be just as unusably slow as
    an under-provisioned MoE model). Returns True (never excludes) when speed can't be estimated."""
    est = estimate_tokens_per_second(model, kind=kind)
    if est is None:
        return True
    return est >= _MIN_USABLE_TOK_S


def _nvidia_vram_gb() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            **hidden_window_kwargs(),
        )
        if out.returncode == 0:
            vals = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
            if vals:
                return max(vals) / 1024  # MiB -> GB (largest single GPU)
    except Exception:
        pass
    return None


def _posix_ram_gb() -> float | None:
    """Total physical RAM in GB on Linux (and, through the platform layer, on Windows)."""
    if platform.system() == "Windows":
        memory = memory_gb()
        return memory[0] if memory else None
    try:
        import os

        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) / (1024**3)
    except (ValueError, OSError, AttributeError):
        return None


def _parse_vm_stat_free_gb(text: str) -> float | None:
    """Free + inactive memory (GB) from ``vm_stat`` output. Inactive pages are reclaimable, so they
    count as effectively available for a new allocation."""

    def _pages(line: str) -> int | None:
        try:
            return int(line.split(":", 1)[1].strip().rstrip("."))
        except (IndexError, ValueError):
            return None

    page = 4096
    free = inactive = None
    for line in text.splitlines():
        if "page size of" in line and "bytes" in line:
            try:
                page = int(line.split("page size of", 1)[1].split("bytes", 1)[0].strip())
            except (IndexError, ValueError):
                pass
        elif line.startswith("Pages free:"):
            free = _pages(line)
        elif line.startswith("Pages inactive:"):
            inactive = _pages(line)
    if free is None:
        return None
    return (free + (inactive or 0)) * page / (1024**3)


def free_mem_gb() -> float | None:
    """Best-effort FREE/available system memory in GB - distinct from ``local_hardware``'s TOTAL. Used
    by the runtime to avoid loading a second model under memory pressure (issue #413). macOS reads
    ``vm_stat`` (free + inactive pages); Linux reads ``/proc/meminfo`` MemAvailable. None if unknown."""
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            return _parse_vm_stat_free_gb(out.stdout) if out.returncode == 0 else None
        if system == "Windows":
            memory = memory_gb()
            return memory[1] if memory else None
        with open("/proc/meminfo") as fh:  # Linux
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024**2)  # kB -> GB
    except Exception:
        return None
    return None


def local_hardware() -> tuple[float, str]:
    """Probe THIS machine for the model picker: ``(memory_gb, kind)`` where kind is
    ``apple`` (Apple-Silicon unified memory), ``gpu`` (an NVIDIA GPU - VRAM gates the choice), or
    ``apple`` as a conservative default for a plain CPU box. Returns ``(0.0, "apple")`` if memory
    cannot be read (the picker then offers only the smallest models)."""
    system = platform.system()
    if system == "Darwin" and platform.machine() == "arm64":
        return (_macos_mem_gb() or 0.0, "apple")
    vram = _nvidia_vram_gb()
    if vram:
        return (vram, "gpu")
    ram = (_macos_mem_gb() if system == "Darwin" else _posix_ram_gb()) or 0.0
    return (ram, "apple")  # CPU box: use the conservative unified-memory fraction


def gpu_box_system_ram_gb() -> float | None:
    """Total system RAM (GB) on a discrete-GPU box - the CPU-offload spill target when a model's 4-bit
    footprint exceeds VRAM (see ``docs/specs/local-moe-offload-fit.md``). A separate probe from
    ``local_hardware()`` (whose ``(mem_gb, kind)`` contract reports VRAM as the "fast pool" for a gpu box
    and stays unchanged for every existing caller) - only the new offload-aware call sites need this
    additional figure. None if it can't be read; callers must treat that as "cannot estimate offload,"
    never a guess."""
    return _macos_mem_gb() if platform.system() == "Darwin" else _posix_ram_gb()
