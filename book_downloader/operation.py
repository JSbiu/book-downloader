"""Optional, thread-local controls for callers with a graphical interface."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
from typing import Callable
import sys
import time


class OperationCancelled(BaseException):
    """Cooperative cancellation; download errors must not turn it into a retry."""


@dataclass
class Operation:
    cancel: Event
    on_event: Callable[[dict], None]


_current = ContextVar("book_downloader_operation", default=None)


@contextmanager
def operation_scope(cancel: Event, on_event: Callable[[dict], None]):
    token = _current.set(Operation(cancel, on_event))
    try:
        yield
    finally:
        _current.reset(token)


def emit(kind: str, **data) -> None:
    operation = _current.get()
    if operation is not None:
        operation.on_event({"kind": kind, **data})


def check_cancelled() -> None:
    operation = _current.get()
    if operation is not None and operation.cancel.is_set():
        raise OperationCancelled()


def wait(seconds: float) -> None:
    operation = _current.get()
    if operation is None:
        time.sleep(seconds)
    elif operation.cancel.wait(seconds):
        raise OperationCancelled()


def warning(message: str) -> None:
    print(message, file=sys.stderr)
    emit("warning", message=message)
