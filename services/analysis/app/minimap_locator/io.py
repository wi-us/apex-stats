from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread_bgr(path: str | Path) -> np.ndarray | None:
    """Read image with Unicode paths on Windows (cv2.imread often fails there)."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = np.fromfile(str(p), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_bgr(path: str | Path, image: np.ndarray) -> bool:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        flag = ".jpg"
    elif ext == ".webp":
        flag = ".webp"
    else:
        flag = ".png"
    ok, encoded = cv2.imencode(flag, image)
    if not ok:
        return False
    encoded.tofile(str(p))
    return bool(p.is_file() and p.stat().st_size > 0)
