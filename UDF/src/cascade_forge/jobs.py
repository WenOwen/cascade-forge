from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .storage import DatasetStore


class JobManager:
    """单写者本地任务队列，状态持久化到数据集 DuckDB。"""

    def __init__(self, store: DatasetStore):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cascade-forge")
        self._write_lock = threading.Lock()

    def submit(
        self,
        job_type: str,
        payload: dict[str, Any],
        function: Callable[[], dict[str, Any]],
    ) -> str:
        job_id = self.store.create_job(job_type, payload)

        def run() -> None:
            with self._write_lock:
                self.store.update_job(job_id, status="running")
                try:
                    result = function()
                except Exception as exc:  # 状态需要保留完整失败原因
                    self.store.update_job(job_id, status="failed", error=str(exc))
                else:
                    self.store.update_job(job_id, status="completed", result=result)

        self.executor.submit(run)
        return job_id

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

