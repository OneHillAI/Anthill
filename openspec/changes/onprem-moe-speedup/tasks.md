# Tasks

- [ ] Add a new, ADDITIVE function to `anthill/hosting/sizing.py` estimating tokens/sec from a model's
  `active_b` and the detected chip's memory bandwidth (see the reference table in proposal.md - VERIFY
  against Apple's own tech specs before finalizing, the table there is a starting point, not verified
  gospel). Apply the ~0.45-0.5x calibration factor derived from the DeepSeek-V3/M3-Ultra data point (naive
  estimate ~44 tok/s vs measured ~17-21 tok/s).
- [ ] Add a hard exclusion floor (a named constant, e.g. `_MIN_USABLE_TOK_S = 10.0`) applied as a FILTER
  before the existing intelligence-based ranking runs in `recommend()`/`recommend_by_family()` - do NOT
  touch the `(m.intelligence, m.params_b) > (best.intelligence, best.params_b)` comparison itself, that
  tiebreak is about capacity, not speed, and stays exactly as it is.
- [ ] Degrade safely: unrecognized chip / non-Apple-Silicon / unreadable capacity -> do not apply the
  floor at all, current behavior is unchanged.
- [ ] Add a region-aware suggestion helper reusing `families_by_intelligence`/existing family-grouping -
  do not reimplement family grouping. Given a region, either propose a real diverse same-region triplet or
  explicitly say the region lacks enough distinct families (EU today, per the real catalog counts below).
- [ ] New tests using REAL catalog entries (below), not synthetic fixtures.

## Reference: what NOT to touch (verbatim, `anthill/hosting/sizing.py`) - the capacity tiebreak stays
exactly as-is

```python
def recommend(mem_gb, *, kind="gpu", context_k=8.0, concurrency=4, catalog=None) -> Sizing:
    if catalog is None:
        catalog = load_catalog()
    ceiling = max_params_b(mem_gb, kind=kind, context_k=context_k, concurrency=concurrency)
    best: Model | None = None
    for m in catalog:
        if m.params_b <= ceiling and (
            best is None or (m.intelligence, m.params_b) > (best.intelligence, best.params_b)
        ):
            best = m  # smartest that fits (intelligence first, larger as the tiebreak) - DO NOT CHANGE
    ...
```
The new speed floor is a PRE-FILTER on `catalog` (or on the candidates considered in the loop) - something
like: skip a candidate entirely if its estimated tok/s is known and below the floor. It must not change
what `best` means when two candidates both pass the floor.

## Reference: `Model` dataclass fields already available (verbatim - no new catalog metadata needed)

```python
@dataclass
class Model:
    name: str
    params_b: float
    hf_id: str = ""
    ollama_tag: str = ""
    gated: bool = False
    family: str = ""
    origin: str = ""
    quant_hf_id: str = ""
    intelligence: float = 0.0
    active_b: float = 0.0  # 0.0 or == params_b for dense; less than params_b for a real MoE
    license: str = ""
```

## Reference: `local_hardware()` (verbatim) - returns capacity + kind, NOT bandwidth (bandwidth is new)

```python
def local_hardware() -> tuple[float, str]:
    system = platform.system()
    if system == "Darwin" and platform.machine() == "arm64":
        return (_macos_mem_gb() or 0.0, "apple")
    vram = _nvidia_vram_gb()
    if vram:
        return (vram, "gpu")
    ram = (_macos_mem_gb() if system == "Darwin" else _posix_ram_gb()) or 0.0
    return (ram, "apple")
```
`platform.machine()` on macOS returns `"arm64"` for ALL Apple Silicon Macs regardless of which specific
chip (M1 vs M3 Ultra) - it does NOT distinguish chip models. Getting the actual chip identifier (needed to
look up bandwidth) requires a DIFFERENT probe (e.g. `sysctl -n machdep.cpu.brand_string` or
`sysctl hw.model`, not currently called anywhere in this file) - verify exactly what string format that
returns on real hardware before writing a lookup table keyed on it; do not assume a format.

## Reference: real catalog entries to test against (verbatim, from
`anthill/hosting/model_catalog.json`, 24 real entries - use these, not invented fixtures)

```
DeepSeek V4-Pro   | DeepSeek | DeepSeek, China      | params_b=1600 | active_b=49 | intel=71
MiniMax M3        | MiniMax  | MiniMax, China        | params_b=428  | active_b=23 | intel=70
GLM-5.1           | GLM      | Zhipu / Z.ai, China   | params_b=744  | active_b=40 | intel=68
GLM-5.2           | GLM      | Zhipu / Z.ai, China   | params_b=744  | active_b=40 | intel=66
Kimi K2.7 Code    | Kimi     | Moonshot AI, China    | params_b=1000 | active_b=32 | intel=64
DeepSeek V4-Flash | DeepSeek | DeepSeek, China       | params_b=284  | active_b=13 | intel=63
Qwen3.5 122B      | Qwen     | Alibaba, China        | params_b=122  | active_b=10 | intel=62
DeepSeek V3.1     | DeepSeek | DeepSeek, China       | params_b=671  | active_b=37 | intel=61
gpt-oss 120B      | gpt-oss  | OpenAI, US            | params_b=120  | active_b=5.1| intel=60
Qwen3.6 35B-A3B   | Qwen     | Alibaba, China        | params_b=35   | active_b=3  | intel=58
GLM-4.7-flash     | GLM      | Zhipu / Z.ai, China   | params_b=30   | active_b=3  | intel=55
Qwen3.5 27B       | Qwen     | Alibaba, China        | params_b=27   | active_b=27 | intel=54  (dense)
DeepSeek-R1 32B   | DeepSeek | DeepSeek, China       | params_b=32   | active_b=32 | intel=50  (dense)
gpt-oss 20B       | gpt-oss  | OpenAI, US            | params_b=20   | active_b=3.6| intel=48
Llama 3.3 70B     | Llama    | Meta, US              | params_b=70   | active_b=70 | intel=46  (dense)
Qwen3.5 9B        | Qwen     | Alibaba, China        | params_b=9    | active_b=9  | intel=45  (dense)
Gemma 3 27B       | Gemma    | Google, US            | params_b=27   | active_b=27 | intel=44  (dense)
Mistral Small 3   | Mistral  | Mistral AI, France(EU)| params_b=24   | active_b=24 | intel=42  (dense)
Phi-4 14B         | Phi      | Microsoft, US         | params_b=14   | active_b=14 | intel=40  (dense)
Qwen3.5 4B        | Qwen     | Alibaba, China        | params_b=4    | active_b=4  | intel=34  (dense)
Gemma 3 4B        | Gemma    | Google, US            | params_b=4    | active_b=4  | intel=30  (dense)
Qwen3.5 2B        | Qwen     | Alibaba, China        | params_b=2    | active_b=2  | intel=24  (dense)
Gemma 3 1B        | Gemma    | Google, US            | params_b=1    | active_b=1  | intel=18  (dense)
Qwen3.5 0.8B      | Qwen     | Alibaba, China        | params_b=0.8  | active_b=0.8| intel=14  (dense)
```
Region counts from this real data: China 5 families (DeepSeek, MiniMax, GLM, Kimi, Qwen), US 4 families
(gpt-oss, Llama, Gemma, Phi), EU 1 family (Mistral). Use these exact numbers in tests - if the real
catalog ever changes, the test should reflect reality, not a frozen assumption.

## Reference: DeepSeek-V3/M3-Ultra calibration point (for the bandwidth-based tok/s estimate)

Published (VentureBeat, MacRumors, Hardware Corner, MacStories, independently): DeepSeek-V3 (active_b=37,
4-bit) on an M3 Ultra (819 GB/s memory bandwidth) measures ~17-21 tok/s. Naive formula
`bandwidth / (active_b * 0.5)` gives `819 / 18.5 ≈ 44 tok/s` - about 2x the measured figure. Calibrate
the estimator so it lands close to the measured range for this specific case, and say in the PR what
calibration constant was chosen and why.
