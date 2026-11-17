"""Memory a loaded model needs, from the model's own metadata (#223).

The flat ``_KV_PER_K_PER_STREAM`` guess in ``sizing.py`` charges every model the same KV cache per 1,000
tokens, though the real cost differs several-fold between architectures. This reads the layer count, KV heads,
head size, sliding window and recurrent layers from Ollama's ``/api/show`` and works the cache out the way
llama.cpp's server, which Ollama runs the model in, allocates it. It never guesses: a model whose metadata is
missing, odd or not understood gives ``None``, and the caller keeps the flat figure. Where the rule is not
certain it counts more memory, never less.

Four figures are reported apart, each marked ``estimated`` or ``measured``:

- ``weight``: the model file.
- ``allocated``: weights, the KV cache at the window in use, and the compute buffer. What Ollama reserves for
  the loaded model, and what ``ollama ps`` shows as SIZE.
- ``resident``: the part of that held in GPU (on a Mac, unified) memory. ``ollama ps`` SIZE_VRAM.
- ``peak``: allocated plus the runner's working set, the figure the fit rules compare with memory.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

GB = 1e9  # the sizes Ollama reports are bytes; the fit rules count decimal gigabytes

# Bytes per cache element. f16 is the default; q8_0 and q4_0 are the OLLAMA_KV_CACHE_TYPE options
# (ggml block sizes: 34 bytes per 32 values, and 18 per 32).
KV_BYTES_PER_ELEMENT = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}

# llama.cpp (b12257, the version Ollama main pins on 2026-11-17) rounds cache sizes up to a multiple of 256
# cells, and sizes a sliding-window layer's cache as the window plus one micro-batch. Ollama's own batch
# flags were not read; 512 is llama.cpp's default micro-batch.
CACHE_PAD = 256
MICRO_BATCH = 512

# Architectures whose metadata carries a sliding window but not the pattern; the number is the period: every
# Nth layer is global, the rest local (llama.cpp load_swa_pattern defaults: gemma3 6, gpt-oss 2). Ollama's
# blobs name gpt-oss "gptoss". Any other architecture with a window key is counted as all global.
_SWA_PERIOD = {"gemma3": 6, "gpt-oss": 2, "gptoss": 2}

# Hybrid architectures: layers are either attention (a per-token cache) or recurrent (a constant state).
_HYBRID = {"qwen3next", "qwen35", "qwen35moe"}
_FULL_ATTENTION_INTERVAL = 4  # llama.cpp default: every 4th layer is attention

Basis = Literal["estimated", "measured"]


def _pad(cells: int) -> int:
    return -(-cells // CACHE_PAD) * CACHE_PAD


@dataclass(frozen=True)
class AttentionLayer:
    """One layer's cache: ``kv_heads`` of (key + value) values per token. ``window`` is the sliding window of a
    local layer, None for a layer that keeps every token. A layer with no cache has 0 KV heads."""

    kv_heads: int
    key_length: int
    value_length: int
    window: int | None = None


@dataclass(frozen=True)
class ModelArch:
    """The shape of a model's attention, read from ``/api/show``."""

    architecture: str
    layers: tuple[AttentionLayer, ...]
    recurrent_state_bytes: int = 0  # constant, whatever the window (hybrid models)
    context_length: int | None = None

    def kv_bytes(self, num_ctx: int, kv_type: str = "f16") -> float | None:
        """Cache bytes at a window of ``num_ctx`` tokens, one sequence. None for an unknown cache type or a
        window that is not positive. The recurrent state is always f32, whatever the cache type."""
        per_element = KV_BYTES_PER_ELEMENT.get(kv_type)
        if per_element is None or num_ctx <= 0:
            return None
        total = float(self.recurrent_state_bytes)
        for layer in self.layers:
            cells = _pad(num_ctx)
            if layer.window is not None:
                cells = min(cells, _pad(min(num_ctx, layer.window + MICRO_BATCH)))
            total += layer.kv_heads * (layer.key_length + layer.value_length) * per_element * cells
        return total

    def kv_gb_per_k(self, num_ctx: int, kv_type: str = "f16") -> float | None:
        """The figure ``sizing`` takes in place of its flat guess: decimal GB per 1,000 tokens, averaged over a
        window of ``num_ctx`` tokens (a sliding-window cache does not grow with the window)."""
        total = self.kv_bytes(num_ctx, kv_type)
        return None if total is None else total / GB / (num_ctx / 1000)


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value > 0 and value == int(value) else None


def _per_layer(value: object, layers: int) -> tuple[int, ...] | None:
    """A per-layer count from either one whole number (the same for every layer) or one number per layer."""
    if isinstance(value, list):
        if len(value) != layers:
            return None
        counts = [
            v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None for v in value
        ]
        return None if None in counts else tuple(counts)  # type: ignore[arg-type]
    n = _whole(value)
    return None if n is None else (n,) * layers


def _head_shape(
    get: Callable[[str], object], layers: int
) -> tuple[tuple[int, ...], int, int] | None:
    """KV heads per layer, key length and value length; None when any of it is missing or odd."""
    heads = get("attention.head_count")
    kv = get("attention.head_count_kv")
    # No grouped-query attention key: one cache head per attention head.
    kv_heads = _per_layer(heads if kv is None else kv, layers)
    heads_per_layer = _per_layer(heads, layers)
    if kv_heads is None or heads_per_layer is None:
        return None
    key_len = _whole(get("attention.key_length"))
    value_len = _whole(get("attention.value_length"))
    if key_len is None or value_len is None:
        # The usual default: the embedding is split evenly between the attention heads.
        embedding = _whole(get("embedding_length"))
        first = next((h for h in heads_per_layer if h), None)
        if embedding is None or first is None or embedding % first:
            return None
        key_len = key_len or embedding // first
        value_len = value_len or embedding // first
    return kv_heads, key_len, value_len


def _local_layers(
    name: str, get: Callable[[str], object], layers: int
) -> tuple[int | None, list[bool]]:
    """The sliding window and which layers use it. A per-layer pattern array is not decoded (which value means
    "local" was not confirmed): every layer is then counted as global, which over-counts the cache and never
    under-counts it."""
    window = _whole(get("attention.sliding_window"))
    pattern = get("attention.sliding_window_pattern")
    local = [False] * layers
    if window is not None and not isinstance(pattern, list):
        period = _whole(pattern) or _SWA_PERIOD.get(name)
        if period:
            local = [(i % period) < period - 1 for i in range(layers)]
    return window, local


def _hybrid_layout(
    get: Callable[[str], object], layers: int, kv_heads: tuple[int, ...]
) -> tuple[list[bool], int, tuple[int, ...]] | None:
    """Which layers are recurrent, the bytes of their constant state, and KV heads with the recurrent layers
    zeroed. None when the layout is not understood (a per-layer recurrent list, or missing state keys)."""
    if get("attention.recurrent_layers") is not None:
        return None  # a per-layer list of recurrent layers: its meaning was not confirmed
    interval = _whole(get("full_attention_interval")) or _FULL_ATTENTION_INTERVAL
    recurrent = [(i + 1) % interval != 0 for i in range(layers)]
    conv_kernel, inner = _whole(get("ssm.conv_kernel")), _whole(get("ssm.inner_size"))
    d_state, groups = _whole(get("ssm.state_size")), _whole(get("ssm.group_count"))
    if conv_kernel is None or inner is None or d_state is None or groups is None:
        return None
    # llama.cpp n_embd_r + n_embd_s per recurrent layer, f32. The qwen35 layers were not confirmed to use
    # exactly this shape; it is a constant per sequence and small next to the weights.
    per_layer = (conv_kernel - 1) * (inner + 2 * groups * d_state) + d_state * inner
    # Published blobs may give one KV head count for every layer, recurrent ones included.
    top = max(kv_heads, default=0)
    heads = tuple(0 if recurrent[i] else (kv_heads[i] or top) for i in range(layers))
    return recurrent, sum(recurrent) * per_layer * 4, heads


def arch_from_show(show: dict) -> ModelArch | None:
    """Read the attention shape from an ``/api/show`` reply, or None when any part of it is missing, odd or
    not understood (an embedding model, a model with no attention keys, a layer count that disagrees with its
    per-layer arrays, a per-layer recurrent array whose meaning is not known). None means "use the flat
    estimate", never "zero cache"."""
    info = show.get("model_info") if isinstance(show, dict) else None
    name = info.get("general.architecture") if isinstance(info, dict) else None
    if not isinstance(info, dict) or not isinstance(name, str) or not name:
        return None

    def get(key: str) -> object:
        return info.get(f"{name}.{key}")

    layers = _whole(get("block_count"))
    shape = _head_shape(get, layers) if layers is not None else None
    if layers is None or shape is None:
        return None
    kv_heads, key_len, value_len = shape
    state_bytes = 0
    if name in _HYBRID:
        hybrid = _hybrid_layout(get, layers, kv_heads)
        if hybrid is None:
            return None
        _, state_bytes, kv_heads = hybrid
    # A local layer can have its own head size (gemma4); a global layer is counted at the larger of the two,
    # so an unknown split never lowers the figure.
    key_swa = _whole(get("attention.key_length_swa")) or key_len
    value_swa = _whole(get("attention.value_length_swa")) or value_len
    window, local = _local_layers(name, get, layers)
    return ModelArch(
        architecture=name,
        layers=tuple(
            AttentionLayer(
                kv_heads=kv_heads[i],
                key_length=key_swa if local[i] else max(key_len, key_swa),
                value_length=value_swa if local[i] else max(value_len, value_swa),
                window=window if local[i] else None,
            )
            for i in range(layers)
        ),
        recurrent_state_bytes=state_bytes,
        context_length=_whole(get("context_length")),
    )


# The compute (graph) buffer Ollama reserves grows with the window. Read off `ollama ps` on Ollama 0.40.7
# (llama.cpp runner, f16 cache) by loading the same model at two windows and taking what is left after the
# weights and the cache: qwen2.5:3b 20.2 KB per token at 4,096 and 21.9 KB at 8,192; granite3.2-vision:2b
# 19.5 KB per token between the two. Two models on one machine and one engine: a rough figure, rounded up.
COMPUTE_BUFFER_BYTES_PER_TOKEN = 22_000


@dataclass(frozen=True)
class Figure:
    gb: float
    basis: Basis


@dataclass(frozen=True)
class MemoryReport:
    """Weight, allocated, resident and peak memory in decimal gigabytes, each marked ``estimated`` or
    ``measured`` (see the module note for what each one is)."""

    num_ctx: int
    weight: Figure | None  # None: the file size was not given, never a zero
    allocated: Figure
    resident: Figure
    peak: Figure


def _runner_gb() -> float:
    from .sizing import _LOCAL_RUNNER_GB

    return _LOCAL_RUNNER_GB


def estimate_memory(
    arch: ModelArch, weight_bytes: int, num_ctx: int, kv_type: str = "f16"
) -> MemoryReport | None:
    """Everything estimated from the metadata and the model file's size. None when the cache cannot be worked
    out (an unknown cache type). The file size is the weight, so a vision model, whose file holds an image
    encoder the text model never loads, is over-counted by that encoder."""
    kv = arch.kv_bytes(num_ctx, kv_type)
    if kv is None or weight_bytes <= 0:
        return None
    weight = weight_bytes / GB
    allocated = weight + (kv + COMPUTE_BUFFER_BYTES_PER_TOKEN * num_ctx) / GB
    return MemoryReport(
        num_ctx=num_ctx,
        weight=Figure(weight, "estimated"),
        allocated=Figure(allocated, "estimated"),
        resident=Figure(allocated, "estimated"),  # a full offload, the case on a Mac
        peak=Figure(allocated + _runner_gb(), "estimated"),
    )


def measured_memory(running: dict, weight_bytes: int | None = None) -> MemoryReport | None:
    """What a loaded model really holds, from its ``/api/ps`` entry: ``size`` is what Ollama allocated and
    ``size_vram`` the part in GPU (on a Mac, unified) memory. ``weight_bytes`` is the file size when known
    (``/api/tags``); without it the weight is None. Peak stays an estimate: nothing measures it yet."""
    size, vram = running.get("size"), running.get("size_vram")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    if not isinstance(vram, int) or isinstance(vram, bool) or vram < 0:
        return None
    ctx = running.get("context_length")
    allocated = size / GB
    return MemoryReport(
        num_ctx=ctx if isinstance(ctx, int) and not isinstance(ctx, bool) else 0,
        weight=Figure(weight_bytes / GB, "measured") if weight_bytes else None,
        allocated=Figure(allocated, "measured"),
        resident=Figure(vram / GB, "measured"),
        peak=Figure(allocated + _runner_gb(), "estimated"),
    )
