"""总览看板。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from auth import current_user
from db import connect
from policies import unrealized_pnl

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _classify_settle(row) -> tuple[str, bool]:
    """把结算批次分成唯一官方 / 双影 mirage / 锁定 / 影子草稿 / 降版 / 作废。

    只有 official=1 且 status='official' 才是真官方；两者冲突即为会在总览
    骗人的「海市蜃楼假绿」。未升正草稿一律是 shadow，不得伪装官方。
    """
    status = row["status"]
    official = row["official"]
    is_official = bool(official == 1 and status == "official")
    if (official == 1) != (status == "official"):
        return "mirage", False
    if is_official:
        return "official", True
    if status == "locked":
        return "locked", False
    if status == "draft":
        return "shadow", False
    if status == "demoted":
        return "demoted", False
    if status == "void":
        return "void", False
    return status or "shadow", False


@router.get("/summary")
def summary(user=Depends(current_user)):
    with connect() as conn:
        instruments = conn.execute("SELECT COUNT(*) c FROM instruments WHERE status='active'").fetchone()["c"]
        sleeves = conn.execute("SELECT COUNT(*) c FROM sleeves WHERE status='active'").fetchone()["c"]
        open_tickets = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE status IN ('open','partial')"
        ).fetchone()["c"]
        fills = conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"]
        queued_liq = conn.execute(
            "SELECT COUNT(*) c FROM liquidation_queue WHERE status='queued'"
        ).fetchone()["c"]
        draft_funding = conn.execute(
            "SELECT COUNT(*) c FROM funding_snapshots WHERE status='draft'"
        ).fetchone()["c"]
        locked_settle = conn.execute(
            "SELECT COUNT(*) c FROM settle_batches WHERE status='locked'"
        ).fetchone()["c"]
        official_settle = conn.execute(
            "SELECT COUNT(*) c FROM settle_batches WHERE official=1"
        ).fetchone()["c"]

        # basispad-01：官方单行身份 + 双影识别 + 三边账实，杜绝总览假绿。
        official_row = conn.execute(
            "SELECT id, code, version FROM settle_batches "
            "WHERE official=1 AND status='official' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        mirage_count = conn.execute(
            "SELECT COUNT(*) c FROM settle_batches "
            "WHERE (official=1 AND status!='official') "
            "OR (official=0 AND status='official')"
        ).fetchone()["c"]
        accrued_pending = conn.execute(
            "SELECT COUNT(*) c FROM funding_ledgers WHERE status='accrued'"
        ).fetchone()["c"]
        has_official = official_settle >= 1
        # 有官方批次却仍挂 accrued 费率旗 => 费率台账与官方结算不同步。
        funding_flags_synced = (accrued_pending == 0) if has_official else True
        books_balanced = bool(mirage_count == 0 and official_settle == 1)
        # 三边（锁定态/官方单行/费率旗）联票核销后的总真相位，总览外框只认它。
        settle_reconciled = bool(books_balanced and funding_flags_synced)

        settle_board = []
        for r in conn.execute(
            "SELECT id, code, title, status, official, version, funding_count, "
            "mtm_total, locked_at FROM settle_batches ORDER BY id DESC"
        ).fetchall():
            classification, is_official = _classify_settle(r)
            settle_board.append(
                {
                    "id": r["id"],
                    "code": r["code"],
                    "title": r["title"],
                    "status": r["status"],
                    "official": r["official"],
                    "version": r["version"],
                    "funding_count": r["funding_count"],
                    "mtm_total": r["mtm_total"],
                    "locked_at": r["locked_at"],
                    "classification": classification,
                    "is_official": is_official,
                }
            )

        wallet_bal = conn.execute("SELECT COALESCE(SUM(balance),0) s FROM wallets").fetchone()["s"]
        upnl = 0.0
        for p in conn.execute("SELECT * FROM positions WHERE qty != 0").fetchall():
            mark = conn.execute(
                "SELECT mark FROM mark_prices WHERE instrument_id=?", (p["instrument_id"],)
            ).fetchone()
            m = mark["mark"] if mark else p["avg_entry"]
            upnl += unrealized_pnl(p["qty"], p["avg_entry"], m)
        return {
            "instruments": instruments,
            "active_sleeves": sleeves,
            "open_tickets": open_tickets,
            "fills": fills,
            "queued_liquidations": queued_liq,
            "draft_funding": draft_funding,
            "locked_settle_batches": locked_settle,
            "official_settle_batches": official_settle,
            # 唯一官方身份（双影/缺行时为 None）。
            "official_settle_id": official_row["id"] if official_row else None,
            "official_settle_code": official_row["code"] if official_row else None,
            "official_settle_version": official_row["version"] if official_row else None,
            # 双影/海市蜃楼行数与账实平衡位（总览只认真相，不挂假绿）。
            "settle_mirage_count": mirage_count,
            "settle_books_balanced": books_balanced,
            "settle_reconciled": settle_reconciled,
            "funding_accrued_pending": accrued_pending,
            "funding_flags_synced": bool(funding_flags_synced),
            "settle_board": settle_board,
            "wallet_balance_sum": wallet_bal,
            "aggregate_upnl": upnl,
        }
