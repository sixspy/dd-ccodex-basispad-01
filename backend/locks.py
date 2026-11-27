"""写路径资源锁。"""
from __future__ import annotations

import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/locks", tags=["locks"])

# basispad-01 结算升正：并发争用占位键（全局限定，不随批次 id 分散）。
SETTLE_PROMOTE_MUTEX = "结算升正互斥"

# 进程内具名互斥体注册表。必须是非重入 Lock：同一用户名的并发双击/双请求
# 也不允许在临界区内存活两路（旧 acquire_lock 按 holder 重入正是双成缺口）。
_NAMED_LOCKS_GUARD = threading.Lock()
_NAMED_LOCKS: dict[str, threading.Lock] = {}


class LockBody(BaseModel):
    resource: str
    reason: str = ""
    ttl_sec: int = 120


def _named_lock(resource: str) -> threading.Lock:
    with _NAMED_LOCKS_GUARD:
        lk = _NAMED_LOCKS.get(resource)
        if lk is None:
            lk = threading.Lock()
            _NAMED_LOCKS[resource] = lk
        return lk


def try_acquire_named_mutex(resource: str):
    """非阻塞抢占具名互斥键。

    成功返回持票 token（底层 Lock）；键已被另一路持有时立即返回 None，
    调用方据此只写争用痕并拒绝，不得排队后再做正式写。
    """
    lk = _named_lock(resource)
    if lk.acquire(blocking=False):
        return lk
    return None


def release_named_mutex(resource: str, token) -> None:
    """凭 try_acquire_named_mutex 返回的 token 释放，吞掉重复释放。"""
    if token is None:
        return
    try:
        token.release()
    except RuntimeError:
        pass


def acquire_lock(conn, resource: str, holder: str, reason: str = "", ttl_sec: int = 120) -> None:
    now = time.time()
    conn.execute("DELETE FROM write_locks WHERE expires_at < ?", (now,))
    row = conn.execute("SELECT * FROM write_locks WHERE resource=?", (resource,)).fetchone()
    if row and row["holder"] != holder and row["expires_at"] > now:
        raise HTTPException(409, f"资源锁定中: {resource} by {row['holder']}")
    conn.execute("DELETE FROM write_locks WHERE resource=?", (resource,))
    conn.execute(
        "INSERT INTO write_locks(resource,holder,reason,expires_at) VALUES(?,?,?,?)",
        (resource, holder, reason, now + ttl_sec),
    )


def release_lock(conn, resource: str, holder: str) -> None:
    conn.execute("DELETE FROM write_locks WHERE resource=? AND holder=?", (resource, holder))


@router.get("")
def list_locks(user=Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM write_locks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


@router.post("/acquire")
def api_acquire(body: LockBody, user=Depends(current_user)):
    with connect() as conn:
        acquire_lock(conn, body.resource, user["username"], body.reason, body.ttl_sec)
        conn.commit()
    return {"ok": True, "resource": body.resource, "holder": user["username"]}


@router.post("/release")
def api_release(body: LockBody, user=Depends(current_user)):
    with connect() as conn:
        release_lock(conn, body.resource, user["username"])
        conn.commit()
    return {"ok": True}
