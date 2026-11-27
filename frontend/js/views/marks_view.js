window.MarksView = {
  props: ["rows", "mtm", "instruments"],
  emits: ["refresh"],
  data() {
    return { form: { instrument_id: "", mark: "", index_price: "" }, err: "", anchor: null };
  },
  async mounted() { await this.loadAnchor(); },
  methods: {
    async loadAnchor() {
      try { this.anchor = await BasisAPI.officialAnchor(); } catch (e) { this.anchor = null; }
    },
    async save() {
      this.err = "";
      try {
        await BasisAPI.upsertMark({
          instrument_id: Number(this.form.instrument_id),
          mark: Number(this.form.mark),
          index_price: Number(this.form.index_price),
        });
        this.$emit("refresh");
      } catch (e) { this.err = e.message; }
    }
  },
  template: `
  <div>
    <div class="panel" :class="anchor && anchor.reconciled ? 'panel-ok' : 'panel-bad'">
      <h3>官方结算口径锚点（升正后必须换锚，不得沿用旧官方名义）</h3>
      <div v-if="anchor && anchor.anchored">
        <p class="meta">
          当前唯一官方单行：
          <b class="ok-text">{{ anchor.official_code }}</b>
          <span class="meta">#{{ anchor.official_id }} · v{{ anchor.official_version }}</span>
          ｜ 官方 MTM 合计：<b>{{ anchor.official_mtm_total }}</b>
          ｜ 费率 settled：<b>{{ anchor.funding_settled }}</b>
          ｜ accrued 挂账：<b :class="anchor.funding_accrued_pending ? 'bad-text' : 'ok-text'">{{ anchor.funding_accrued_pending }}</b>
        </p>
        <span class="badge" :class="anchor.reconciled ? 'badge-green' : 'badge-red'">
          {{ anchor.reconciled ? '账实联查一致 · 官方单行' : '双影/账实不符' }}
        </span>
        <span class="badge" :class="anchor.funding_flags_synced ? 'badge-green' : 'badge-amber'" style="margin-left:8px">
          {{ anchor.funding_flags_synced ? '费率旗已同步 settled' : '费率旗未同步' }}
        </span>
      </div>
      <p v-else class="meta">暂无唯一官方结算批次（official_rows={{ anchor ? anchor.official_rows : '-' }}）。</p>
    </div>

    <div class="panel">
      <h3>更新标记价</h3>
      <div class="form-grid">
        <div><label>标的</label>
          <select v-model="form.instrument_id"><option value="">选择</option>
            <option v-for="i in instruments" :value="i.id">{{ i.symbol }}</option></select></div>
        <div><label>标记价</label><input v-model="form.mark" /></div>
        <div><label>指数价</label><input v-model="form.index_price" /></div>
      </div>
      <div class="row"><button class="btn" @click="save">写入</button></div>
      <div class="err" v-if="err">{{ err }}</div>
    </div>
    <div class="panel">
      <h3>标记价</h3>
      <table>
        <thead><tr><th>标的</th><th>标记</th><th>指数</th><th>基差(bps)</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.instrument_id">
            <td>{{ r.symbol || r.instrument_id }}</td><td>{{ r.mark }}</td><td>{{ r.index_price }}</td>
            <td>{{ r.basis_bps }}</td><td>{{ r.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>盯市未实现</h3>
      <table>
        <thead><tr><th>簿</th><th>标的</th><th>仓位</th><th>均价</th><th>标记</th><th>UPNL</th></tr></thead>
        <tbody>
          <tr v-for="(r,i) in mtm" :key="i">
            <td>{{ r.sleeve_id }}</td><td>{{ r.instrument_id }}</td><td>{{ r.qty }}</td>
            <td>{{ r.avg_entry }}</td><td>{{ r.mark }}</td><td>{{ r.upnl }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>`
};
