# basispad-01 · 结算升正锁案（当班封死说明册）

- 编号：**basispad-01**
- 所属领域：其它垂类行业 · 子方向 web3 区块链 量化交易
- 主链路：**「结算封批升正须锁后落官方」**
- 案签：**结算升正锁案**
- 测试器槽位：**〈基差甲槽〉**
- 见红凭据：**〈基差甲槽专文〉**

> **基差甲槽专文：升正锁册与官方单行须联票核销，本枪叙事独自成篇勿复用。**

---

## 1. 基差正确性口径声明（两边不得混用）

复现链起笔：**「draft 结算批次未锁定直接升正」**（现场：SET-0002 未锁定就被点升正，
官方批次成双影，费率 `settled` 旗与盯市旧官方名义对不上）。

题面给了甲、乙两条互斥写路径，**只能落一条写路径，禁止第三路折中**。当班拍板：

### ✅ 采用「甲路径」——未锁定升正整笔拒写

- 未锁定（`status != 'locked'`）就调升正：**整笔拒写（HTTP 409）并回滚事务**；
- **正式官方结算册零增**：不新增、不置位任何 `official=1` 行，版本号不自增；
- 费率 `settled` 旗零改；影子草稿保持 `draft / official=0`，**可回看且不得伪装官方**；
- 拒写在独立短事务留「交班痕」，写清**拒写原因 + 锁键名「结算升正互斥」**。

### ❌ 不采用「乙路径」——未锁定只许影子升正草案、锁后原子升正

本仓**不新增**任何「未锁即可影子升正、再二次原子落官方」的写路径/端点/状态迁移。
总览里出现的 “shadow/影子” 仅指**尚未升正的普通 draft 批次的展示分类**，
它不产生任何独立的「影子升正」写入，更不会进入正式官方区。

> 口径封死：全仓只有 `/api/settle/batches/{id}/promote` 这一条正式升正写路径，
> 且强制「先 `/lock` 后 `/promote`」。甲、乙口径不混用，不做第三路折中，
> 也不以「只改页角标动画 / 只改总览色点」冒充修复。

---

## 2. 三边同一事务联票核销（锁定态 · 官方单行 · 费率旗）

锁后升正在**同一个 SQLite 事务**内完成并 `commit`：

1. **锁定态联查/条件占位**：`UPDATE ... SET status='official', official=1, version=version+1
   WHERE id=? AND status='locked'`，`rowcount != 1` 即判争用/状态不符并整体回滚；
2. **降旧官方**：旧 `official=1` 行不仅置 `official=0`，同时 `status='demoted'`，
   消除「两行 `status='official'`」的**双影**；
3. **写新官方单行**：新批次 `status='official', official=1`；
4. **费率旗**：同事务把 `funding_ledgers.status='accrued'` 置 `'settled'`；
5. **跨表账实联查**：`official=1` 行数 = 1、`status='official'` 行数 = 1、
   且二者是同一新批次；不符则回滚（`promote_reconcile_failed`）；
6. **同一说明册**：仅写一条 `audit_logs.action='promote'`，detail 串联
   `案签=结算升正锁案; 锁键=结算升正互斥; 锁定态=locked; official_rows=1;
   official_id=<id>; status_official_rows=1; funding_settled=<n>; 联票核销`。

盯市页经 `GET /api/marks/official-anchor` 联查当前唯一官方单行，升正后立即换锚，
不再显示旧官方名义；总览 `/api/dashboard/summary` 给出唯一官方身份
`official_settle_id/code`、`settle_mirage_count`、`settle_books_balanced`、
`funding_flags_synced` 与 `settle_board` 真相榜。

---

## 3. 并发争用：占位键「结算升正互斥」

- 全局具名非重入互斥键：**`结算升正互斥`**（`locks.SETTLE_PROMOTE_MUTEX`，
  进程内 `threading.Lock` 非阻塞抢占 ＋ 数据库条件更新兜底多进程）。
- 并发双请求统一走测试/前端同名入口 **`race_settle_promote`**
  （前端 `BasisAPI.race_settle_promote(id)` 真发两路 fetch；测试在
  `tests/feat_settle_race.py::race_settle_promote` 用 barrier 齐发）。
- 两路抢键：**恰好一路 200 正式成功**；败方 **409 且零正式改**
  （官方仍单行、version 只自增一次、只有一条 `action='promote'` 成功叙述）。
- 败方**只留一条争用痕** `audit_logs.action='promote_contended'`，
  回执与痕内点出锁键名。**争用叙述（promote_contended）与取舍叙述（promote）分开。**

---

## 4. 三红测试（先见红，后改服务；无见红不合入）

| 红测 | 独占文件 | 首跑红因（基差甲槽见红〈基差甲槽专文〉） |
| --- | --- | --- |
| 甲红测 · 锁写 | `tests/feat_settle_lock_write.py` | 拒写回执无锁键名/交班痕；锁后升正旧官方仍 `status=official` 成双影；说明册未联写三边 |
| 乙红测 · 总览海市蜃楼 | `tests/feat_settle_board_mirage.py` | 总览缺唯一官方身份/双影识别字段；总览页未绑定真相（疑似只改色点） |
| 争用红测 | `tests/feat_settle_race.py` | 败方回执无「结算升正互斥」、无 `promote_contended` 争用痕 |

> 地面真相补记：仓内原顺序 `/promote` 对 draft 已有 409 守卫（故「未锁零增」表层已成立），
> 但「旧官方状态不降级 → 双 `status=official` 双影」「同 admin 锁可重入、无具名互斥与争用痕」
> 「总览无官方身份/双影口径」均为真实缺口，三文件首跑如实见红 6 条。

收口命令：

```bash
python -m pytest tests/feat_settle_lock_write.py \
                 tests/feat_settle_board_mirage.py \
                 tests/feat_settle_race.py -q
python -m pytest -q          # 含 test_paths / test_smoke，全绿方可收工
```

---

## 5. 收工五页逐页点开（禁止跳过）

结算封批页 `settle`、资金费率页 `funding`、标记价盯市页 `marks`、总览页 `dashboard`、
时间线页 `history` 均须可打开、数据口 200、徽标与说明册如实渲染。并发双请走
`race_settle_promote`。鉴权登录（`/api/auth/login`、admin/admin123 等）与
SQLite 路径（`db.DB_PATH = data/app.db`）保持原样，未改库型、未改登录。

---

## 6. basispad-01 收口判据

1. **未锁定升正，正式零增**（整笔拒写 + 回滚）；
2. **锁后升正仅一行官方**（旗标与 status 均单行，旧官方 demoted）**且费率 `settled`**；
3. **败方零正式改**（一成一拒，仅一条争用痕）。

案签：**结算升正锁案**。当班封死，不再开第二/第三条写路径。
