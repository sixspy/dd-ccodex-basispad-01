"""审计只读。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit(
    limit: int = 100,
    action: str | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    user=Depends(current_user),
):
    limit = max(1, min(limit, 500))
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    args: list = []
    if action:
        sql += " AND action=?"
        args.append(action)
    if entity:
        sql += " AND entity=?"
        args.append(entity)
    if entity_id:
        sql += " AND entity_id=?"
        args.append(str(entity_id))
    if actor:
        sql += " AND actor=?"
        args.append(actor)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
