# Spec: the hardware label describes the machine, not the sizing bucket

Status: implemented. Lane: `pillar:model`. Issue: #153.

## Problem

`local_hardware()` returns `(memory_gb, kind)` where `kind` is a sizing bucket: `apple` (unified memory, the
conservative usable fraction), `gpu` (VRAM gates the choice). A plain CPU box has no VRAM, so it is sized as
`apple` on purpose. The "Choose your AI" step then turned `kind == "apple"` straight into the user-facing text
`16 GB unified memory (Apple Silicon)`, so a Debian machine with no GPU was told it was a Mac (#153). A first-time
user reads that as "it got my computer wrong, so is it wrong about which models fit?". The council suggestion
carried the same conflation in a rawer form (`16 GB apple`).

## Requirements

1. `sizing.hardware_label(mem_gb, kind)` builds the text from the real platform. "unified memory (Apple Silicon)"
   is used only on macOS on arm64; a discrete GPU (`kind == "gpu"`) reads "GPU memory"; any other box reads
   "RAM (CPU only)". With no memory reading it is "this machine".
2. The model picker (setup step, `/models`, Settings, `/personalize`) and the council suggestion use it.
3. `local_hardware()`, `kind` and every sizing decision are unchanged. Only the wording moves.

## Known limits

- The figure is total RAM in GiB shown as "GB", rounded to the nearest whole number, as before. A machine that
  reports 15.6 GiB shows "16 GB". That matches how the sizing treats it and how Macs are labelled, so it is left
  alone.
- An Intel Mac, and a Windows or Linux box with an integrated GPU only, read "RAM (CPU only)". Integrated GPUs
  are not probed, so "CPU only" means no NVIDIA GPU was found.
- The Organization settings dropdown (`Apple Silicon (unified memory)`) is a deliberate choice of node type for
  a cluster, not a probe of this machine, and is unchanged.

## Acceptance

- On Linux x86_64 with no NVIDIA GPU and 16 GB of RAM, the setup step shows "16 GB RAM (CPU only)" and neither
  "Apple Silicon" nor "unified memory". Sizing still uses the `apple` fraction.
- On macOS arm64 the label is "unified memory (Apple Silicon)"; with an NVIDIA GPU it is "GPU memory".
- Tests: `tests/test_sizing_local.py` and `tests/test_model_picker_onboarding.py` (model-free).
