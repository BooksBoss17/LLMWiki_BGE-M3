"""Unicode-safe image decoding for portable model runners."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_rgb_array(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))
