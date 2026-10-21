from __future__ import annotations

import threading
import time

import pytest

from buscateses.jobs import JobConflict, JobContext, JobManager, JobNotFound


def test_successful_job_reports_progress() -> None:
    manager = JobManager()

    def work(ctx: JobContext) -> None:
        ctx.update(stage="download", message="Baixando…", progress=0.5)
        ctx.update(bytes_done=5, bytes_total=10)
        ctx.update(stage="done", message="Pronto.", progress=1.0)

    job_id = manager.submit("test", work)
    final = manager.wait(job_id, timeout=5)
    assert final["status"] == "done"
    assert final["stage"] == "done"
    assert final["message"] == "Pronto."
    assert final["progress"] == 1.0
    assert final["bytes_done"] == 10
    assert final["bytes_total"] == 10
    assert final["started_at"] is not None
    assert final["finished_at"] is not None
    assert final["error"] is None


def test_failure_becomes_error_with_message() -> None:
    manager = JobManager()

    def work(ctx: JobContext) -> None:
        ctx.update(stage="download", message="Baixando…")
        raise RuntimeError("sem conexão")

    final = manager.wait(manager.submit("failing", work), timeout=5)
    assert final["status"] == "error"
    assert "sem conexão" in final["error"]


def test_exclusive_job_kind() -> None:
    manager = JobManager()
    release = threading.Event()

    def slow(_ctx: JobContext) -> None:
        release.wait(2)

    first = manager.submit("setup", slow, exclusive=True)
    with pytest.raises(JobConflict):
        manager.submit("setup", slow, exclusive=True)
    release.set()
    assert manager.wait(first, timeout=5)["status"] == "done"
    second = manager.submit("setup", lambda _ctx: None, exclusive=True)
    assert manager.wait(second, timeout=5)["status"] == "done"


def test_cancellation_is_cooperative() -> None:
    manager = JobManager()
    started = threading.Event()

    def work(ctx: JobContext) -> None:
        started.set()
        while not ctx.is_cancelled():
            time.sleep(0.01)
        ctx.check_cancelled()

    job_id = manager.submit("cancelable", work)
    assert started.wait(2)
    assert manager.cancel(job_id) is True
    final = manager.wait(job_id, timeout=5)
    assert final["status"] == "cancelled"


def test_history_limit_keeps_most_recent_finished_jobs() -> None:
    manager = JobManager(history_limit=3)
    for index in range(6):
        job_id = manager.submit("short", lambda _ctx, value=index: value)
        manager.wait(job_id, timeout=5)
    jobs = manager.list()
    assert len(jobs) == 3
    assert [job["kind"] for job in jobs] == ["short", "short", "short"]


def test_unknown_job() -> None:
    manager = JobManager()
    with pytest.raises(JobNotFound):
        manager.get("nao-existe")
