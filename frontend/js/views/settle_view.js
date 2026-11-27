window.SettleView = {
  props: ["batches"],
  emits: ["refresh"],
  data() {
    return {
      title: "日终结算",
      err: "",
      raceMsg: "",
      book: [],
      mutexKey: "结算升正互斥",
      caseSign: "结算升正锁案",
    };
  },
  async mounted() { await this.loadBook(); },
  methods: {
    async loadBook() {
      try { this.book = await BasisAPI.settleBook(); } catch (e) { this.book = []; }
    },
    async afterAction() {
      this.err = "";
      await this.loadBook();
      this.$emit("refresh");
    },
    async create() {
      this.err = "";
      this.raceMsg = "";
      try {
        await BasisAPI.createBatch({ title: this.title });
        await this.afterAction();
      } catch (e) { this.err = e.message; }
    },
    async lock(id) {
      this.err = "";
      this.raceMsg = "";
      try {
        await BasisAPI.lockBatch(id);
        await this.afterAction();
      } catch (e) { this.err = e.message; }
    },
    async promote(id) {
      this.err = "";
      this.raceMsg = "";
      try {
        const d = await BasisAPI.promoteBatch(id);
        await this.afterAction();
        this.raceMsg = "升正成功：唯一官方行 #" + (d.batch_id ?? id) +
          "，费率 settled=" + (d.funding_settled ?? "-") +
          "，锁键=" + (d.lock_key || this.mutexKey);
      } catch (e) {
        // 甲路径：未锁定整笔拒写，官方零增；页面如实展示拒写原因与锁键名。
        this.err = "拒写（官方零增已回滚）：" + e.message;
        await this.loadBook();
      }
    },
    async racePromote(id) {
      this.err = "";
      this.raceMsg = "两路并发争抢「" + this.mutexKey + "」中…";
      const outcomes = await BasisAPI.race_settle_promote(id);
      const win = outcomes.filter((o) => o.ok).length;
      const lose = outcomes.length - win;
      await this.loadBook();
      this.$emit("refresh");
      this.raceMsg = "race_settle_promote 结果：成功 " + win + " 路 / 拒写 " + lose +
        " 路（只允许 1 成 1 拒；败方零正式改，仅留争用痕）";
    },
    badgeClass(r) {
      if (r.official === 1 && r.status === "official") return "badge badge-green";
      if (r.status === "official" || (r.official === 1 && r.status !== "official")) return "badge badge-red";
      if (r.status === "locked") return "badge badge-blue";
      if (r.status === "demoted") return "badge badge-gray";
      if (r.status === "void") return "badge badge-gray";
      return "badge badge-amber";
    },
    badgeText(r) {
      if (r.official === 1 && r.status === "official") return "官方单行";
      if (r.status === "official" && r.official !== 1) return "双影异常";
      if (r.official === 1 && r.status !== "official") return "旗标异常";
      if (r.status === "locked") return "已锁定待升正";
      if (r.status === "demoted") return "旧官方已降版";
      if (r.status === "void") return "已作废";
      return "影子草稿·未官方";
    },
    fmtTs(t) {
      return t ? new Date(t * 1000).toLocaleString() : "";
    },
  },
  template: `
  <div>
    <div class="panel case-banner">
      <h3>结算封批 · 案签「{{ caseSign }}」</h3>
      <p class="meta">
        唯一写路径＝甲路径：<b>先锁定再升正</b>；未锁定升正整笔拒写并回滚，正式官方零增。
        锁后升正在同一事务内「降旧官方 ＋ 写新官方单行 ＋ 费率 settled」，三边联票核销。
        并发占位键：<code>{{ mutexKey }}</code>，败方零正式改、只留争用痕。
      </p>
    </div>

    <div class="panel">
      <h3>新建结算批次</h3>
      <div class="row">
        <input v-model="title" style="max-width:280px" />
        <button class="btn" @click="create">生成草稿（影子）</button>
      </div>
      <div class="err" v-if="err" style="white-space:pre-wrap">{{ err }}</div>
      <div class="ok-msg" v-if="raceMsg">{{ raceMsg }}</div>
    </div>

    <div class="panel">
      <h3>结算批次（官方单行 / 影子不得伪装官方）</h3>
      <table>
        <thead><tr>
          <th>编码</th><th>标题</th><th>真相状态</th><th>status</th><th>official 旗</th>
          <th>版本</th><th>费率条数</th><th>MTM合计</th><th>操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in batches" :key="r.id"
              :class="{'row-mirage': r.status==='official' && r.official!==1}">
            <td>{{ r.code }}</td>
            <td>{{ r.title }}</td>
            <td><span :class="badgeClass(r)">{{ badgeText(r) }}</span></td>
            <td>{{ r.status }}</td>
            <td>{{ r.official }}</td>
            <td>{{ r.version }}</td>
            <td>{{ r.funding_count }}</td>
            <td>{{ r.mtm_total }}</td>
            <td class="row-actions">
              <button class="btn secondary" v-if="r.status==='draft'" @click="lock(r.id)">锁定封批</button>
              <button class="btn" v-if="r.status==='locked'" @click="promote(r.id)">锁后升正</button>
              <button class="btn warn" v-if="r.status==='locked'" @click="racePromote(r.id)">并发双发争用</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>升正说明册（锁定态 · 官方单行 · 费率旗 联票核销 / 拒写 / 争用痕）</h3>
      <table>
        <thead><tr><th>时间</th><th>动作</th><th>操作人</th><th>批次</th><th>说明（含拒写原因与锁键名）</th></tr></thead>
        <tbody>
          <tr v-for="e in book" :key="e.id" :class="{'row-contend': e.action==='promote_contended'}">
            <td>{{ fmtTs(e.created_at) }}</td>
            <td><span class="badge" :class="e.action==='promote' ? 'badge-green' : (e.action==='promote_contended' ? 'badge-red' : 'badge-amber')">{{ e.action }}</span></td>
            <td>{{ e.actor }}</td>
            <td>{{ e.entity_id }}</td>
            <td class="detail-cell">{{ e.detail }}</td>
          </tr>
          <tr v-if="!book.length"><td colspan="5" class="meta">暂无升正记录</td></tr>
        </tbody>
      </table>
    </div>
  </div>`
};
