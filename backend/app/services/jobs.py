"""Background analysis workers: a bounded thread pool with cancellation and recovery after a restart."""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from app import config, db

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_cancelled: set[str] = set()


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=config.analysis_workers(), thread_name_prefix="analysis")
        return _executor


def _run(analysis_id: str) -> None:
    from app.services.analyzer import pipeline

    try:
        pipeline.execute(analysis_id, is_cancelled)
    except Exception:  # noqa: BLE001
        logger.exception("Analysis worker crashed for %s", analysis_id)
    finally:
        _cancelled.discard(analysis_id)


def submit(analysis_id: str) -> None:
    if os.environ.get("ANALYSIS_SYNC") == "1":
        _run(analysis_id)
        return
    _pool().submit(_run, analysis_id)


def cancel(analysis_id: str) -> None:
    _cancelled.add(analysis_id)


def is_cancelled(analysis_id: str) -> bool:
    return analysis_id in _cancelled


def resume_after_restart() -> None:
    """Queued work is picked up again; work that was running when the server stopped is marked failed."""
    for row in db.fetchall("SELECT analysis_id FROM analyses WHERE status = 'running'"):
        db.exec_sql(
            "UPDATE analyses SET status = 'failed', error = 'Interrupted by a server restart. Run it again.', message = 'Interrupted', finished_at = ? WHERE analysis_id = ?",
            [db.now(), row["analysis_id"]],
        )
    for row in db.fetchall("SELECT analysis_id FROM analyses WHERE status = 'queued' ORDER BY created_at"):
        submit(row["analysis_id"])


def shutdown() -> None:
    global _executor
    with _lock:
        if _executor is not None:
            for future_id in list(_cancelled):
                _cancelled.add(future_id)
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None
