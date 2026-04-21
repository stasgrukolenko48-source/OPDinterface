"""Чистая логика проверки сейсмического файла (без GUI)."""

from __future__ import annotations

import logging
import os
from typing import Optional

from models import SeismicPreview, ValidationResult

LOG = logging.getLogger(__name__)


def _segyio_path(path: str) -> str:
    """Путь для segyio на Windows: пробуем короткий 8.3, если есть кириллица."""
    if os.name != "nt":
        return path
    if all(ord(ch) < 128 for ch in path):
        return path
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        if n > 0:
            return str(buf.value)
    except Exception:
        pass
    return path


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

        with segyio.open(_segyio_path(path), "r", ignore_geometry=True, strict=False) as f:
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
        with segyio.open(_segyio_path(path), "r", strict=False, ignore_geometry=True) as f:
            n_tr = int(f.tracecount)
            if n_tr <= 0:
                return None
            ns_hdr = int(len(f.samples))
            if ns_hdr <= 0:
                return None

            block = None
            # Предпочтительно 2D-массив trace.raw (как в testtrass) — стабильнее, чем f.trace[i]
            try:
                raw = np.asarray(f.trace.raw, dtype=np.float32)
                if raw.ndim == 2 and raw.shape[0] > 0 and raw.shape[1] > 0:
                    n_rows = min(n_tr, raw.shape[0])
                    n_cols = min(ns_hdr, raw.shape[1])
                    mtx = raw[:n_rows, :n_cols]
                    nt = min(max_traces, mtx.shape[0])
                    ns_out = min(max_samples, mtx.shape[1])
                    t_idx = np.linspace(0, mtx.shape[0] - 1, num=nt, dtype=np.intp)
                    s_idx = np.linspace(0, mtx.shape[1] - 1, num=ns_out, dtype=np.intp)
                    block = mtx[t_idx][:, s_idx].astype(np.float32, copy=True)
            except Exception:
                LOG.debug("trace.raw для превью недоступен, пробуем по трассам", exc_info=True)

            if block is None:
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
