"""Bounded, in-memory task runner for read-only discovery."""

from __future__ import annotations

import threading
import time
import uuid
from queue import Full, Queue
from datetime import datetime, timezone

from .discovery import DiscoveryCancelled


def _now():
    return datetime.now(timezone.utc).isoformat()


class TaskError(RuntimeError):
    pass


class TaskRegistry:
    def __init__(self, max_workers=4, max_tasks=128, ttl_seconds=3600):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._max_tasks = max_tasks
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._tasks = {}
        self._jobs = {}
        self._queue = Queue(maxsize=max_tasks)
        self._shutdown = False
        self._workers = []
        for index in range(max_workers):
            worker = threading.Thread(
                target=self._worker,
                name=f"storage-assistant-discovery-{index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def _prune(self):
        cutoff = time.time() - self._ttl_seconds
        stale = [
            task_id for task_id, task in self._tasks.items()
            if task.get("finished_epoch") and task["finished_epoch"] < cutoff
        ]
        for task_id in stale:
            self._tasks.pop(task_id, None)
            self._jobs.pop(task_id, None)

    def _public(self, task):
        return {
            key: value for key, value in task.items()
            if key not in {"cancel_event", "finished_epoch", "owner"}
        }

    def start(self, owner, resource, runner, notify=None, finished=None):
        with self._lock:
            if self._shutdown:
                raise TaskError("discovery.too_many_tasks")
            self._prune()
            if len(self._tasks) >= self._max_tasks:
                raise TaskError("discovery.too_many_tasks")
            active_for_owner = sum(
                task["owner"] == owner and task["state"] in {"queued", "running", "cancel_requested"}
                for task in self._tasks.values()
            )
            if active_for_owner >= 2:
                raise TaskError("discovery.too_many_user_tasks")
            task_id = str(uuid.uuid4())
            task = {
                "id": task_id,
                "owner": owner,
                "resource_id": resource["id"],
                "resource_type": resource["type"],
                "target_id": resource.get("cluster_id") or resource.get("pbs_id"),
                "state": "queued",
                "phase": "discovery.phase.queued",
                "progress": 0,
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "cancel_event": threading.Event(),
                "finished_epoch": None,
            }
            self._tasks[task_id] = task
            self._jobs[task_id] = (runner, notify, finished)
            try:
                self._queue.put_nowait(task_id)
            except Full as exc:
                self._jobs.pop(task_id, None)
                self._tasks.pop(task_id, None)
                raise TaskError("discovery.too_many_tasks") from exc
            return self._public(task)

    def _worker(self):
        while True:
            task_id = self._queue.get()
            try:
                if task_id is None:
                    return
                with self._lock:
                    callbacks = self._jobs.pop(task_id, None)
                if callbacks is not None:
                    self._run(task_id, *callbacks)
            finally:
                self._queue.task_done()

    def _run(self, task_id, runner, notify, finished):
        cancelled_before_start = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if task["cancel_event"].is_set():
                task["state"] = "cancelled"
                task["phase"] = "discovery.phase.cancelled"
                task["error"] = "discovery.cancelled"
                self._finish(task)
                cancelled_before_start = True
            else:
                task["state"] = "running"
                task["started_at"] = _now()
            snapshot = self._public(task)
        if notify:
            notify(snapshot)
        if cancelled_before_start:
            if finished:
                try:
                    finished("cancelled", "discovery.cancelled")
                except Exception:
                    pass
            return

        def progress(phase, value):
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    raise DiscoveryCancelled("discovery.cancelled")
                current["phase"] = str(phase)
                current["progress"] = max(0, min(100, int(value)))
                snap = self._public(current)
            if notify:
                notify(snap)

        def cancelled():
            with self._lock:
                current = self._tasks.get(task_id)
                return current is None or current["cancel_event"].is_set()

        final_state = "failed"
        error = None
        try:
            result = runner(progress, cancelled)
            with self._lock:
                task = self._tasks[task_id]
                if task["cancel_event"].is_set():
                    task["state"] = "cancelled"
                    task["phase"] = "discovery.phase.cancelled"
                    task["error"] = "discovery.cancelled"
                else:
                    task["state"] = "succeeded"
                    task["result"] = result
                    task["phase"] = "discovery.phase.complete"
                    task["progress"] = 100
                final_state = task["state"]
                error = task["error"]
                self._finish(task)
                snapshot = self._public(task)
        except DiscoveryCancelled:
            with self._lock:
                task = self._tasks[task_id]
                task["state"] = "cancelled"
                task["phase"] = "discovery.phase.cancelled"
                task["error"] = "discovery.cancelled"
                final_state, error = task["state"], task["error"]
                self._finish(task)
                snapshot = self._public(task)
        except Exception as exc:
            with self._lock:
                task = self._tasks[task_id]
                # Cancellation has precedence over a transport error returned
                # by an in-flight blocking call after cancellation was asked
                # for. The call cannot be interrupted safely, but its late
                # timeout must not turn an accepted cancellation into failure.
                if task["cancel_event"].is_set():
                    task["state"] = "cancelled"
                    task["phase"] = "discovery.phase.cancelled"
                    task["error"] = "discovery.cancelled"
                else:
                    task["state"] = "failed"
                    task["phase"] = "discovery.phase.failed"
                    task["error"] = str(getattr(exc, "code", "discovery.failed"))
                final_state, error = task["state"], task["error"]
                self._finish(task)
                snapshot = self._public(task)
        if notify:
            notify(snapshot)
        if finished:
            try:
                finished(final_state, error)
            except Exception:
                pass

    @staticmethod
    def _finish(task):
        task["finished_at"] = _now()
        task["finished_epoch"] = time.time()

    def get(self, task_id, owner):
        with self._lock:
            self._prune()
            task = self._tasks.get(task_id)
            if task is None or task["owner"] != owner:
                return None
            return self._public(task)

    def cancel(self, task_id, owner):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task["owner"] != owner:
                return None
            if task["state"] in {"succeeded", "failed", "cancelled"}:
                return self._public(task)
            task["cancel_event"].set()
            task["state"] = "cancel_requested"
            task["phase"] = "discovery.phase.cancelling"
            return self._public(task)

    def shutdown(self, wait=True):
        with self._lock:
            if self._shutdown:
                workers = list(self._workers)
                enqueue_stop = False
            else:
                self._shutdown = True
                for task in self._tasks.values():
                    if task["state"] in {"queued", "running", "cancel_requested"}:
                        task["cancel_event"].set()
                workers = list(self._workers)
                enqueue_stop = True
        # Never block on a full queue while holding the registry lock: workers
        # need that lock to turn queued jobs into cooperative cancellations.
        if enqueue_stop:
            for _worker in workers:
                self._queue.put(None)
        if wait:
            for worker in workers:
                worker.join()
