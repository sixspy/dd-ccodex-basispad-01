"""结算封批。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user, require_roles
from db import audit, connect, row_to_dict
from locks import (
    SETTLE_PROMOTE_MUTEX,
    acquire_lock,
    release_lock,
    release_named_mutex,
    try_acquire_named_mutex,
)
from policies import assert_writable_role, settle_batch_code, unrealized_pnl

router = APIRouter(prefix="/api/settle", tags=["settle"])

# basispad-01 案签与口径。唯一写路径 = 甲路径（未锁整笔拒写回滚），不落地乙路径影子升正。
CASE_SIGN = "结算升正锁案"
# 测试观察缝：持锁后额外占住互斥键若干秒（默认 0，生产无延迟），供并发红测确定性相撞。
_PROMOTE_HOLD_SECONDS = 0.0


class BatchBody(BaseModel):
    title: str
    note: str = ""


class _PromoteReject(Exception):
    """正式事务须回滚的拒写（缺锁定态 / 账实联查失败 / 批次不存在）。"""

    def __init__(self, status_code: int, reason: str, action: str = "promote_rejected"):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.action = action


class _PromoteContend(Exception):
    """抢到串行权但发现已被并发一路升正：败方，只留争用痕。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _write_handoff_trace(bid, code_hint, username, action, reason) -> None:
    """独立短事务写交班/争用痕。

    必须独立提交：甲路径下正式写事务整体回滚，拒写原因与锁键名仍要留在说明册，
    供交班单与时间线回看。它不是正式成功叙述（action 与 promote 分开）。
    """
    with connect() as tconn:
        audit(
            tconn,
            username,
            action,
            "settle_batch",
            bid,
            f"案签={CASE_SIGN}; 锁键={SETTLE_PROMOTE_MUTEX}; {reason}; batch={code_hint}",
        )
        tconn.commit()


@router.get("/batches")
def list_batches(status: str | None = None, user=Depends(current_user)):
    sql = "SELECT * FROM settle_batches"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


@router.get("/batches/{bid}")
def get_batch(bid: int, user=Depends(current_user)):
    with connect() as conn:
        row = conn.execute("SELECT * FROM settle_batches WHERE id=?", (bid,)).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        items = [dict(r) for r in conn.execute("SELECT * FROM settle_items WHERE batch_id=?", (bid,)).fetchall()]
        return {**dict(row), "items": items}


@router.post("/batches")
def create_batch(body: BatchBody, user=Depends(require_roles("admin", "trader"))):
    assert_writable_role(user["role"])
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM settle_batches").fetchone()["c"] + 1
        code = settle_batch_code(n)
        now = time.time()
        cur = conn.execute(
            "INSERT INTO settle_batches(code,title,status,official,version,funding_count,mtm_total,created_by,created_at,locked_at,note)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (code, body.title, "draft", 0, 1, 0, 0, user["username"], now, None, body.note),
        )
        bid = cur.lastrowid
        # 预览条目
        funding_count = 0
        mtm_total = 0.0
        for p in conn.execute("SELECT * FROM positions").fetchall():
            mark = conn.execute(
                "SELECT mark FROM mark_prices WHERE instrument_id=?", (p["instrument_id"],)
            ).fetchone()
            m = mark["mark"] if mark else p["avg_entry"]
            upnl = unrealized_pnl(p["qty"], p["avg_entry"], m)
            fund = conn.execute(
                "SELECT COALESCE(SUM(payment),0) s FROM funding_ledgers WHERE sleeve_id=? AND instrument_id=? AND status='accrued'",
                (p["sleeve_id"], p["instrument_id"]),
            ).fetchone()["s"]
            if fund:
                funding_count += 1
            mtm_total += upnl
            conn.execute(
                "INSERT INTO settle_items(batch_id,sleeve_id,instrument_id,funding_payment,mtm_pnl,note)"
                " VALUES(?,?,?,?,?,?)",
                (bid, p["sleeve_id"], p["instrument_id"], fund, upnl, ""),
            )
        conn.execute(
            "UPDATE settle_batches SET funding_count=?, mtm_total=? WHERE id=?",
            (funding_count, mtm_total, bid),
        )
        audit(conn, user["username"], "create", "settle_batch", bid, code)
        conn.commit()
        return row_to_dict(conn.execute("SELECT * FROM settle_batches WHERE id=?", (bid,)).fetchone())


@router.post("/batches/{bid}/lock")
def lock_batch(bid: int, user=Depends(require_roles("admin", "trader"))):
    assert_writable_role(user["role"])
    resource = f"settle:{bid}"
    with connect() as conn:
        acquire_lock(conn, resource, user["username"], "lock")
        try:
            row = conn.execute("SELECT * FROM settle_batches WHERE id=?", (bid,)).fetchone()
            if not row:
                raise HTTPException(404, "批次不存在")
            if row["status"] != "draft":
                raise HTTPException(409, "仅 draft 可锁定")
            other = conn.execute(
                "SELECT 1 FROM settle_batches WHERE status='locked' AND id!=?", (bid,)
            ).fetchone()
            if other:
                raise HTTPException(409, "已有其它锁定批次")
            now = time.time()
            conn.execute(
                "UPDATE settle_batches SET status='locked', locked_at=? WHERE id=?",
                (now, bid),
            )
            audit(conn, user["username"], "lock", "settle_batch", bid, row["code"])
            conn.commit()
            return {"ok": True, "status": "locked"}
        finally:
            release_lock(conn, resource, user["username"])
            conn.commit()


@router.post("/batches/{bid}/promote")
def promote_batch(bid: int, user=Depends(require_roles("admin"))):
    """正式升正（甲路径，唯一写路径）。

    铁律（basispad-01 · 案签「结算升正锁案」）：
      1. 先抢全局占位键「结算升正互斥」，并发两路只放一路进临界区；
      2. 同事务联查锁定态：必须 status='locked'，否则整笔拒写并回滚，官方零增；
      3. 同事务降旧官方（official=0 且 status='demoted'，消除双影）+ 写新官方单行；
      4. 同事务把 accrued 费率台账置 settled；
      5. 同事务跨表联查 official=1 与 status='official' 都恰好一行（账实）；
      6. 一条 promote 审计把「锁定态/官方单行/费率旗」三边与案签写进同一说明册。
    败方（互斥落败或发现已被升正）零正式改，只写 promote_contended 争用痕。
    """
    assert_writable_role(user["role"])
    key = SETTLE_PROMOTE_MUTEX
    holder = user["username"]

    token = try_acquire_named_mutex(key)
    if token is None:
        reason = (
            "抢占升正互斥键失败(race_settle_promote contention)：另一路正在升正，"
            "落败会话零正式改，仅留争用痕"
        )
        _write_handoff_trace(bid, "?", holder, "promote_contended", f"争用拒写原因={reason}")
        raise HTTPException(
            409,
            f"{reason}；锁键名={key}；案签={CASE_SIGN}",
        )

    try:
        if _PROMOTE_HOLD_SECONDS:
            time.sleep(_PROMOTE_HOLD_SECONDS)

        code_hint = str(bid)
        try:
            with connect() as conn:
                conn.execute("PRAGMA busy_timeout=5000")
                row = conn.execute(
                    "SELECT * FROM settle_batches WHERE id=?", (bid,)
                ).fetchone()
                if not row:
                    raise _PromoteReject(404, "批次不存在", action="promote_rejected")
                code_hint = row["code"]

                # 已被并发/重复请求升正：归类为争用落败，而不是普通缺锁。
                if row["status"] == "official" and row["official"] == 1:
                    raise _PromoteContend("该批次已是官方单行，重复/并发升正落败")

                # 甲路径：缺锁定态 -> 整笔拒写（with 退出即 rollback），官方册零增。
                if row["status"] != "locked":
                    raise _PromoteReject(
                        409,
                        f"未锁定禁止升正：当前 status={row['status']}，"
                        "须先 /lock 封批；甲路径整笔拒写并回滚，正式官方零增",
                    )

                # 1) 降旧官方：旗标与状态一起降，杜绝 status='official' 双影。
                conn.execute(
                    "UPDATE settle_batches SET official=0, status='demoted' "
                    "WHERE official=1 AND id<>?",
                    (bid,),
                )
                # 2) 条件占位升正：仅当仍为 locked 才命中，数据库层再兜底并发。
                cur = conn.execute(
                    "UPDATE settle_batches "
                    "SET status='official', official=1, version=version+1 "
                    "WHERE id=? AND status='locked'",
                    (bid,),
                )
                if cur.rowcount != 1:
                    raise _PromoteContend("条件升正未命中 locked 行（锁定态已被并发改动）")

                # 3) 费率旗同事务 settled。
                conn.execute(
                    "UPDATE funding_ledgers SET status='settled' WHERE status='accrued'"
                )
                funding_settled_total = conn.execute(
                    "SELECT COUNT(*) c FROM funding_ledgers WHERE status='settled'"
                ).fetchone()["c"]

                # 4) 跨表账实联查：旗标单行 + 状态单行，且都是本批。
                official_rows = conn.execute(
                    "SELECT COUNT(*) c FROM settle_batches WHERE official=1"
                ).fetchone()["c"]
                status_official_rows = conn.execute(
                    "SELECT COUNT(*) c FROM settle_batches WHERE status='official'"
                ).fetchone()["c"]
                same_row = conn.execute(
                    "SELECT COUNT(*) c FROM settle_batches "
                    "WHERE id=? AND official=1 AND status='official'",
                    (bid,),
                ).fetchone()["c"]
                if official_rows != 1 or status_official_rows != 1 or same_row != 1:
                    raise _PromoteReject(
                        500,
                        f"升正联查账实不符，回滚：official_rows={official_rows}, "
                        f"status_official_rows={status_official_rows}, same_row={same_row}",
                        action="promote_reconcile_failed",
                    )

                # 5) 三边写进同一条说明册（成功取舍叙述，与争用痕分开）。
                audit(
                    conn,
                    holder,
                    "promote",
                    "settle_batch",
                    bid,
                    f"案签={CASE_SIGN}; 锁键={key}; 锁定态=locked@{row['locked_at']}; "
                    f"official_rows=1; official_id={bid}; status_official_rows=1; "
                    f"funding_settled={funding_settled_total}; 联票核销",
                )
                conn.commit()
        except _PromoteContend as c:
            _write_handoff_trace(
                bid, code_hint, holder, "promote_contended", f"争用拒写原因={c.reason}"
            )
            raise HTTPException(409, f"{c.reason}；锁键名={key}；案签={CASE_SIGN}")
        except _PromoteReject as r:
            # 正式写已随 with 异常回滚；另立独立交班痕（含拒写原因 + 锁键名）。
            _write_handoff_trace(bid, code_hint, holder, r.action, f"拒写原因={r.reason}")
            raise HTTPException(
                r.status_code,
                f"{r.reason}；锁键名={key}；案签={CASE_SIGN}",
            )

        return {
            "ok": True,
            "status": "official",
            "official": 1,
            "batch_id": bid,
            "official_rows": 1,
            "official_count": 1,
            "single_official": True,
            "funding_settled": funding_settled_total,
            "settled": funding_settled_total,
            "lock_key": key,
            "mutex": key,
            "case_sign": CASE_SIGN,
            "case": CASE_SIGN,
        }
    finally:
        release_named_mutex(key, token)


@router.get("/book")
def promotion_book(limit: int = 100, user=Depends(current_user)):
    """升正说明册：结算封批的锁定/升正/拒写/争用痕，按时间倒序。"""
    limit = max(1, min(limit, 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, actor, action, entity, entity_id, detail, created_at "
            "FROM audit_logs WHERE entity='settle_batch' "
            "AND action IN ('lock','promote','promote_rejected','promote_contended',"
            "'promote_reconcile_failed') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/batches/{bid}/void")
def void_batch(bid: int, user=Depends(require_roles("admin"))):
    with connect() as conn:
        row = conn.execute("SELECT * FROM settle_batches WHERE id=?", (bid,)).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        if row["official"] == 1:
            raise HTTPException(409, "官方批次不可直接作废")
        conn.execute("UPDATE settle_batches SET status='void' WHERE id=?", (bid,))
        audit(conn, user["username"], "void", "settle_batch", bid, row["code"])
        conn.commit()
        return {"ok": True}
