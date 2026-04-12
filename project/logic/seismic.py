"""Чистая логика проверки сейсмического файла (без GUI)."""

from __future__ import annotations

import logging
import os
from typing import Optional

from models import SeismicPreview, ValidationResult

LOG = logging.getLogger(__name__)


def validate_seismic_file(path: str) -> ValidationResult:
    path = os.path.abspath(os.path.normpath(path))
    if not os.path.isfile(path):
        return ValidationResult(ok=False, error="not_file")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".sgy", ".segy"):
        return ValidationResult(ok=False, error="bad_ext")
    return ValidationResult(ok=True, name=os.path.basename(path), path=path)


def read_segy_meta(path: str) -> Optional[tuple[int, int]]:
    """Число трасс и отсчётов на трассу (как в testtrass: tracecount, len(samples))."""
    try:
        import segyio

        with segyio.open(path, "r", ignore_geometry=True, strict=False) as f:
            return int(f.tracecount), int(len(f.samples))
    except Exception:
        LOG.exception("read_segy_meta: %s", path)
        return None


def load_segy_preview(
    path: str,
    max_traces: int = 512,
    max_samples: int = 1024,
) -> Optional[SeismicPreview]:
    """Читает SEG-Y и строит компактную сетку амплитуд для imshow (фоновый поток)."""
    try:
        import numpy as np
        import segyio
    except ImportError:
        LOG.warning("segyio/numpy недоступны — превью сейсмики отключено")
        return None

    try:
        with segyio.open(path, "r", strict=False) as f:
            n_tr = len(f.trace)
            if n_tr <= 0:
                return None
            # Длина трасс может отличаться — берём минимум по первым трассам
            probe = min(n_tr, 32)
            ns = min(int(len(f.trace[i])) for i in range(probe))
            if ns <= 0:
                return None

            nt = min(max_traces, n_tr)
            ns_out = min(max_samples, ns)
            t_idx = np.linspace(0, n_tr - 1, num=nt, dtype=np.int64)
            s_idx = np.linspace(0, ns - 1, num=ns_out, dtype=np.int64)

            block = np.empty((nt, ns_out), dtype=np.float32)
            for ir, ti in enumerate(t_idx):
                tr = np.asarray(f.trace[int(ti)], dtype=np.float32)[:ns]
                block[ir, :] = tr[s_idx.astype(int)]

            flat = np.abs(block).ravel()
            p98 = float(np.percentile(flat, 98.0)) if flat.size else 1.0
            if p98 <= 0.0:
                p98 = 1.0
            block = np.clip(block / p98, -1.0, 1.0)

            return SeismicPreview(
                n_traces=int(block.shape[0]),
                n_samples=int(block.shape[1]),
                data=block.tobytes(),
            )
    except Exception:
        LOG.exception("Не удалось прочитать SEG-Y для превью: %s", path)
        return None


def reorder_pipeline(seq: list[str], from_i: int, to_i: int) -> None:
    """Переставить элемент списка на место to_i (на месте, как insert после pop)."""
    if not (0 <= from_i < len(seq) and 0 <= to_i < len(seq)):
        return
    item = seq.pop(from_i)
    seq.insert(to_i, item)
