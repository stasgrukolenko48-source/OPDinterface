"""Фоновый поток: задачи без доступа к виджетам Tk."""

from __future__ import annotations

import logging
import queue
from typing import Any

from models import (
    LogicTaskValidateSeismic,
    UiMessageValidateResult,
    UiMessageWorkerError,
    ValidationResult,
)

from .seismic import load_segy_preview, read_segy_meta, validate_seismic_file

LOG = logging.getLogger(__name__)

LOGIC_STOP = object()


def logic_worker_main(task_queue: queue.Queue, ui_queue: queue.Queue) -> None:
    while True:
        task: Any = task_queue.get()
        if task is LOGIC_STOP:
            break
        if isinstance(task, LogicTaskValidateSeismic):
            try:
                result = validate_seismic_file(task.path)
                if result.ok and result.path:
                    preview = load_segy_preview(result.path)
                    meta = read_segy_meta(result.path)
                    tc = meta[0] if meta else None
                    sc = meta[1] if meta else None
                    result = ValidationResult(
                        ok=True,
                        name=result.name,
                        path=result.path,
                        preview=preview,
                        tracecount=tc,
                        samples_count=sc,
                    )
                ui_queue.put(UiMessageValidateResult(request_id=task.request_id, result=result))
            except Exception:
                LOG.exception("Ошибка в задаче validate_seismic")
                ui_queue.put(
                    UiMessageWorkerError(
                        request_id=task.request_id,
                        message="Внутренняя ошибка при проверке файла",
                    )
                )
