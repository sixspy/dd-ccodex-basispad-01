# -*- coding: utf-8 -*-
"""
基差甲槽 · 乙红测 · 总览结算假绿/海市蜃楼（独占文件）
======================================================

测试器槽位：〈基差甲槽〉
见红凭据：〈基差甲槽专文〉——「升正锁册与官方单行须联票核销，本枪叙事独自成篇勿复用。」

失败测乙检查：总览把「未升正影子结算」当成「已官方」假绿（board mirage）。
本测同时核验后端真相口径与总览页确实绑定该口径（禁止只改页角标动画/色点交差）。
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


@pytest.fixture()
def ctx(tmp_path, request):
    old_path = dbmod.DB_PATH
    request.addfinalizer(lambda: setattr(dbmod, "DB_PATH", old_path))
    dbmod.DB_PATH = tmp_path / "jia_board_mirage.db"
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


def _new_draft(client, headers, title="乙槽影子批次"):
    r = client.post("/api/settle/batches", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


class Test基差甲槽:
    def test_overview_does_not_greenwash_unpromoted_shadow(self, ctx):
        client, headers = ctx

        before = client.get("/api/dashboard/summary", headers=headers)
        assert before.status_code == 200, before.text
        before_body = before.json()
        official_before = before_body["official_settle_batches"]

        bid = _new_draft(client, headers)

        # 未锁定直推被甲路径整笔拒写 —— 它始终只是可回看的影子草稿。
        pr = client.post(f"/api/settle/batches/{bid}/promote", headers=headers)
        assert pr.status_code == 409

        body = client.get("/api/dashboard/summary", headers=headers).json()

        # 1) 权威计数不得因影子/拒写而增加。
        assert body["official_settle_batches"] == official_before, (
            f"{RED_MARK} 总览把未升正影子计成官方：{official_before}->"
            f"{body['official_settle_batches']}"
        )

        # 2) 总览必须给出唯一官方身份，而不是一个会骗人的绿点/计数。
        assert "official_settle_id" in body and "official_settle_code" in body, (
            f"{RED_MARK} 总览缺唯一官方身份字段 official_settle_id/code，无法识别假绿"
        )
        assert body.get("official_settle_id") != bid, (
            f"{RED_MARK} 总览把未升正影子 id={bid} 指认为官方，构成海市蜃楼假绿"
        )

        # 3) 双影/假绿识别位：status 与 official 旗标冲突的行必须为 0。
        assert body.get("settle_mirage_count") == 0, (
            f"{RED_MARK} 总览仍把 status/official 冲突的双影当官方："
            f"mirage={body.get('settle_mirage_count')} body={body}"
        )
        assert body.get("settle_books_balanced") is True, (
            f"{RED_MARK} 结算册账实不平衡却被总览假绿：{body}"
        )

        # 4) 总览结算榜必须把该批次标成 shadow/draft 且 is_official=False。
        board = body.get("settle_board")
        assert isinstance(board, list) and board, (
            f"{RED_MARK} 总览缺 settle_board 真相榜"
        )
        mine = [b for b in board if b.get("id") == bid]
        assert mine, f"{RED_MARK} settle_board 未包含影子批次 {bid}"
        item = mine[0]
        assert item.get("is_official") is False, (
            f"{RED_MARK} 影子批次被 settle_board 标成官方：{item}"
        )
        assert item.get("classification") in ("shadow", "draft", "locked"), (
            f"{RED_MARK} 未升正批次分类错误：{item.get('classification')!r}"
        )

    def test_overview_flags_double_shadow_after_naive_promote_is_fixed(self, ctx):
        """锁后正推也只能有一个官方；总览不得出现第二个 status=official 假绿。"""
        client, headers = ctx
        bid = _new_draft(client, headers, title="乙槽锁后正推查双影")
        assert client.post(f"/api/settle/batches/{bid}/lock", headers=headers).status_code == 200
        assert client.post(f"/api/settle/batches/{bid}/promote", headers=headers).status_code == 200

        body = client.get("/api/dashboard/summary", headers=headers).json()
        assert body["official_settle_batches"] == 1, (
            f"{RED_MARK} 升正后 official=1 行数应为 1，实际 {body['official_settle_batches']}"
        )
        assert body.get("settle_mirage_count") == 0, (
            f"{RED_MARK} 旧官方未降版形成双影，总览假绿：mirage={body.get('settle_mirage_count')}"
        )
        assert body.get("official_settle_id") == bid, (
            f"{RED_MARK} 总览官方身份未指向新单行 id={bid}：{body.get('official_settle_id')}"
        )
        # 费率旗三边同步后，总览应报费率旗已同步。
        assert body.get("funding_flags_synced") is True, (
            f"{RED_MARK} 官方单行与费率 settled 旗不同步，总览须如实报 funding_flags_synced"
        )

    def test_dashboard_page_bindings_backed_by_truth_not_color_only(self):
        """禁止只改页角标动画/色点：总览页必须绑定后端真相字段。"""
        view = (ROOT / "frontend" / "js" / "views" / "dashboard_view.js").read_text(
            encoding="utf-8"
        )
        assert "settle_books_balanced" in view or "settle_mirage_count" in view, (
            f"{RED_MARK} 总览页未绑定账实平衡/双影识别字段，疑似只改色点交差"
        )
        assert "official_settle_code" in view, (
            f"{RED_MARK} 总览页未展示唯一官方批次编码，无法肉眼识破假绿"
        )
        settle_view = (ROOT / "frontend" / "js" / "views" / "settle_view.js").read_text(
            encoding="utf-8"
        )
        assert "影子" in settle_view and "官方" in settle_view, (
            f"{RED_MARK} 结算封批页须区分影子草稿与官方单行，不得伪装官方"
        )
