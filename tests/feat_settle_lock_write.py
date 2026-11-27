# -*- coding: utf-8 -*-
"""
基差甲槽 · 甲红测 · 结算升正锁写（独占文件）
================================================

测试器槽位：〈基差甲槽〉
见红凭据：〈基差甲槽专文〉——「升正锁册与官方单行须联票核销，本枪叙事独自成篇勿复用。」

基差复现链起笔：「draft 结算批次未锁定直接升正」。

口径（拍板，唯一写路径 = 甲路径，禁止与乙路径混用）：
  * 未锁定升正：整笔拒写并回滚，官方结算册零增，费率 settled 旗零改；
  * 先锁后升正：同一事务内「降旧官方 + 写新官方单行 + 费率 settled」，
    锁定态 / 官方单行 / 费率旗三边联查，跨表账实；
  * 旧官方必须真正降版（official=0 且 status 不再是 official），不得留双影。

收口：案签「结算升正锁案」；争用占位键「结算升正互斥」。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db as dbmod  # noqa: E402

TESTER_SLOT = "基差甲槽"
RED_MARK = "〈基差甲槽专文〉"
MUTEX_KEY = "结算升正互斥"
CASE_SIGN = "结算升正锁案"


@pytest.fixture()
def ctx(tmp_path, request):
    old_path = dbmod.DB_PATH
    request.addfinalizer(lambda: setattr(dbmod, "DB_PATH", old_path))
    dbmod.DB_PATH = tmp_path / "jia_lock_write.db"
    dbmod.init_db()
    from fastapi.testclient import TestClient

    from app import app

    with TestClient(app) as client:
        r = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert r.status_code == 200, r.text
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        yield client, headers
    dbmod.DB_PATH = old_path


def _raw():
    conn = sqlite3.connect(str(dbmod.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _official_flag_count():
    with _raw() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM settle_batches WHERE official=1"
        ).fetchone()["c"]


def _official_status_count():
    with _raw() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM settle_batches WHERE status='official'"
        ).fetchone()["c"]


def _settled_count():
    with _raw() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM funding_ledgers WHERE status='settled'"
        ).fetchone()["c"]


def _new_draft(client, headers, title="甲槽未锁直推草稿"):
    r = client.post("/api/settle/batches", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


class Test基差甲槽:
    """测试器〈基差甲槽〉：本类用例首跑必须见红〈基差甲槽专文〉。"""

    def test_unlocked_promote_zero_official_insert_and_rollback(self, ctx):
        client, headers = ctx
        bid = _new_draft(client, headers)

        before_flag = _official_flag_count()
        before_status_official = _official_status_count()
        before_settled = _settled_count()

        # 复现链起笔：draft 结算批次「未锁定直接升正」。
        r = client.post(f"/api/settle/batches/{bid}/promote", headers=headers)

        # 甲路径：整笔拒写（409），不是 200 假成功。
        assert r.status_code == 409, (
            f"{RED_MARK} 未锁定升正必须整笔拒写(409)，实际 {r.status_code} {r.text}"
        )

        # 失败测甲核心检查：未锁定「仍插入新官方结算行」即红 —— 官方册必须零增。
        after_flag = _official_flag_count()
        with _raw() as conn:
            row = conn.execute(
                "SELECT status, official FROM settle_batches WHERE id=?", (bid,)
            ).fetchone()
            new_official_row = conn.execute(
                "SELECT COUNT(*) c FROM settle_batches WHERE id=? AND official=1",
                (bid,),
            ).fetchone()["c"]
        assert new_official_row == 0, (
            f"{RED_MARK} 未锁定升正竟插入/置位了新官方结算行 id={bid}"
        )
        assert after_flag == before_flag, (
            f"{RED_MARK} 缺锁升正后正式官方行数 {before_flag}->{after_flag}，必须零增并回滚"
        )
        assert row["official"] == 0 and row["status"] == "draft", (
            f"{RED_MARK} 拒写后影子草稿须保持 draft/official=0 可回看且不伪装官方，"
            f"实际 {dict(row)}"
        )
        # 费率 settled 旗不得被拒写请求顺带改动。
        assert _settled_count() == before_settled, (
            f"{RED_MARK} 未锁定升正回滚不彻底：费率 settled 行数被改动"
        )
        assert _official_status_count() == before_status_official, (
            f"{RED_MARK} 未锁定升正不得新增 status=official 双影"
        )

        # 交班单必须写清拒写原因与锁键名「结算升正互斥」。
        detail = (r.json().get("detail") or "") if r.headers.get("content-type", "").startswith("application/json") else r.text
        assert MUTEX_KEY in detail and ("锁" in detail), (
            f"{RED_MARK} 拒写响应须写明原因并点出锁键名「{MUTEX_KEY}」，实际：{detail!r}"
        )
        with _raw() as conn:
            trace = conn.execute(
                "SELECT COUNT(*) c FROM audit_logs "
                "WHERE entity='settle_batch' AND entity_id=? "
                "AND (detail LIKE ? OR action LIKE '%reject%' OR action LIKE '%block%')",
                (str(bid), f"%{MUTEX_KEY}%"),
            ).fetchone()["c"]
        assert trace >= 1, (
            f"{RED_MARK} 未锁拒写必须在说明册留交班痕（含锁键名 {MUTEX_KEY} 与拒写原因）"
        )

    def test_locked_promote_single_official_row_demotes_old_and_settles_funding(self, ctx):
        client, headers = ctx
        bid = _new_draft(client, headers, title="甲槽锁后正推")

        lr = client.post(f"/api/settle/batches/{bid}/lock", headers=headers)
        assert lr.status_code == 200, lr.text

        r = client.post(f"/api/settle/batches/{bid}/promote", headers=headers)
        assert r.status_code == 200, f"{RED_MARK} 锁后升正应成功：{r.text}"
        body = r.json()
        assert body.get("lock_key") == MUTEX_KEY, (
            f"{RED_MARK} 升正回执须核销争用占位键 {MUTEX_KEY}，实际 {body}"
        )

        with _raw() as conn:
            flags = conn.execute(
                "SELECT id,status,official,version FROM settle_batches "
                "WHERE official=1 ORDER BY id"
            ).fetchall()
            status_official = conn.execute(
                "SELECT id,status,official FROM settle_batches WHERE status='official'"
            ).fetchall()
            target = conn.execute(
                "SELECT status,official FROM settle_batches WHERE id=?", (bid,)
            ).fetchone()
            # 联票核销：官方单行（official=1 仅一行）。
            assert len(flags) == 1, (
                f"{RED_MARK} 锁后升正 official=1 必须仅一行，实际 {[dict(x) for x in flags]}"
            )
            assert flags[0]["id"] == bid, f"{RED_MARK} 唯一官方行应为新批次 {bid}"
            # 双影闸：status='official' 也只能有一行，旧官方须降版。
            assert len(status_official) == 1, (
                f"{RED_MARK} 出现官方双影：status=official 多行 "
                f"{[dict(x) for x in status_official]}"
            )
            assert status_official[0]["id"] == bid
            assert target["status"] == "official" and target["official"] == 1

        # 费率旗同事务 settled（种子有一笔 accrued；升正后不得再有 accrued 悬挂）。
        with _raw() as conn:
            hanging = conn.execute(
                "SELECT COUNT(*) c FROM funding_ledgers WHERE status='accrued'"
            ).fetchone()["c"]
            settled = _settled_count()
        assert settled >= 1, f"{RED_MARK} 升正后费率台账须有 settled 行"
        assert hanging == 0, f"{RED_MARK} 仍有 accrued 费率旗未随官方结算置 settled"

        # 同一说明册：成功 audit 必须把三边（锁定态/官方单行/费率旗）与案签写进一条。
        with _raw() as conn:
            book = conn.execute(
                "SELECT detail FROM audit_logs WHERE action='promote' "
                "AND entity='settle_batch' AND entity_id=? ORDER BY id DESC LIMIT 1",
                (str(bid),),
            ).fetchone()
        assert book is not None, f"{RED_MARK} 缺正式 promote 说明册记录"
        text = book["detail"]
        for token in (MUTEX_KEY, "official_rows=1", f"funding_settled={settled}", CASE_SIGN):
            assert token in text, (
                f"{RED_MARK} 说明册须联写三边与案签，缺少 {token!r}；detail={text!r}"
            )
