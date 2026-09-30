from __future__ import annotations

import base64
from pathlib import Path

from ..inference.base import Message


def encode_image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


# ── chart rendering (PNG/JPG via Pillow - no extra dependency) ─────────────────


def bar_chart(labels, values, out_path: Path, *, title: str = "", size=(820, 500)) -> Path:
    """Render a simple labelled bar chart to PNG or JPG (by file extension).

    labels: list[str], values: list[number]. Amber bars on a cream background.
    """
    from PIL import Image, ImageDraw, ImageFont

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    labels = [str(l) for l in labels]
    values = [float(v) for v in values]
    if not values:
        values, labels = [0.0], [""]

    W, H = size
    pad_l, pad_r, pad_t, pad_b = 60, 30, (60 if title else 30), 70
    img = Image.new("RGB", (W, H), "#FAF5ED")  # Cream (RGB → JPG-safe)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    if title:
        d.text((pad_l, 20), title, fill="#1A110A", font=font)

    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    base_y = H - pad_b
    d.line([(pad_l, base_y), (W - pad_r, base_y)], fill="#8B5E3C", width=2)  # axis

    vmax = max(values) or 1.0
    slot = plot_w / len(values)
    bar_w = slot * 0.6
    for i, (lab, val) in enumerate(zip(labels, values, strict=False)):
        x0 = pad_l + i * slot + (slot - bar_w) / 2
        x1 = x0 + bar_w
        y0 = base_y - (val / vmax) * plot_h
        d.rectangle([x0, y0, x1, base_y], fill="#D4891A")  # Amber bars
        d.text((x0, y0 - 14), _fmt(val), fill="#1A110A", font=font)
        d.text((x0, base_y + 6), lab[:14], fill="#1A110A", font=font)

    img.save(str(out_path))
    return out_path


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def image_prompt_messages(prompt: str, images_b64: list[str]) -> list[Message]:
    """Build a message list for an Ollama vision call.

    Ollama's /api/chat accepts {"role":"user","content":"...","images":[b64,...]}
    We store the images in Message.images so the backend can forward them.
    """
    return [Message("user", prompt, images=images_b64)]
