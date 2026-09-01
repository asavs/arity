"""Diagnostics and run-level data-loss tracking for Arity (Axiom 12, A12-2)."""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_data_loss_count = 0
_data_loss_reasons: list[str] = []


def record_data_loss(reason: str, exc: Optional[BaseException] = None) -> None:
    """Record that evidence, a record, or a credential could not be persisted or read."""
    global _data_loss_count
    with _lock:
        _data_loss_count += 1
        msg = f"{reason}: {exc}" if exc else reason
        _data_loss_reasons.append(msg)
    logger.warning("Data loss detected: %s", msg)


def get_data_loss_count() -> int:
    """Return total number of data loss events recorded during this run."""
    with _lock:
        return _data_loss_count


def get_data_loss_reasons() -> list[str]:
    """Return descriptions of all data loss events recorded."""
    with _lock:
        return list(_data_loss_reasons)


def reset_data_loss_count() -> None:
    """Reset data loss counter for a fresh run or test."""
    global _data_loss_count
    with _lock:
        _data_loss_count = 0
        _data_loss_reasons.clear()
