# -*- coding: utf-8 -*-
"""
基差甲槽 · 争用红测 · 两路抢「结算升正互斥」（独占文件）
========================================================

测试器槽位：〈基差甲槽〉
见红凭据：〈基差甲槽专文〉——「升正锁册与官方单行须联票核销，本枪叙事独自成篇勿复用。」

争用失败测检查：两路并发抢占位键「结算升正互斥」时，旧实现无有效互斥 -> 无锁双成。
收口要求：恰好一路正式成功（降旧 + 写新官方单行 + 费率 settled，同事务），
败方零正式改，只留一条「争用痕」（区别于胜方的取舍叙述），并点出锁键名。

并发双请求统一走 race_settle_promote。
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db as dbmod  # noqa: E402

TESTER_SLOT = "基差甲槽"
RED_MARK = "〈基差甲槽专文〉"
MUTEX_KEY = "结算升正互斥"


def race_settle_promote(client, bid: int, headers: dict, workers: int = 2):
    """并发双请求驱动器：barrier 齐发 N 路 POST .../promote，返回 [(code, json/text)]。"""
    barrier = threading.Barrier(workers)
    results: list[tuple[int, object]] = []
    lock = threading.Lock()

    def _one():
        barrier.wait(timeout=10)
        r = client.post(f"/api/settle/batches/{bid}/promote", headers=headers)
        try:
            payload = r.json()
        except Exception:
            payload = r.text
        with lock:
            results.append((r.status_code, payload))
        return r.status_code, payload

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one) for _ in range(workers)]
        return [f.result(timeout=30) for f in futs]


@pytest.fixture()
def ctx(tmp_path, request):
    old_path = dbmod.DB_PATH
    request.addfinalizer(lambda: setattr(dbmod, "DB_PATH", old_path))
    dbmod.DB_PATH = tmp_path / "jia_race.db"
    dbmod.init_db()
    from fastapi.testclient import TestClient

    from app import app

    with TestClient(app) as client:
        r = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        yield client, headers
    dbmod.DB_PATH = old_path


def _raw():
    conn = sqlite3.connect(str(dbmod.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class Test基差甲槽:
    def test_two_contenders_mutex_single_formal_winner(self, ctx, monkeypatch):
        client, headers = ctx
        bid = client.post(
            "/api/settle/batches", headers=headers, json={"title": "争用批次"}
        ).json()["id"]
        assert client.post(f"/api/settle/batches/{bid}/lock", headers=headers).status_code == 200

        # 若实现提供持锁观察缝，适当放大临界区，保证两路真正在互斥键上相撞。
        try:
            import settle_svc

            monkeypatch.setattr(settle_svc, "_PROMOTE_HOLD_SECONDS", 0.25, raising=False)
        except Exception:
            pass

        outcomes = race_settle_promote(client, bid, headers, workers=2)
        codes = [code for code, _ in outcomes]

        # 恰好一路 200，另一路被拒（409）；不允许「无锁双成」(200,200)。
        assert sorted(codes) == [200, 409], (
            f"{RED_MARK} 两路抢「{MUTEX_KEY}」必须一成一拒，实际状态码 {codes}；outcomes={outcomes}"
        )

        # 败方回执必须写明争用/拒写原因与锁键名。
        loser = [p for c, p in outcomes if c == 409][0]
        loser_detail = loser.get("detail", "") if isinstance(loser, dict) else str(loser)
        assert MUTEX_KEY in loser_detail, (
            f"{RED_MARK} 败方回执须点出锁键名「{MUTEX_KEY}」，实际 {loser_detail!r}"
        )

        with _raw() as conn:
            # 官方单行：official=1 与 status=official 都只能有这一批。
            n_flag = conn.execute(
                "SELECT COUNT(*) c FROM settle_batches WHERE official=1"
            ).fetchone()["c"]
            n_status = conn.execute(
                "SELECT COUNT(*) c FROM settle_batches WHERE status='official'"
            ).fetchone()["c"]
            row = conn.execute(
                "SELECT status,official,version FROM settle_batches WHERE id=?", (bid,)
            ).fetchone()
            n_success_audit = conn.execute(
                "SELECT COUNT(*) c FROM audit_logs WHERE action='promote' "
                "AND entity='settle_batch' AND entity_id=?",
                (str(bid),),
            ).fetchone()["c"]
            # 争用痕：败方一路，单独的 action，区别于正式 promote 取舍叙述。
            n_contend = conn.execute(
                "SELECT COUNT(*) c FROM audit_logs WHERE entity='settle_batch' "
                "AND entity_id=? AND action='promote_contended'",
                (str(bid),),
            ).fetchone()["c"]

        assert n_flag == 1, f"{RED_MARK} 争用后 official=1 必须仅一行，实际 {n_flag}"
        assert n_status == 1, f"{RED_MARK} 争用后不得留官方双影，status=official 行数 {n_status}"
        assert row["status"] == "official" and row["official"] == 1
        assert row["version"] == 2, (
            f"{RED_MARK} 败方零正式改：version 应只自增一次到 2，实际 {row['version']}"
        )
        assert n_success_audit == 1, (
            f"{RED_MARK} 正式成功取舍叙述只能一条 promote，实际 {n_success_audit}"
        )
        assert n_contend == 1, (
            f"{RED_MARK} 落败会话必须且只能留一条争用痕 promote_contended，实际 {n_contend}"
        )

    def test_race_helper_symbol_present(self):
        # 固定并发入口名，供收工双请求复跑。
        assert callable(race_settle_promote), f"{RED_MARK} race_settle_promote 必须可调用"
