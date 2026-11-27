"""变更与业务历史聚合。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/timeline")
def timeline(user=Depends(current_user)):
    with connect() as conn:
        events = []
        for r in conn.execute(
            "SELECT id, code, status, created_at, 'ticket' AS kind FROM tickets ORDER BY id DESC LIMIT 30"
        ).fetchall():
            events.append(dict(r))
        for r in conn.execute(
            "SELECT id, code, source AS status, created_at, 'fill' AS kind FROM fills ORDER BY id DESC LIMIT 30"
        ).fetchall():
            events.append(dict(r))
        for r in conn.execute(
            "SELECT id, code, status, created_at, 'funding' AS kind FROM funding_snapshots ORDER BY id DESC LIMIT 20"
        ).fetchall():
            events.append(dict(r))
        for r in conn.execute(
            "SELECT id, code, status, created_at, 'settle' AS kind FROM settle_batches ORDER BY id DESC LIMIT 20"
        ).fetchall():
            events.append(dict(r))
        # basispad-01：升正说明册 / 拒写交班 / 争用痕进时间线，叙述可回看。
        for r in conn.execute(
            "SELECT id, action AS code, action AS status, created_at, "
            "'settle_audit' AS kind, actor, entity_id, detail "
            "FROM audit_logs WHERE entity='settle_batch' "
            "AND action IN ('lock','promote','promote_rejected','promote_contended',"
            "'promote_reconcile_failed','create') "
            "ORDER BY id DESC LIMIT 40"
        ).fetchall():
            events.append(dict(r))
        for r in conn.execute(
            "SELECT id, code, status, created_at, 'liq' AS kind FROM liquidation_queue ORDER BY id DESC LIMIT 20"
        ).fetchall():
            events.append(dict(r))
        events.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
        return events[:80]
