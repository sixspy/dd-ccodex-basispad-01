"""标记价与基差。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user, require_roles
from db import audit, connect, row_to_dict
from policies import assert_writable_role, unrealized_pnl

router = APIRouter(prefix="/api/marks", tags=["marks"])


class MarkBody(BaseModel):
    instrument_id: int
    mark: float
    index_price: float
    status: str = "live"


@router.get("")
def list_marks(user=Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.*, i.symbol FROM mark_prices m JOIN instruments i ON i.id=m.instrument_id ORDER BY m.instrument_id"
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/upsert")
def upsert_mark(body: MarkBody, user=Depends(require_roles("admin", "trader"))):
    assert_writable_role(user["role"])
    with connect() as conn:
        inst = conn.execute("SELECT * FROM instruments WHERE id=?", (body.instrument_id,)).fetchone()
        if not inst:
            raise HTTPException(404, "标的不存在")
        # 结算锁定时禁止改标记价
        if conn.execute("SELECT 1 FROM settle_batches WHERE status='locked'").fetchone():
            raise HTTPException(409, "结算批次锁定中，禁止改标记价")
        basis = 0.0
        if body.index_price:
            basis = (body.mark - body.index_price) / body.index_price * 10000.0
        now = time.time()
        conn.execute(
            "INSERT INTO mark_prices(instrument_id,mark,index_price,basis_bps,status,as_of)"
            " VALUES(?,?,?,?,?,?) ON CONFLICT(instrument_id) DO UPDATE SET"
            " mark=excluded.mark, index_price=excluded.index_price, basis_bps=excluded.basis_bps,"
            " status=excluded.status, as_of=excluded.as_of",
            (body.instrument_id, body.mark, body.index_price, basis, body.status, now),
        )
        audit(conn, user["username"], "upsert", "mark", body.instrument_id, f"mark={body.mark}")
        conn.commit()
        return row_to_dict(
            conn.execute("SELECT * FROM mark_prices WHERE instrument_id=?", (body.instrument_id,)).fetchone()
        )


@router.get("/mtm")
def mtm_report(user=Depends(current_user)):
    with connect() as conn:
        out = []
        for p in conn.execute("SELECT * FROM positions WHERE qty != 0").fetchall():
            mark = conn.execute(
                "SELECT mark FROM mark_prices WHERE instrument_id=?", (p["instrument_id"],)
            ).fetchone()
            m = mark["mark"] if mark else p["avg_entry"]
            out.append(
                {
                    "sleeve_id": p["sleeve_id"],
                    "instrument_id": p["instrument_id"],
                    "qty": p["qty"],
                    "avg_entry": p["avg_entry"],
                    "mark": m,
                    "upnl": unrealized_pnl(p["qty"], p["avg_entry"], m),
                }
            )
        return out


@router.get("/official-anchor")
def official_anchor(user=Depends(current_user)):
    """盯市页的官方结算口径锚点。

    始终联查当前唯一官方单行：升正后这里必须立刻指向新批次，
    不允许盯市面板继续挂旧官方名义。附带账实核对与费率 settled 数。
    """
    with connect() as conn:
        batch = conn.execute(
            "SELECT * FROM settle_batches "
            "WHERE official=1 AND status='official' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        official_rows = conn.execute(
            "SELECT COUNT(*) c FROM settle_batches WHERE official=1"
        ).fetchone()["c"]
        status_official_rows = conn.execute(
            "SELECT COUNT(*) c FROM settle_batches WHERE status='official'"
        ).fetchone()["c"]
        if not batch:
            return {
                "anchored": False,
                "official_rows": official_rows,
                "status_official_rows": status_official_rows,
                "reconciled": official_rows == 0,
                "batch": None,
                "items": [],
                "funding_settled": conn.execute(
                    "SELECT COUNT(*) c FROM funding_ledgers WHERE status='settled'"
                ).fetchone()["c"],
            }
        items = [
            dict(r)
            for r in conn.execute(
                "SELECT si.sleeve_id, si.instrument_id, si.funding_payment, si.mtm_pnl, "
                "i.symbol FROM settle_items si LEFT JOIN instruments i ON i.id=si.instrument_id "
                "WHERE si.batch_id=? ORDER BY si.id",
                (batch["id"],),
            ).fetchall()
        ]
        funding_settled = conn.execute(
            "SELECT COUNT(*) c FROM funding_ledgers WHERE status='settled'"
        ).fetchone()["c"]
        funding_accrued = conn.execute(
            "SELECT COUNT(*) c FROM funding_ledgers WHERE status='accrued'"
        ).fetchone()["c"]
        b = dict(batch)
        return {
            "anchored": True,
            "reconciled": official_rows == 1 and status_official_rows == 1,
            "official_rows": official_rows,
            "status_official_rows": status_official_rows,
            "batch": b,
            "official_code": b["code"],
            "official_id": b["id"],
            "official_version": b["version"],
            "official_mtm_total": b["mtm_total"],
            "items": items,
            "funding_settled": funding_settled,
            "funding_accrued_pending": funding_accrued,
            "funding_flags_synced": funding_accrued == 0,
            "case_sign": "结算升正锁案",
            "lock_key": "结算升正互斥",
        }


@router.post("/{instrument_id}/freeze")
def freeze_mark(instrument_id: int, user=Depends(require_roles("admin"))):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM mark_prices WHERE instrument_id=?", (instrument_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "无标记价")
        conn.execute(
            "UPDATE mark_prices SET status='frozen' WHERE instrument_id=?", (instrument_id,)
        )
        audit(conn, user["username"], "freeze", "mark", instrument_id, "frozen")
        conn.commit()
        return {"ok": True, "status": "frozen"}
