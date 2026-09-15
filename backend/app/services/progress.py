"""抽取进度登记:纯内存,供前端轮询「已用时间 / 预计剩余时间」。

为什么不落库:抽取期间主请求持有数据库事务,进度查询必须完全不碰库,
否则轮询会和抽取抢 SQLite 写锁,把界面卡死。

耗时预估采用「先验 + 实测」两段式:
- 一块都没跑完时用 config.EXTRACT_SECONDS_PER_CHUNK 的典型值先兜底,
  让用户一开始就能看到预计耗时;
- 跑完若干块后换成实测的平均单块耗时,预估会随实际速度自动收敛。
"""

import threading
import time

from .. import config

_lock = threading.Lock()
_jobs: dict = {}


def start(document_id: int, total: int, mode: str, owner_id=None) -> None:
    """登记一次新的抽取任务。同一文档重复抽取会覆盖上一次的记录。"""
    with _lock:
        _jobs[document_id] = {
            "owner_id": owner_id,
            "total": max(total, 0),
            "done": 0,
            "mode": mode,
            "started_at": time.time(),
            "finished_at": None,
            "failed": "",
        }


def advance(document_id: int) -> None:
    """完成一块,进度 +1。每块由抽取线程在结束时调用。"""
    with _lock:
        job = _jobs.get(document_id)
        if job is not None:
            job["done"] += 1


def finish(document_id: int, failed: str = "") -> None:
    """标记任务结束(成功或失败),此后快照里 running 为 False。"""
    with _lock:
        job = _jobs.get(document_id)
        if job is None:
            return
        job["done"] = job["total"]
        job["finished_at"] = time.time()
        job["failed"] = failed


def summary(owner_id=None) -> dict:
    """正在跑的文档 id → 进度。

    一次拿全部进度,前端一个轮询请求就能刷新所有文档的进度条。
    按归属过滤在内存里完成,不查库 —— 抽取正在进行时碰数据库会抢锁。
    """
    with _lock:
        return {
            doc_id: _snapshot_locked(job)
            for doc_id, job in _jobs.items()
            if job["finished_at"] is None and job["owner_id"] == owner_id
        }


def _snapshot_locked(job: dict) -> dict:
    total = job["total"]
    done = min(job["done"], total) if total else job["done"]
    now = job["finished_at"] or time.time()
    elapsed = max(now - job["started_at"], 0.0)

    remaining = max(total - done, 0)
    if remaining <= 0:
        eta = 0.0
    elif done <= 0:
        # 一块都还没跑完:没有实测速度,先用典型值兜底,让用户一开始就有数
        workers = max(1, min(config.EXTRACT_CONCURRENCY, total or 1))
        eta = round(remaining / workers * config.EXTRACT_SECONDS_PER_CHUNK, 1)
    else:
        # 用实测的平均单块耗时推算剩余,估偏了也会随进度自我纠正
        eta = round(elapsed / done * remaining, 1)

    return {
        "running": job["finished_at"] is None,
        "mode": job["mode"],
        "total": total,
        "done": done,
        "elapsed": round(elapsed, 1),
        "eta": eta,
        "failed": job["failed"],
    }
