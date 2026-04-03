"""Типы данных: сообщения очередей, состояние перетаскивания, результат валидации."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

ErrorCode = Literal["not_file", "bad_ext"]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    name: Optional[str] = None
    error: Optional[ErrorCode] = None
    path: Optional[str] = None


@dataclass(frozen=True)
class LogicTaskValidateSeismic:
    path: str
    request_id: int


@dataclass(frozen=True)
class UiMessageValidateResult:
    request_id: int
    result: ValidationResult


@dataclass(frozen=True)
class UiMessageWorkerError:
    request_id: int
    message: str


UiMessage = Union[UiMessageValidateResult, UiMessageWorkerError]


@dataclass
class PipeDragState:
    idx: int
    mid: str
    x0: int
    y0: int
    moved: bool
    row: Any
    title_lbl: Any
    visual_on: bool
    hl_row: Optional[Any]
    ghost: Optional[Any]
    goffs: tuple[int, int]
    ghost_w: int
