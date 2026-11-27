window.HistoryView = {
  props: ["events"],
  methods: {
    kindLabel(k) {
      return ({
        ticket: "委托",
        fill: "成交",
        funding: "费率快照",
        settle: "结算批次",
        settle_audit: "升正说明册",
        liq: "强平",
      })[k] || k;
    },
    statusClass(s) {
      if (s === "promote" || s === "official" || s === "settled") return "badge badge-green";
      if (s === "promote_contended" || s === "promote_reconcile_failed") return "badge badge-red";
      if (s === "promote_rejected") return "badge badge-amber";
      if (s === "locked") return "badge badge-blue";
      return "badge badge-gray";
    },
    fmtTs(t) {
      return t ? new Date(t * 1000).toLocaleString() : "";
    },
  },
  template: `
  <div class="panel">
    <h3>业务时间线（含升正说明册 · 拒写交班 · 争用痕）</h3>
    <table>
      <thead><tr><th>类型</th><th>编码/动作</th><th>状态</th><th>详情</th><th>时间</th></tr></thead>
      <tbody>
        <tr v-for="(r,i) in events" :key="i"
            :class="{'row-contend': r.status==='promote_contended'}">
          <td>{{ kindLabel(r.kind) }}</td>
          <td>{{ r.code }}</td>
          <td><span :class="statusClass(r.status)">{{ r.status }}</span></td>
          <td class="detail-cell">{{ r.detail || "" }}<span v-if="r.actor" class="meta">（{{ r.actor }} → {{ r.entity_id }}）</span></td>
          <td>{{ fmtTs(r.created_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>`
};
