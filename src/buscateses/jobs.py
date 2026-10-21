"""Small thread-based job manager with progress and cancellation.

Jobs are intentionally kept in-process: the application is a single-user local
app, and long tasks only need to be observable from the local HTTP UI.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JobStatus = str
_JOB_STATUSES = {"queued", "running", "done", "error", "cancelled"}
_HISTORY_LIMIT = 50


class JobError(RuntimeError):
    """Base class for job related errors."""


class JobConflict(JobError):
    """Raised when an exclusive job kind is already active."""


class JobNotFound(JobError):
    """Raised when a job id is unknown."""


class JobCancelled(JobError):
    """Raised by task code to signal cooperative cancellation."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class _Job:
    id: str
    kind: str
    status: JobStatus = "queued"
    stage: str = ""
    message: str = ""
    progress: float | None = None
    bytes_done: int = 0
    bytes_total: int | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class JobContext:
    """Handle passed to job functions for progress and cancellation."""

    def __init__(self, manager: JobManager, job_id: str) -> None:
        self._manager = manager
        self.job_id = job_id

    def update(
        self,
        *,
        stage: str | None = None,
        message: str | None = None,
        progress: float | None = None,
        bytes_done: int | None = None,
        bytes_total: int | None = None,
    ) -> None:
        """Update observable job fields. Values are clamped and validated."""
        self._manager._update(
            self.job_id,
            stage=stage,
            message=message,
            progress=progress,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
        )

    def is_cancelled(self) -> bool:
        """Return whether cancellation was requested."""
        return self._manager._cancel_requested(self.job_id)

    def check_cancelled(self) -> None:
        """Raise :class:`JobCancelled` when cancellation was requested."""
        if self.is_cancelled():
            raise JobCancelled("Tarefa cancelada.")


JobFunction = Callable[[JobContext], Any]


class JobManager:
    """Thread-safe in-memory job manager (SPEC §8)."""

    def __init__(self, history_limit: int = _HISTORY_LIMIT) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._history_limit = max(1, int(history_limit))
        self._threads: dict[str, threading.Thread] = {}

    def submit(
        self,
        kind: str,
        fn: JobFunction,
        *,
        exclusive: bool = False,
        message: str = "",
        job_id: str | None = None,
    ) -> str:
        """Create and start a job, returning its id.

        With ``exclusive=True`` only one active job of the same ``kind`` is
        allowed; a :class:`JobConflict` is raised otherwise.
        """
        clean_kind = str(kind).strip()
        if not clean_kind:
            raise JobError("O tipo da tarefa não pode ficar vazio.")
        if not callable(fn):
            raise TypeError("fn deve ser chamável")

        with self._condition:
            if exclusive and self._kind_active_locked(clean_kind):
                raise JobConflict(
                    f"Já existe uma tarefa '{clean_kind}' em andamento. Aguarde a conclusão."
                )
            identifier = job_id or uuid.uuid4().hex
            if identifier in self._jobs:
                raise JobConflict("Já existe uma tarefa com este identificador.")
            job = _Job(id=identifier, kind=clean_kind, message=message)
            self._jobs[identifier] = job
            self._trim_locked()
            thread = threading.Thread(
                target=self._worker,
                args=(identifier, fn),
                name=f"buscateses-job-{clean_kind}-{identifier[:8]}",
                daemon=True,
            )
            self._threads[identifier] = thread
            thread.start()
            self._condition.notify_all()
        return identifier

    def get(self, job_id: str) -> dict[str, Any]:
        """Return a snapshot of ``job_id`` or raise :class:`JobNotFound`."""
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise JobNotFound("Tarefa não encontrada.")
            return job.snapshot()

    def get_optional(self, job_id: str) -> dict[str, Any] | None:
        """Return a snapshot or ``None`` when the id is unknown."""
        try:
            return self.get(job_id)
        except JobNotFound:
            return None

    def list(self, *, active: bool | None = None) -> list[dict[str, Any]]:
        """List jobs, most recent first, optionally filtered by active state."""
        with self._lock:
            snapshots = [job.snapshot() for job in self._jobs.values()]
        if active is True:
            snapshots = [
                item for item in snapshots if item["status"] in {"queued", "running"}
            ]
        elif active is False:
            snapshots = [
                item
                for item in snapshots
                if item["status"] not in {"queued", "running"}
            ]
        return list(reversed(snapshots))

    def cancel(self, job_id: str) -> bool:
        """Request cooperative cancellation. Returns False when already finished."""
        with self._condition:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise JobNotFound("Tarefa não encontrada.")
            if job.status not in {"queued", "running"}:
                return False
            job.cancel_event.set()
            job.message = "Cancelamento solicitado…"
            self._condition.notify_all()
            return True

    def wait(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Wait until a job finishes and return its final snapshot."""
        identifier = str(job_id)
        with self._condition:
            if identifier not in self._jobs:
                raise JobNotFound("Tarefa não encontrada.")
            self._condition.wait_for(
                lambda: self._jobs[identifier].status
                in {"done", "error", "cancelled"},
                timeout=timeout,
            )
            return self._jobs[identifier].snapshot()

    def is_active(self, kind: str) -> bool:
        """Return whether a job of ``kind`` is queued or running."""
        with self._lock:
            return self._kind_active_locked(str(kind).strip())

    def active(self) -> list[dict[str, Any]]:
        """Return active jobs, most recent first."""
        return self.list(active=True)

    def _kind_active_locked(self, kind: str) -> bool:
        return any(
            job.kind == kind and job.status in {"queued", "running"}
            for job in self._jobs.values()
        )

    def _worker(self, job_id: str, fn: JobFunction) -> None:
        context = JobContext(self, job_id)
        with self._condition:
            job = self._jobs[job_id]
            if job.cancel_event.is_set():
                self._finish_locked(job, "cancelled", "Tarefa cancelada.")
                return
            job.status = "running"
            job.started_at = _now()
            job.message = job.message or "Tarefa em andamento…"
            self._condition.notify_all()
        try:
            fn(context)
            with self._condition:
                job = self._jobs[job_id]
                if job.cancel_event.is_set() or job.status == "cancelled":
                    self._finish_locked(job, "cancelled", "Tarefa cancelada.")
                else:
                    if job.progress is None:
                        job.progress = 1.0
                    if job.bytes_total is not None:
                        job.bytes_done = job.bytes_total
                    self._finish_locked(job, "done", job.message or "Tarefa concluída.")
        except JobCancelled:
            with self._condition:
                self._finish_locked(self._jobs[job_id], "cancelled", "Tarefa cancelada.")
        except BaseException as exc:  # noqa: BLE001 - translated for the local UI
            with self._condition:
                self._finish_locked(
                    self._jobs[job_id], "error", _friendly_error(exc), error=_friendly_error(exc)
                )
        finally:
            with self._condition:
                self._threads.pop(job_id, None)
                self._condition.notify_all()

    def _finish_locked(
        self,
        job: _Job,
        status: JobStatus,
        message: str,
        *,
        error: str | None = None,
    ) -> None:
        if status not in _JOB_STATUSES:
            status = "error"
        job.status = status
        job.message = message
        job.error = error
        job.finished_at = _now()
        if status == "done" and job.progress is None:
            job.progress = 1.0
        self._condition.notify_all()

    def _update(
        self,
        job_id: str,
        *,
        stage: str | None,
        message: str | None,
        progress: float | None,
        bytes_done: int | None,
        bytes_total: int | None,
    ) -> None:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound("Tarefa não encontrada.")
            if job.status not in {"queued", "running"}:
                return
            if stage is not None:
                job.stage = str(stage)
            if message is not None:
                job.message = str(message)
            if progress is not None:
                job.progress = max(0.0, min(float(progress), 1.0))
            if bytes_done is not None:
                job.bytes_done = max(0, int(bytes_done))
            if bytes_total is not None:
                job.bytes_total = max(0, int(bytes_total))
            self._condition.notify_all()

    def _cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancel_event.is_set())

    def _trim_locked(self) -> None:
        finished = [
            identifier
            for identifier, job in self._jobs.items()
            if job.status not in {"queued", "running"}
        ]
        excess = len(self._jobs) - self._history_limit
        for identifier in finished:
            if excess <= 0:
                break
            del self._jobs[identifier]
            excess -= 1


def _friendly_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, JobCancelled):
        return "Tarefa cancelada."
    if not text:
        return "Ocorreu um erro inesperado. Tente novamente."
    if text.lower().startswith(("erro", "falha", "não", "nao")):
        return text
    return f"Falha na tarefa: {text}"
