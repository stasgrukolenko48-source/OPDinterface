"""Чистая логика проверки сейсмического файла (без GUI)."""

from __future__ import annotations

import os

from models import ValidationResult


def validate_seismic_file(path: str) -> ValidationResult:
    path = os.path.abspath(os.path.normpath(path))
    if not os.path.isfile(path):
        return ValidationResult(ok=False, error="not_file")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".sgy", ".segy"):
        return ValidationResult(ok=False, error="bad_ext")
    return ValidationResult(ok=True, name=os.path.basename(path), path=path)


def reorder_pipeline(seq: list[str], from_i: int, to_i: int) -> None:
    """Переставить элемент списка на место to_i (на месте, как insert после pop)."""
    if not (0 <= from_i < len(seq) and 0 <= to_i < len(seq)):
        return
    item = seq.pop(from_i)
    seq.insert(to_i, item)
