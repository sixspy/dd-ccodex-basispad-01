window.DashboardView = {
  props: ["summary"],
  template: `
  <div>
    <div class="panel">
      <div class="grid">
        <div class="kpi" v-for="k in cards" :key="k.label">
          <div class="label">{{ k.label }}</div>
          <div class="value">{{ k.value }}</div>
        </div>
      </div>
    </div>

    <div class="panel" :class="balanceClass">
      <h3>结算封批真相（basispad-01 · 案签「结算升正锁案」）</h3>
      <p class="meta">
        唯一官方批次：
        <b v-if="s.official_settle_code" class="ok-text">
          {{ s.official_settle_code }} <span class="meta">#{{ s.official_settle_id }} · v{{ s.official_settle_version }}</span>
        </b>
        <b v-else class="bad-text">缺失（无唯一官方单行）</b>
      </p>
      <div class="row" style="gap:10px;flex-wrap:wrap">
        <span class="badge" :class="s.settle_reconciled ? 'badge-green' : 'badge-red'">
          三边联票核销：{{ s.settle_reconciled ? '锁定态·官方单行·费率旗 一致' : '未核销/账实不符' }}
        </span>
        <span class="badge" :class="s.settle_books_balanced ? 'badge-green' : 'badge-red'">
          官方单行：{{ s.settle_books_balanced ? '仅一行官方' : '双影/缺行' }}
        </span>
        <span class="badge" :class="(s.settle_mirage_count||0) === 0 ? 'badge-green' : 'badge-red'">
          双影/海市蜃楼：{{ s.settle_mirage_count ?? '-' }}
        </span>
        <span class="badge" :class="s.funding_flags_synced ? 'badge-green' : 'badge-amber'">
          费率 settled 旗：{{ s.funding_flags_synced ? '已与官方同步' : ('未同步 · accrued 挂账 ' + (s.funding_accrued_pending ?? 0)) }}
        </span>
      </div>
      <p class="meta" style="margin-top:8px">
        口径：未锁定升正整笔拒写、官方零增；只有 official=1 且 status=official 才亮绿，
        影子草稿/锁定批一律不亮官方绿点。
      </p>
    </div>

    <div class="panel">
      <h3>总结算榜（识别未升正影子假绿）</h3>
      <table>
        <thead><tr><th>编码</th><th>标题</th><th>分类</th><th>status</th><th>official</th><th>版本</th></tr></thead>
        <tbody>
          <tr v-for="r in (s.settle_board || [])" :key="r.id"
              :class="{'row-mirage': r.classification==='mirage'}">
            <td>{{ r.code }}</td>
            <td>{{ r.title }}</td>
            <td>
              <span class="badge" :class="boardBadge(r.classification)">
                {{ boardLabel(r.classification) }}
              </span>
            </td>
            <td>{{ r.status }}</td>
            <td>{{ r.official }}</td>
            <td>{{ r.version }}</td>
          </tr>
          <tr v-if="!(s.settle_board || []).length"><td colspan="6" class="meta">暂无结算批次</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>产品骨架</h3>
      <p class="meta">量化纸面台按永续标的与策略簿下单撮合，资金费率与标记价同源入账，结算封批与强平队列互锁。</p>
    </div>
  </div>`,
  computed: {
    s() { return this.summary || {}; },
    balanceClass() {
      const s = this.s;
      const ok = ("settle_reconciled" in s) ? s.settle_reconciled : s.settle_books_balanced;
      return ok ? "panel panel-ok" : "panel panel-bad";
    },
    cards() {
      const s = this.summary || {};
      return [
        { label: "活跃标的", value: s.instruments ?? "-" },
        { label: "活跃策略簿", value: s.active_sleeves ?? "-" },
        { label: "未完成委托", value: s.open_tickets ?? "-" },
        { label: "成交笔数", value: s.fills ?? "-" },
        { label: "强平排队", value: s.queued_liquidations ?? "-" },
        { label: "草稿费率", value: s.draft_funding ?? "-" },
        { label: "锁定结算", value: s.locked_settle_batches ?? "-" },
        { label: "官方结算(旗标行)", value: s.official_settle_batches ?? "-" },
        { label: "双影异常", value: s.settle_mirage_count ?? "-" },
        { label: "钱包余额合计", value: (s.wallet_balance_sum ?? 0).toFixed?.(2) ?? s.wallet_balance_sum },
        { label: "合计未实现盈亏", value: (s.aggregate_upnl ?? 0).toFixed?.(2) ?? s.aggregate_upnl },
      ];
    }
  },
  methods: {
    boardBadge(c) {
      return ({
        official: "badge-green",
        mirage: "badge-red",
        locked: "badge-blue",
        shadow: "badge-amber",
        draft: "badge-amber",
        demoted: "badge-gray",
        void: "badge-gray",
      })[c] || "badge-gray";
    },
    boardLabel(c) {
      return ({
        official: "官方单行",
        mirage: "双影/假绿",
        locked: "已锁定",
        shadow: "影子·未升正",
        draft: "草稿·未升正",
        demoted: "旧官方降版",
        void: "作废",
      })[c] || c;
    },
  },
};
