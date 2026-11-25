// The Sector scout tab: one Damodaran industry's members ranked on Value, Quality and Moat,
// with the holdings as reference rows. Display only: nothing here reaches a holding's score.
// Data: /api/scout (job status, industries), /api/scout/view (domain/scout.py payload).
(function () {
const SCOUT_ROOT = () => document.getElementById('p8');
const SC = { list: null, cur: null, sel: null, quota: null, watch: {} };
let SRC = [];
const st = { view: {}, sort: 'value', dir: 1, all: false, open: new Set(), cols: 'score' };
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MINUS = '−';
const pct = (x, d = 1) => x == null ? '—' : (x < 0 ? MINUS : '+') + Math.abs(x * 100).toFixed(d) + '%';
const pctU = (x, d = 1) => x == null ? '—' : (x < 0 ? MINUS : '') + Math.abs(x * 100).toFixed(d) + '%';
const money = x => {
  if (x == null) return '—';
  const a = Math.abs(x), s = x < 0 ? MINUS : '';
  if (a >= 1e12) return s + '$' + (a / 1e12).toFixed(2) + 'T';
  if (a >= 1e9) return s + '$' + (a / 1e9).toFixed(1) + 'B';
  if (a >= 1e6) return s + '$' + (a / 1e6).toFixed(0) + 'M';
  return s + '$' + a.toLocaleString('en-US', { maximumFractionDigits: 2 });
};
const MOAT = { improving: ['▲', 'กว้างขึ้น', 'var(--c2)'], stable: ['▬', 'คงที่', 'var(--c6)'], eroding: ['▼', 'แคบลง', 'var(--c4)'], none: ['○', 'ไม่มีป้าย', 'var(--track)'] };
const SIGTH = { spread_slope: 'S1 ROIC−WACC รายปี', share_change: 'S2 ส่วนแบ่งรายได้', gm_gap_change: 'S3 GM เทียบ median', incremental_margin: 'S4 margin ส่วนเพิ่ม' };
const DIR = { 1: '+1 ดีขึ้น', 0: '0 ในกรอบ', '-1': '−1 แย่ลง' };
const FLAG = { F3: 'งบรายปีต่างประเทศ', F5: 'one-off', F6: 'EV ขั้นต่ำ', F7: 'การเงิน/คริปโต', F8: 'ตรวจ margin', DQ: 'ข้อมูลมีข้อบกพร่อง' };
const PEND = 'รอข้อมูล (กดรันใหม่)';
const QL = { spread: 'ROIC − WACC', cap: 'CAP', inc: 'ROIC ส่วนเพิ่ม − WACC', fcfconv: 'FCF ÷ NOPAT', sbc: 'SBC % รายได้', dil: 'Dilution %/ปี' };
const RULE = { R1: 'กฎ 1 ราคา/งวดข้อมูล', R2: 'กฎ 2 จำนวนหุ้นผิดหน่วย', R3: 'กฎ 3 บริษัท US งวดเก่า' };

function ind() { return SC.cur; }
function defaultSub(I) {
  const h = I.rows.find(r => r.hold && I.ranks['sic:' + r.sic]);
  const s = Object.entries(I.sic_groups).filter(([k]) => I.ranks['sic:' + k]).sort((a, c) => c[1] - a[1]);
  return h ? 'sic:' + h.sic : s.length ? 'sic:' + s[0][0] : 'all';
}
function viewKey(I) { return st.view[I.key] || (st.view[I.key] = defaultSub(I)); }
function ranks(I) { return I.ranks[viewKey(I)]; }
function viewRows(I) {
  const v = viewKey(I);
  return I.rows.filter(r => !r.excl && (v === 'all' || 'sic:' + r.sic === v));
}
function mlabel(r) { return r.moat.label || 'none'; }

function sigText(s) {
  const val = s.name === 'spread_slope' ? (s.value < 0 ? MINUS : '+') + Math.abs(s.value).toFixed(2) + ' pp/ปี'
    : s.name === 'share_change' ? pct(s.value) + ' (เทียบส่วนแบ่งเดิม)' : pct(s.value) + ' จุด';
  const band = { spread_slope: '±1.0 pp/ปี', share_change: '±10%', gm_gap_change: '±2 จุด', incremental_margin: '±5 จุด' }[s.name];
  let basis = '';
  if (s.name === 'share_change') basis = s.basis === 'sub_group' ? ` · ฐาน SIC ${esc(s.sic)} ${s.matched_n} ราย (${pctU(s.share_then, 2)} → ${pctU(s.share_now, 2)})` : ` · ฐานทั้งอุตสาหกรรม ${s.matched_n} ราย (${pctU(s.share_then, 2)} → ${pctU(s.share_now, 2)})${s.basis_reason ? ' · ' + esc(s.basis_reason) : ''}`;
  if (s.name === 'gm_gap_change' && s.comparators) basis = ` · เทียบ SIC ${esc(s.comparators.level === 4 ? s.comparators.sic : String(s.comparators.sic).slice(0, 3) + 'x')} ${s.comparators.n} ราย`;
  if (s.name === 'spread_slope') basis = ' · ' + esc(s.basis);
  return `<b>${SIGTH[s.name]}</b>: <span class="num">${val}</span> → ${DIR[s.direction]} (กรอบ ${band}) · ${esc(s.window)}${basis}`;
}

function kv(label, m, f) {
  const src = m.s != null ? SRC[m.s] : '';
  const body = m.v == null ? `— <span class="muted">${esc(m.why)}</span>` : `<span class="num">${f(m.v)}</span>`;
  return `<dt>${label}</dt><dd>${body}<br><span class="muted">${esc(src)}${m.p ? ' · ' + esc(m.p) : ''}</span></dd>`;
}

function detail(I, r, cols) {
  const m = r.m, W = I.wacc;
  const val = r.v_ok ? `<dl class="kv">
    <dt>EBIT/EV − WACC</dt><dd><span class="num">${pct(r.ey - W)}</span> <span class="muted">(${pctU(r.ey)} − ${pctU(W, 2)})</span></dd>
    <dt>EV/EBIT</dt><dd><span class="num">${r.evebit.toFixed(1)}x</span> <span class="muted">median ${esc(r.rel_basis.group)} ${r.rel_basis.n} ราย ${r.rel_basis.med_m.toFixed(1)}x</span></dd>
    <dt>เทียบ median ปรับโต</dt><dd><span class="num">${r.rel.toFixed(2)}</span> <span class="muted">${r.rel_basis.growth_adj ? `= (EV/EBIT ÷ median) × (1 + g median ${pct(r.rel_basis.med_g)}) ÷ (1 + g ${pct(r.g3)}); ต่ำกว่า 1 = ถูกกว่ากลุ่ม` : 'ไม่ได้ปรับโต: ไม่มี g 3 ปี'}</span></dd>
    <dt>g ที่ราคาสะท้อน</dt><dd><span class="num">${pct(r.gimp)}</span>${r.gimp_clip ? ` <span class="muted">(ชนขอบ ${r.gimp_clip === 'below' ? '−50%' : '+100%'})</span>` : ''} <span class="muted">reverse DCF แบบง่าย: EV = NOPAT (EBIT×0.75) โต g 10 ปี + terminal g 3% คิดลดด้วย WACC ไม่หักเงินลงทุนซ้ำ จึงเป็นค่าขั้นต่ำ</span></dd>
    <dt>g จริง 3 ปี</dt><dd>${r.g3 == null ? `— <span class="muted">${esc(m.g3.why)}</span>` : `<span class="num">${pct(r.g3)}</span> <span class="muted">CAGR รายได้ ${esc(m.g3.p)}</span>`}</dd>
    <dt>FCF yield</dt><dd>${r.fcfy == null ? `— <span class="muted">${r.qp.fcf.pending ? PEND : esc(r.qp.fcf.why)}</span>` : `<span class="num">${pct(r.fcfy)}</span> <span class="muted">FCF FY${esc(String(r.qp.fcf.end).slice(0, 4))} (CFO − capex − SBC) ${money(r.qp.fcf.v)} ÷ EV${I.scored.fcfy ? '' : ' · ยังไม่นับคะแนน: มีข้อมูล ' + I.cover.fcfy + '% ของชั้น (ต้อง ≥80%)'}</span>`}</dd>
    <dt>คะแนน Value</dt><dd><span class="num">${r.s_value.toFixed(0)}</span> <span class="muted">เฉลี่ย percentile ${r.s_value_n} ตัวชี้วัด</span></dd>
  </dl>` : `<p class="note">ไม่เข้าชั้น Value: ${r.v_why.map(esc).join(' · ')}</p>`;
  const qv = (k, f) => { const x = r.qp[k] || {}; return x.pending ? `— <span class="muted">${PEND}</span>` : x.v == null ? `— <span class="muted">${esc(x.why)}</span>` : `<span class="num">${f(x)}</span> <span class="muted">${esc(x.src || '')}</span>`; };
  const parts = r.q_parts ? Object.entries(QL).map(([k, l]) => `${l} ${r.q_parts[k] != null ? r.q_parts[k].toFixed(0) : (I.scored[k] === false ? 'ไม่นับ' : '—')}`).join(' · ') : '';
  const q = `<dl class="kv">${r.roic != null ? `<dt>ROIC ≈</dt><dd><span class="num">${pctU(r.roic)}</span> <span class="muted">EBIT×(1−25%) ÷ IC ${money(r.ic)} · − WACC = ${pct(r.roic - W)}</span></dd>` : `<dt>ROIC</dt><dd class="muted">${esc(r.q_why)}</dd>`}
    <dt>CAP</dt><dd>${qv('cap', x => `${x.v}/${x.of} ปี`)}</dd>
    <dt>ROIC ส่วนเพิ่ม</dt><dd>${qv('inc', x => `${pctU(x.raw / 100)} (− WACC ${pct(x.v)})`)}</dd>
    <dt>FCF ÷ NOPAT</dt><dd>${qv('fcfconv', x => (x.v * 100).toFixed(0) + '%')}</dd>
    <dt>SBC</dt><dd>${qv('sbc', x => pctU(x.v) + ' ของรายได้')}</dd>
    <dt>Dilution</dt><dd>${qv('dil', x => pct(x.v) + '/ปี')}</dd>
    <dt>ลูกหนี้ (EQ)</dt><dd>${qv('ar', x => x.v ? 'โตเร็วกว่ารายได้' : 'ปกติ')}${r.qp.ar.flags && r.qp.ar.flags.length ? '<br><span class="muted">' + r.qp.ar.flags.map(esc).join(' · ') + '</span>' : ''}</dd>
    <dt>หนี้สุทธิ/EBITDA</dt><dd>${qv('nde', x => x.v.toFixed(2) + 'x')}</dd>
    <dt>Interest coverage</dt><dd>${qv('cov', x => x.v.toFixed(1) + 'x')}</dd>
    ${r.s_quality != null ? `<dt>คะแนน Quality</dt><dd><span class="num">${r.s_quality.toFixed(0)}</span> <span class="muted">= ${r.q_base.toFixed(1)} (เฉลี่ยถ่วง percentile: ${esc(parts)})${r.pen ? ' − ' + r.pen + ' (' + r.pen_why.map(esc).join(', ') + ')' : ''}${r.pen_shown ? ' · หักได้ ' + r.pen_shown + ' แต่ยังไม่นับ (ข้อมูลหนี้ครบ ' + I.cover.pen + '% ของชั้น)' : ''}</span></dd>` : ''}</dl>`;
  const mo = r.moat;
  const ml = MOAT[mlabel(r)];
  const moat = `<p class="note" style="margin:0 0 4px">ป้าย: <b>${ml[0]} ${ml[1]}</b> · สัญญาณที่ลงคะแนน ${mo.cast} จาก ${mo.possible}${mo.net != null ? ` · รวม ${mo.net > 0 ? '+' : mo.net < 0 ? MINUS : ''}${Math.abs(mo.net)} (ต้อง ±2 จึงมีทิศ)` : ''}</p>
    <ul class="sig">${mo.signals.map(s => `<li>${sigText(s)}</li>`).join('')}${mo.absent.map(a => `<li class="muted">ไม่นับ: ${esc(a)}</li>`).join('')}</ul>`;
  const extra = [];
  if (r.oo.v1.length && !r.oo.v2.length) extra.push(`<li><b>one-off v1 → ไม่ติดธงใน v2</b>: v1 จับเพราะ ${esc(r.oo.v1.join(', '))} แต่ไม่มีหลักฐานรายการครั้งเดียว${r.oo.recovered ? ' และ margin ดิบ (ก่อน add-back) ฟื้นจริง' : ''}</li>`);
  if (r.challenger) extra.push(`<li><b>ผู้ท้าชิง</b>: ส่วนแบ่ง ${pctU(r.challenger.me[0], 2)} → ${pctU(r.challenger.me[1], 2)} ขณะที่ ${esc(r.challenger.vs)} ที่ใหญ่กว่าลดจาก ${pctU(r.challenger.them[0], 2)} → ${pctU(r.challenger.them[1], 2)} (ฐาน ${esc(r.challenger.group)}${r.challenger.others.length ? ', รายใหญ่อื่นที่ลดด้วย: ' + esc(r.challenger.others.join(', ')) : ''})</li>`);
  if (r.sim && r.sim.length) extra.push(`<li><b>ทดแทน/เทียบกับ ${esc(r.sim.join(', '))}</b>: SIC 4 หลักเดียวกับหุ้นในพอร์ต (${esc(r.sec_sic[0] || r.sic)})</li>`);
  const flags = (r.flags.length || extra.length) ? `<ul class="sig">${r.flags.map(f => `<li><b>${FLAG[f[0]]}</b>: ${esc(f[1])}</li>`).join('')}${extra.join('')}</ul>` : '<p class="note">ไม่มีธง</p>';
  const x = r.info;
  const info = `<dl class="kv"><dt>ราคาย้อนหลัง</dt><dd>${x.have ? esc(x.hist) : `<span class="muted">${esc(x.pending)}</span>`}</dd>
    ${x.have ? `<dt>Volatility</dt><dd><span class="num">${x.vol == null ? '—' : pctU(x.vol, 1)}</span></dd><dt>Beta SPY</dt><dd><span class="num">${x.beta == null ? '—' : x.beta.toFixed(2)}</span></dd><dt>Corr สูงสุด</dt><dd>${x.top ? `<span class="num">${x.top.value.toFixed(2)}</span> กับ ${esc(x.top.ticker)}` : '—'}</dd><dt>จังหวะ</dt><dd>${esc(x.mom || '—')} · RSI ${x.ind ? x.ind.rsi : '—'} ${esc(x.ind ? x.ind.rsi_signal : '')} · MACD ${esc(x.ind ? x.ind.macd_signal : '')} · DBBMV ${esc(x.ind ? x.ind.dbbmv_signal : '')} <span class="muted">(จังหวะ ไม่ใช่เหตุผลซื้อ)</span></dd>` : ''}
    <dt>งบล่าสุด</dt><dd>งวด ${esc(x.period_end || '—')} · ยื่น ${x.filed ? esc(x.filed) : '<span class="muted">' + PEND + '</span>'}<br><span class="muted">งบถัดไปเร็วสุด ${esc(x.next_by || '—')} (${esc(x.rule || '')})${x.new_since ? ' · ถึงกำหนดแล้ว อาจมีงบใหม่หลังวันที่ดึง' : ''}</span></dd></dl>`;
  const inputs = `<dl class="kv">${kv('ราคา', m.price, x => '$' + x.toFixed(2))}${kv('Market cap', m.mcap, money)}${kv('EV', m.ev, money)}${kv('รายได้', m.rev, money)}${kv('EBIT ใช้', m.ebit, money)}${kv('EBIT ดิบ', m.ebit_raw, money)}${kv('Add-back', m.addback, money)}${kv('ส่วนผู้ถือหุ้น', m.equity, money)}${kv('หนี้', m.debt, money)}${kv('เงินสด', m.cash, money)}${kv('สัญญาเช่า', m.leases, money)}</dl>`;
  return `<tr class="det" id="d-${I.key}-${esc(r.t)}"><td colspan="${cols}"><div class="dgrid">
    <div><h4>ชั้น 3 Value</h4>${val}<h4 style="margin-top:10px">ชั้น 2 Quality</h4>${q}</div>
    <div><h4>ชั้น 1 Moat</h4>${r.moat_why ? `<p class="note">${esc(r.moat_why)}</p>` : ''}${moat}<h4 style="margin-top:10px">ธงและข้อควรระวัง</h4>${flags}<h4 style="margin-top:10px">ข้อมูลประกอบ (ไม่นับคะแนน)</h4>${info}</div>
    <div><h4>ข้อมูลตั้งต้น (แหล่งและงวด)</h4>${inputs}<p class="note">SIC Damodaran ${esc(r.sic)} · SIC ที่ยื่น SEC ${esc(r.sec_sic[0])} ${esc(r.sec_sic[1] || '')} · ${r.dom === 'us' ? 'บริษัท US' : 'บริษัทต่างประเทศ'}</p>
    ${wlBox(I, r)}</div>
  </div></td></tr>`;
}

const num = (v, f, why) => v == null ? `—<span class="why">${esc(why || '')}</span>` : f(v);
const pend = (r, k) => r.qp[k] && r.qp[k].pending;
function qcell(r, k, f) {
  const q = r.qp[k] || {};
  if (q.pending) return `<span class="muted">—</span><span class="why">${PEND}</span>`;
  const tag = q.basis === 'fy' ? '<span class="why">ประมาณจาก fy rows</span>' : '';
  return q.v == null ? `—<span class="why">${esc(short(q.why))}</span>` : f(q) + tag;
}
function short(w) { w = String(w || ''); return w.length > 34 ? w.slice(0, 32) + '…' : w; }
function riskCell(r) {
  const n = r.qp.nde, c = r.qp.cov;
  if (n.pending) return `<span class="muted">—</span><span class="why">${PEND}</span>`;
  const a = n.v == null ? '—' : n.v.toFixed(1) + 'x', b = c.v == null ? 'ไม่มีดอกเบี้ย' : c.v.toFixed(1) + 'x';
  const p = r.pen || r.pen_shown || 0;
  return `${a}<span class="why">coverage ${b}${p ? ` · หัก ${p}${r.pen_shown != null ? ' (ยังไม่นับ)' : ''}` : ''}</span>`;
}
function infoCell(r, f) { return r.info.have ? f(r.info) : (ind().has_g2 ? `<span class="muted">—</span><span class="why">${esc(r.info.pending)}</span>` : '<span class="muted">—</span>'); }
function filingCell(r) {
  const x = r.info;
  const base = x.filed ? `<span class="num">${esc(x.filed)}</span>` : `<span class="muted">—</span>`;
  const nw = x.new_since ? `<span class="pill warn">มีงบใหม่หลังวันที่ดึง?</span>` : '';
  return `${base}${nw}<span class="why">งวด ${esc(x.period_end || '—')}${x.filed ? '' : ' · วันยื่น: ' + PEND}</span>`;
}
const COLSETS = {
  score: { label: 'คะแนน', cols: [
    ['t', 'หุ้น', 'tk'], ['s_comb', 'คะแนนรวม (อันดับ)', 'c-sc'], ['value', 'Value อันดับ', ''],
    ['ey', 'EBIT/EV − WACC', 'c-ey'], ['rel', 'EV/EBIT เทียบ SIC (ปรับโต)', 'c-rel'], ['ggap', 'g ที่ราคาสะท้อน / g จริง 3 ปี', 'c-g'],
    ['fcfy', 'FCF yield', 'c-fy'], ['quality', 'Quality อันดับ', ''], ['q', 'ROIC − WACC', 'c-q'], ['moat', 'Moat', '']] },
  quality: { label: 'Quality รายละเอียด', cols: [
    ['t', 'หุ้น', 'tk'], ['quality', 'Quality อันดับ (คะแนน)', ''], ['q', 'ROIC − WACC', ''], ['cap', 'CAP (ปี ROIC > WACC)', ''],
    ['inc', 'ROIC ส่วนเพิ่ม − WACC', ''], ['fcfconv', 'FCF ÷ NOPAT', ''], ['sbc', 'SBC % รายได้', 'c-x'], ['dil', 'Dilution %/ปี', 'c-x'],
    ['ar', 'ลูกหนี้ (EQ)', 'c-x'], ['risk', 'หนี้สุทธิ/EBITDA', '']] },
  info: { label: 'ข้อมูลประกอบ (ไม่นับคะแนน)', cols: [
    ['t', 'หุ้น', 'tk'], ['s_comb', 'คะแนนรวม (อันดับ)', 'c-sc'], ['filed', 'งบล่าสุดที่ยื่น', ''], ['vol', 'Volatility ต่อปี', ''],
    ['beta', 'Beta เทียบ SPY', ''], ['corr', 'Correlation สูงสุดกับหุ้นในพอร์ต', ''], ['mom', 'จังหวะ ไม่ใช่เหตุผลซื้อ', '']] },
};
function cols() { return COLSETS[st.cols].cols; }
function sortLabel() { for (const c of Object.values(COLSETS)) { const x = c.cols.find(k => k[0] === st.sort); if (x) return x[1]; } return st.sort; }
const SORTV = {
  comb: (r, R) => R.comb[r.t], s_comb: r => r.s_comb == null ? null : -r.s_comb, value: (r, R) => R.value[r.t], quality: (r, R) => R.quality[r.t],
  moat: (r, R) => R.moat[r.t], ey: r => r.v_ok ? -r.ey : null, rel: r => r.v_ok ? r.rel : null, ggap: r => r.v_ok && r.ggap != null ? -r.ggap : null,
  q: r => r.roic == null ? null : -r.roic, t: r => r.t, fcfy: r => r.fcfy == null ? null : -r.fcfy,
  cap: r => r.qp.cap.v == null ? null : -(r.qp.cap.v / r.qp.cap.of), inc: r => r.qp.inc.v == null ? null : -r.qp.inc.v,
  fcfconv: r => r.qp.fcfconv.v == null ? null : -r.qp.fcfconv.v, sbc: r => r.qp.sbc.v, dil: r => r.qp.dil.v,
  ar: r => r.qp.ar.v == null ? null : (r.qp.ar.v ? 0 : 1), risk: r => r.qp.nde.v,
  vol: r => r.info.vol, beta: r => r.info.beta, corr: r => r.info.top ? -r.info.top.value : null, mom: r => r.info.mom, filed: r => r.info.filed ? '~' + r.info.filed : null
};
function cell(k, I, r, R) {
  const W = I.wacc, rk = (t, why) => R[t][r.t] != null ? `<span class="num rk">${R[t][r.t]}</span>` : `—<span class="why">${esc(why)}</span>`;
  const tip = m => esc(`${SRC[m.s] || ''}${m.p ? ' · ' + m.p : ''}`);
  const ml = MOAT[mlabel(r)], mo = r.moat;
  switch (k) {
    case 's_comb': return `<td class="num c-sc">${r.s_comb != null ? r.s_comb.toFixed(0) + `<span class="why">#${R.comb[r.t]}${r.comb_n < 3 ? ` · จาก ${r.comb_n} ชั้น` : ''}</span>` : '—<span class="why">ไม่มี Value</span>'}</td>`;
    case 'value': return `<td>${rk('value', r.v_short || '')}</td>`;
    case 'ey': return `<td class="num c-ey" title="${tip(r.m.ebit)} · EV ${tip(r.m.ev)}">${r.v_ok ? pct(r.ey - W) : '—<span class="why">' + esc(r.v_short) + '</span>'}</td>`;
    case 'rel': return `<td class="num c-rel">${r.v_ok ? r.rel.toFixed(2) + `<span class="why">${r.evebit.toFixed(1)}x · ${esc(r.rel_basis.group)}</span>` : '—'}</td>`;
    case 'ggap': return `<td class="num c-g">${r.v_ok ? pct(r.gimp) + `<span class="why">จริง ${r.g3 == null ? '— ไม่มี FY3' : pct(r.g3)}</span>` : '—'}</td>`;
    case 'fcfy': return `<td class="num c-fy">${!r.v_ok ? '—' : r.fcfy != null ? pct(r.fcfy) + (I.scored.fcfy ? '' : '<span class="why">ยังไม่นับคะแนน</span>') : pend(r, 'fcf') ? `<span class="muted">—</span><span class="why">${PEND}</span>` : '—<span class="why">ไม่มี FCF</span>'}</td>`;
    case 'quality': return `<td>${rk('quality', r.q_short || '')}${r.s_quality != null && st.cols === 'quality' ? `<span class="why">${r.s_quality.toFixed(0)} คะแนน</span>` : ''}</td>`;
    case 'q': return `<td class="num c-q" title="${tip(r.m.ebit)}">${r.roic != null ? pct(r.roic - W) : '—<span class="why">' + esc(r.q_short) + '</span>'}</td>`;
    case 'moat': return `<td class="mo"><span aria-hidden="true">${ml[0]}</span> ${ml[1]}${R.moat[r.t] != null ? ` <span class="num muted">#${R.moat[r.t]}</span>` : ''}<span class="why">${r.moat_why ? 'รายได้ < $200M ไม่จัดอันดับ' : `ลงคะแนน ${mo.cast} จาก ${mo.possible}`}</span></td>`;
    case 'cap': return `<td class="num">${qcell(r, 'cap', q => `${q.v}/${q.of}`)}</td>`;
    case 'inc': return `<td class="num">${qcell(r, 'inc', q => pct(q.v) + `<span class="why">ROIC ส่วนเพิ่ม ${pctU(q.raw / 100)}</span>`)}</td>`;
    case 'fcfconv': return `<td class="num">${qcell(r, 'fcfconv', q => (q.v * 100).toFixed(0) + '%')}</td>`;
    case 'sbc': return `<td class="num c-x">${qcell(r, 'sbc', q => pctU(q.v))}</td>`;
    case 'dil': return `<td class="num c-x">${qcell(r, 'dil', q => pct(q.v))}</td>`;
    case 'ar': return `<td class="c-x">${qcell(r, 'ar', q => q.v ? '<span class="pill warn">โตเร็วกว่ารายได้</span>' : 'ปกติ')}</td>`;
    case 'risk': return `<td class="num">${riskCell(r)}</td>`;
    case 'vol': return `<td class="num">${infoCell(r, x => x.vol == null ? '—' : pctU(x.vol, 0))}</td>`;
    case 'beta': return `<td class="num">${infoCell(r, x => x.beta == null ? '—' : x.beta.toFixed(2))}</td>`;
    case 'corr': return `<td>${infoCell(r, x => x.top ? `<span class="num">${x.top.value.toFixed(2)}</span><span class="why">กับ ${esc(x.top.ticker)}</span>` : '—')}</td>`;
    case 'mom': return `<td>${infoCell(r, x => `${esc(x.mom || '—')}<span class="why">RSI ${x.ind ? x.ind.rsi : '—'} · MACD ${esc(x.ind ? x.ind.macd_signal : '—')}</span>`)}</td>`;
    case 'filed': return `<td>${filingCell(r)}</td>`;
  }
  return '<td>—</td>';
}

function tags(r) {
  let t = '';
  if (r.challenger) t += `<span class="pill tag" title="${esc(`ส่วนแบ่ง ${pctU(r.challenger.me[0], 2)} → ${pctU(r.challenger.me[1], 2)} ขณะที่ ${r.challenger.vs} ${pctU(r.challenger.them[0], 2)} → ${pctU(r.challenger.them[1], 2)} (${r.challenger.group})`)}">ผู้ท้าชิง ${esc(r.challenger.vs)}</span>`;
  if (r.sim && r.sim.length) t += `<span class="pill tag">ทดแทน/เทียบกับ ${esc(r.sim.join(', '))}</span>`;
  return t;
}

function row(I, r, R) {
  const open = st.open.has(I.key + r.t), C = cols();
  const flags = r.flags.map(f => `<span class="pill warn" title="${esc(f[1])}">${FLAG[f[0]]}</span>`).join('');
  return `<tr class="${r.hold ? 'hold' : ''}">
    <th scope="row" class="tk"><button type="button" class="xbtn" aria-expanded="${open}" aria-controls="d-${I.key}-${esc(r.t)}" data-x="${esc(r.t)}">${esc(r.t)}<span class="sr"> ดูรายละเอียดและแหล่งข้อมูล</span></button>
      <span class="nm" title="${esc(r.name)}">${esc(r.name)}</span>${r.hold ? '<span class="pill hold">ในพอร์ต</span>' : ''}${tags(r)}${flags}</th>
    ${C.slice(1).map(([k]) => cell(k, I, r, R)).join('')}
  </tr>${open ? detail(I, r, C.length) : ''}`;
}

function sorted(I, rows, R) {
  const f = SORTV[st.sort];
  return rows.slice().sort((a, b) => {
    const x = f(a, R), y = f(b, R);
    if (x == null && y == null) return a.t < b.t ? -1 : 1;
    if (x == null) return 1;
    if (y == null) return -1;
    if (x === y) return a.t < b.t ? -1 : 1;
    return (x < y ? -1 : 1) * st.dir;
  });
}

function funnel(I, rows, R) {
  const s0 = I.rows.filter(r => viewKey(I) === 'all' || 'sic:' + r.sic === viewKey(I));
  const s1 = rows, s2 = s1.filter(r => R.value[r.t] != null), s3 = s2.filter(r => r.roic != null && r.roic > I.wacc);
  const s4 = s3.filter(r => r.moat.label === 'improving' || r.moat.label === 'stable');
  const s5 = s4.filter(r => R.value[r.t] <= 10);
  return [['สมาชิกในมุมมองนี้', s0], ['ผ่านกฎข้อมูล 1–3', s1], ['จัดอันดับ Value ได้', s2], ['ROIC > WACC', s3], ['Moat กว้างขึ้นหรือคงที่', s4], ['อยู่ใน Value 10 อันดับแรก', s5]];
}

function scatter(I, rows, R, cw) {
  const pts = rows.filter(r => R.value[r.t] != null && r.roic != null);
  const skip = rows.filter(r => R.value[r.t] != null && r.roic == null).length;
  const X = [-0.15, 0.15], Y = [-0.4, 0.8], T0 = 0, w = Math.max(300, Math.round(cw || 560)), h = Math.round(Math.min(340, Math.max(240, w * 0.55))), L = 44, B = 30, T = 10, Rr = 12;
  const sx = x => L + (Math.max(X[0], Math.min(X[1], x)) - X[0]) / (X[1] - X[0]) * (w - L - Rr);
  const sy = y => T + (1 - (Math.max(Y[0], Math.min(Y[1], y)) - Y[0]) / (Y[1] - Y[0])) * (h - T - B);
  const W = I.wacc, top = new Set(Object.keys(R.comb).filter(t => R.comb[t] <= 5));
  let g = '';
  for (const v of [-0.1, -0.05, 0.05, 0.1]) g += `<line class="ax" x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${h - B}"/><text x="${sx(v)}" y="${h - B + 14}" text-anchor="middle">${pct(v, 0)}</text>`;
  for (const v of [-0.2, 0.2, 0.4, 0.6]) g += `<line class="ax" x1="${L}" x2="${w - Rr}" y1="${sy(v)}" y2="${sy(v)}"/><text x="${L - 6}" y="${sy(v) + 4}" text-anchor="end">${pct(v, 0)}</text>`;
  g += `<line class="zero" x1="${sx(0)}" x2="${sx(0)}" y1="${T}" y2="${h - B}"/><line class="zero" x1="${L}" x2="${w - Rr}" y1="${sy(0)}" y2="${sy(0)}"/><text x="${sx(0)}" y="${h - B + 14}" text-anchor="middle">0</text><text x="${L - 6}" y="${sy(0) + 4}" text-anchor="end">0</text>`;
  g += `<text x="${w - Rr}" y="${h - 2}" text-anchor="end">EBIT/EV − WACC (ขวา = ถูกกว่า)</text><text x="${L + 4}" y="${T + 10}">ROIC − WACC (บน = คุณภาพสูงกว่า)</text>`;
  const clipped = [];
  const marks = pts.map(r => {
    const x = r.ey - W, y = r.roic - W, cx = sx(x), cy = sy(y);
    const out = x < X[0] || x > X[1] || y < Y[0] || y > Y[1];
    if (out) clipped.push(r.t);
    const right = cx > w - 60;
    const lab = r.hold || top.has(r.t) ? `<text x="${right ? cx - 9 : cx + 7}" y="${cy < 24 ? cy + 16 : cy - 6}" text-anchor="${right ? 'end' : 'start'}" style="font-weight:600;fill:var(--ink)">${esc(r.t)}</text>` : '';
    const sh = r.hold ? `<path d="M${cx} ${cy - 6}L${cx + 6} ${cy}L${cx} ${cy + 6}L${cx - 6} ${cy}Z" fill="var(--c5)" stroke="var(--surface)"/>`
      : top.has(r.t) ? `<circle cx="${cx}" cy="${cy}" r="5" fill="var(--c1)" stroke="var(--surface)"/>`
        : `<circle cx="${cx}" cy="${cy}" r="3.5" fill="none" stroke="var(--ctl)" stroke-width="1.5"/>`;
    return (out ? `<circle cx="${cx}" cy="${cy}" r="8" fill="none" stroke="var(--ink-2)" stroke-dasharray="2 2"/>` : '') + sh + lab;
  }).join('');
  const holds = pts.filter(r => r.hold).map(r => `${r.t} (${pct(r.ey - W)}, ${pct(r.roic - W)})`);
  const lbl = `กราฟกระจาย ${pts.length} หุ้น: แกนนอน EBIT/EV − WACC แกนตั้ง ROIC − WACC. ในพอร์ต: ${holds.join(', ') || 'ไม่มี'}. 5 อันดับรวมแรก: ${[...top].join(', ')}. อยู่นอกกรอบ (วาดที่ขอบ): ${clipped.join(', ') || 'ไม่มี'}.`;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(lbl)}" width="${w}" height="${h}" style="display:block;max-width:100%">${g}${marks}</svg>
   <div class="legend"><span><svg width="12" height="12" aria-hidden="true"><path d="M6 0L12 6L6 12L0 6Z" fill="var(--c5)"/></svg> ในพอร์ต</span><span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="var(--c1)"/></svg> 5 อันดับรวมแรก</span><span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4" fill="none" stroke="var(--ctl)" stroke-width="1.5"/></svg> อื่น ๆ</span><span><svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="var(--ink-2)" stroke-dasharray="2 2"/></svg> ค่าเกินกรอบ วาดที่ขอบ${clipped.length ? ': ' + clipped.map(esc).join(', ') : ''}</span></div>
   <p class="note">แสดงเฉพาะที่มีทั้ง Value และ Quality · ไม่แสดง ${skip} ราย (ROIC n/m)</p>`;
}

function moatBox(I, rows) {
  const n = { improving: 0, stable: 0, eroding: 0, none: 0 };
  rows.forEach(r => n[mlabel(r)]++);
  const tot = rows.length || 1, keys = ['improving', 'stable', 'eroding', 'none'];
  const lbl = keys.map(k => `${MOAT[k][1]} ${n[k]}`).join(', ');
  return `<div class="stack" role="img" aria-label="ป้าย moat ของ ${rows.length} หุ้น: ${lbl}">${keys.map(k => `<i class="${k}" style="width:${n[k] / tot * 100}%;background:${MOAT[k][2]}"></i>`).join('')}</div>
   <ul class="mlist">${keys.map(k => `<li><span><span class="sw" style="background:${k === 'none' ? 'repeating-linear-gradient(135deg,var(--ctl) 0 2px,transparent 2px 4px)' : MOAT[k][2]};border:1px solid var(--ctl)"></span>${MOAT[k][0]} ${MOAT[k][1]}</span><span class="num">${n[k]}</span></li>`).join('')}</ul>
   <p class="note">ป้ายจากกฎ ±2: ผลรวมคะแนน S1–S4 ≥ +2 = กว้างขึ้น, ≤ −2 = แคบลง, ต้องมีอย่างน้อย 2 สัญญาณ. กรอบยังเป็นค่าชั่วคราว (provisional) เหมือนแท็บคู่แข่ง</p>`;
}

function formula(I) {
  const sc = k => I.scored[k] ? '' : ` (ยังไม่นับ: มีข้อมูล ${I.cover[k]}% ต้อง ≥80%)`;
  const off = [['fcfconv', 'FCF ÷ NOPAT'], ['sbc', 'SBC'], ['dil', 'dilution'], ['pen', 'การหักคะแนน']].filter(([k]) => !I.scored[k]);
  const offTxt = off.length ? ` · ยังไม่นับจนกว่าข้อมูลครบ ≥80% ของชั้น (ตอนนี้ ${I.cover[off[0][0]]}%): ${off.map(x => x[1]).join(', ')}` : '';
  if (st.cols === 'quality') return `Quality = เฉลี่ยถ่วงของ percentile ที่มีค่า: ROIC − WACC 30%, CAP (ปี ROIC > WACC ÷ ปีที่วัด) 15%, ROIC ส่วนเพิ่ม − WACC 15%, FCF ÷ NOPAT 15%, SBC % รายได้ 10% (ต่ำดีกว่า), dilution %/ปี 15% (ต่ำดีกว่า) แล้วหักลูกหนี้โตเร็วกว่ารายได้ 10, หนี้สุทธิ/EBITDA > 3 หัก 5 (> 5 หัก 10), coverage < 3 เท่า หัก 5 (< 1.5 หัก 10) รวมหักไม่เกิน 20${offTxt}`;
  if (st.cols === 'info') return 'คอลัมน์ชุดนี้ไม่เข้าคะแนนรวม: วันยื่นงบ, volatility, beta, correlation กับหุ้นในพอร์ต, จังหวะ (momentum) ใช้ประกอบการตัดสินใจเท่านั้น' + (I.has_g2 ? '' : `<br><b>ราคาย้อนหลัง (กลุ่ม 2): ${PEND}</b> ทุกแถวในชุดข้อมูลนี้ยังไม่มี volatility, beta, correlation และจังหวะ`);
  return `คะแนนรวม = 50% Value + 30% Quality + 20% Moat (percentile 0–100 ในทั้งอุตสาหกรรม ต้องมี Value ชั้นที่ไม่มีค่าไม่นับแล้วถ่วงใหม่). Value = เฉลี่ย percentile ของ EBIT/EV − WACC, EV/EBIT เทียบ SIC ปรับโต, (g จริง − g ที่ราคาสะท้อน) และ FCF yield${sc('fcfy')}. Moat ต้องรายได้ ≥ $200M`;
}

function oneoffBox(I) {
  const on = I.rows.filter(r => r.oo.v2.length), watch = I.rows.filter(r => !r.oo.v2.length && r.oo.watch);
  return `<details class="box s12" id="oo"><summary>กฎ one-off: ติดธง ${on.length} ราย · ธงตรวจ margin ${watch.length} ราย</summary>
    <p class="note">ติดธงเฉพาะเมื่อมีหลักฐานรายการครั้งเดียว: O1 กำไรไม่ใช่จากการดำเนินงานหรือไม่ใช่เงินสดอยู่ใน EBIT (มีหลักฐานจากผลประกอบการ), O2 add-back มากกว่าครึ่งของ EBIT ดิบ และ margin ดิบ TTM ไม่ฟื้นเกิน FY ล่าสุด + 2 จุด, O3 add-back ชนเพดาน 25%. margin กระโดดที่มีรายได้หรือ GM ฟื้นตามไม่ใช่ one-off; ถ้าไม่มีอะไรหนุนเป็นแค่ธงตรวจ (F8) ยังจัดอันดับ. ตัวอ่าน SEC หักกำไรครั้งเดียวที่งบวางไว้ใน EBIT เองแล้ว (E79)</p>
    <table class="xl"><thead><tr><th scope="col">หุ้น</th><th scope="col">ธง</th><th scope="col">เหตุผล</th></tr></thead><tbody>
    ${on.map(r => `<tr><th scope="row" class="num">${esc(r.t)}</th><td>one-off</td><td>${esc(r.oo.v2.join('; '))}</td></tr>`).join('')}
    ${watch.map(r => `<tr><th scope="row" class="num">${esc(r.t)}</th><td>ตรวจ margin</td><td>${esc(r.oo.watch)}</td></tr>`).join('')}</tbody></table></details>`;
}

function render() {
  const I = ind(), R = ranks(I), rows = viewRows(I), v = viewKey(I);
  const F = funnel(I, rows, R), max = F[0][1].length || 1, cand = F[5][1].filter(r => !r.hold), heldPass = F[5][1].filter(r => r.hold);
  const sics = Object.entries(I.sic_groups).filter(([s]) => I.ranks['sic:' + s]).sort((a, b) => b[1] - a[1]);
  const excl = I.rows.filter(r => r.excl);
  const holds = sorted(I, rows.filter(r => r.hold), R), rest = sorted(I, rows.filter(r => !r.hold && (st.all || R.value[r.t] != null)), R);
  const hidden = rows.filter(r => !r.hold && R.value[r.t] == null).length;
  const th = cols().map(([k, l, c]) => `<th scope="col" class="${c}"${st.sort === k ? ` aria-sort="${st.dir > 0 ? 'ascending' : 'descending'}"` : ''}><button type="button" data-sort="${k}">${l}<span aria-hidden="true">${st.sort === k ? (st.dir > 0 ? ' ▲' : ' ▼') : ''}</span></button></th>`).join('');
  const refRows = v === 'all' ? I.refs.map(x => `<tr class="hold"><th scope="row" class="tk"><b class="num">${esc(x.t)}</b><span class="pill hold">ในพอร์ต</span></th><td colspan="${cols().length - 1}" style="text-align:left" class="note">— ${esc(x.missing)}</td></tr>`).join('') : '';
  const vd = v === 'all' ? `ทั้งกลุ่ม Damodaran: ${esc(I.industry)}` : `เฉพาะธุรกิจเดียวกัน SIC ${esc(v.slice(4))}`;
  document.getElementById('scoutBody').innerHTML = `
  <div>
  <div class="ctl">
    <div class="seg" role="group" aria-label="เลือกกลุ่มที่แสดง (ตรงกับแท็บคู่แข่ง)">
      <button type="button" data-view="sub" aria-pressed="${v !== 'all'}">เฉพาะธุรกิจเดียวกัน (SIC)</button><button type="button" data-view="all" aria-pressed="${v === 'all'}">ทั้งกลุ่ม Damodaran</button></div>
    ${v !== 'all' ? `<label class="note" for="sic">SIC <select id="sic">${sics.map(([s, n]) => `<option value="${s}"${'sic:' + s === v ? ' selected' : ''}>${s} (${n} ราย)</option>`).join('')}</select></label>` : ''}
    <span class="note">มุมมอง: ${vd} · อันดับนับใหม่ในมุมมอง ลำดับระหว่างหุ้นไม่เปลี่ยน · S2 และ S3 ใช้ฐาน SIC ตามกฎของแต่ละหุ้นในทั้งสองมุมมอง</span>
  </div>
  <div class="band">
    <section class="box s8" aria-labelledby="h-fun"><div class="hero">
      <div><h2 id="h-fun">น่าส่งรัน DEEP</h2><p class="big">${cand.length}</p>
        <p class="note">หุ้นนอกพอร์ตที่ผ่านครบทุกชั้นของ funnel (${vd})${heldPass.length ? ` · ในพอร์ตที่ผ่านด้วย: ${heldPass.map(r => esc(r.t)).join(', ')}` : ''}</p>
        <div class="chips">${cand.map(r => `<button type="button" class="chip" data-jump="${esc(r.t)}">${esc(r.t)}</button>`).join('') || '<span class="note">ไม่มีหุ้นผ่านครบ</span>'}</div></div>
      <div><h3>Funnel สามชั้น (Value → Quality → Moat)</h3>
        <ol class="funnel" aria-label="จำนวนหุ้นในแต่ละขั้นของ funnel">${F.map(([l, s]) => `<li><span class="fl">${l}</span><span class="num fn">${s.length}</span><div class="fbar" aria-hidden="true"><i style="width:${s.length / max * 100}%"></i></div></li>`).join('')}</ol>
      </div></div></section>
    <section class="box s4" aria-labelledby="h-st"><h2 id="h-st" class="sr">ตัวเลขประกอบ</h2><div class="stats">
      <div class="stat"><div class="l">WACC อุตสาหกรรม</div><div class="v">${pctU(I.wacc, 2)}</div><div class="note">${esc(I.wacc_src || 'Damodaran')}</div></div>
      <div class="stat"><div class="l">ข้อมูล ณ</div><div class="v num" style="font-size:var(--t-lg)">${esc(I.as_of)}</div><div class="note">ราคาปิด T-1 ${esc(I.ref_date)}</div></div>
      <div class="stat"><div class="l">ตัดออก</div><div class="v">${excl.length}</div><div class="note"><a href="#ex">ดูรายชื่อและเหตุผล</a></div></div>
      <div class="stat"><div class="l">สมาชิก SEC ทั้งกลุ่ม</div><div class="v">${I.n_members}</div><div class="note">ส่วนแบ่ง S2 ใช้ CY${I.years[0]}→CY${I.years[1]}</div></div>
      <div class="stat" style="grid-column:1/-1"><div class="l">ช่องว่างก่อนชนเพดาน sector</div>${I.sector_headroom && I.sector_headroom.value != null ? `<div class="v">${I.sector_headroom.value.toFixed(1)} จุด</div><div class="note">${esc(I.sector_headroom.sector)} ${I.sector_headroom.weight_pct.toFixed(1)}% ของพอร์ต เพดาน ${I.sector_headroom.cap_pct.toFixed(0)}% (ไม่นับคะแนน)</div>` : `<div class="v muted">—</div><div class="note">${esc((I.sector_headroom || {}).reason || PEND)}</div>`}</div>
    </div></section>
  </div>
  <div class="band">
    <section class="box s8" aria-labelledby="h-sc"><h2 id="h-sc">Value เทียบ Quality</h2><p class="note" style="margin:0 0 6px">มุมขวาบน = ถูกกว่าต้นทุนเงินทุนและสร้างผลตอบแทนเกินต้นทุน</p><div id="scat"></div></section>
    <section class="box s4" aria-labelledby="h-mo"><h2 id="h-mo">Moat proxy</h2>${moatBox(I, rows)}</section>
  </div>
  <section class="box" aria-labelledby="h-tb" style="margin-bottom:12px"><div class="tblwrap">
    <h2 id="h-tb">ตารางจัดอันดับ <span class="proxy">ค่าประมาณ ไม่ใช่คะแนน DEEP</span></h2>
    <div class="ctl"><div class="seg" role="group" aria-label="ชุดคอลัมน์">${Object.entries(COLSETS).map(([k, c]) => `<button type="button" data-cols="${k}" aria-pressed="${st.cols === k}">${c.label}</button>`).join('')}</div></div>
    <p class="formula">${formula(I)}</p>
    <div class="ctl"><label class="note"><input type="checkbox" id="all"${st.all ? ' checked' : ''}> แสดงแถวที่ยังจัดอันดับ Value ไม่ได้ด้วย (${hidden} ราย)</label><span class="note">กดชื่อหุ้นเพื่อดูทุกค่าพร้อมแหล่งและงวด · เรียงตาม ${sortLabel()}</span></div>
    <table class="sc"><caption class="sr">หุ้นใน ${vd} เรียงตาม ${sortLabel()}</caption>
      <thead><tr>${th}</tr></thead>
      <tbody><tr class="grp"><td colspan="${cols().length}">ในพอร์ต (แถวอ้างอิง)</td></tr>${holds.map(r => row(I, r, R)).join('')}${refRows}
      <tr class="grp"><td colspan="${cols().length}">ผู้สมัคร ${rest.length} ราย</td></tr>${rest.map(r => row(I, r, R)).join('')}</tbody></table>
  </div></section>
  <div class="band">
    <details class="box s7" id="ex"><summary>ตัดออก ${excl.length} ราย (กฎข้อมูล R1–R3)</summary>
      <p class="note">${Object.entries(excl.reduce((a, r) => (a[r.excl[0]] = (a[r.excl[0]] || 0) + 1, a), {})).map(([k, n]) => `${RULE[k]}: ${n}`).join(' · ')} (นับตามกฎแรกที่ไม่ผ่าน)</p>
      <table class="xl"><thead><tr><th scope="col">หุ้น</th><th scope="col">กฎ</th><th scope="col">เหตุผลทุกข้อที่ไม่ผ่าน</th></tr></thead>
      <tbody>${excl.map(r => `<tr><th scope="row" class="num">${esc(r.t)}</th><td>${RULE[r.excl[0]]}</td><td>${r.hits.map(h => esc(h[1])).join('<br>')}${r.flags.filter(f => f[0] === 'DQ').map(f => '<br><span class="muted">ข้อมูล: ' + esc(f[1]) + '</span>').join('')}</td></tr>`).join('')}</tbody></table></details>
    <section class="box s5" aria-labelledby="h-cv"><h2 id="h-cv">ข้อควรรู้ของชุดข้อมูลนี้</h2><p class="note">ตัวเลขไม่ได้แก้ด้วยมือในหน้านี้ ข้อบกพร่องของ reader กลางแก้ที่ตัวอ่าน SEC (E77–E82)</p>
      <ul class="cav">${(I.caveats || []).map(c => `<li>${esc(c)}</li>`).join('') || '<li>ไม่มี</li>'}</ul></section>
  </div>
  <div class="band">${oneoffBox(I)}</div></div>`;
  drawScatter();
  document.getElementById('scoutLive').textContent = `${I.industry} · ${vd} · คอลัมน์ ${COLSETS[st.cols].label} · เรียงตาม ${sortLabel()} ${st.dir > 0 ? 'น้อยไปมาก' : 'มากไปน้อย'} · ${holds.length + rest.length} แถว`;
  document.getElementById('scoutFoot').innerHTML = `Sector scout · ${esc(I.industry)} · WACC: ${esc(I.wacc_src)} · ทุกตัวเลขเป็นค่าประมาณเพื่อคัดกรอง ไม่ใช่คะแนน DEEP ไม่เปลี่ยนคะแนนของหุ้นในพอร์ต และไม่ใช่คำแนะนำการลงทุน`;
}

function drawScatter() {
  const el = document.getElementById('scat'); if (!el) return;
  const I = ind(); if (!I) return; el.innerHTML = scatter(I, viewRows(I), ranks(I), el.clientWidth);
}
let rz; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(drawScatter, 120); });
SCOUT_ROOT().addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  const I = ind();
  if (scoutAction(b)) return;
  if (!I) return;
  if (b.dataset.view) {
    if (b.dataset.view === 'all') st.view[I.key] = 'all';
    else st.view[I.key] = defaultSub(I);
    render(); document.querySelector(`[data-view="${b.dataset.view}"]`).focus(); return;
  }
  if (b.dataset.cols) { st.cols = b.dataset.cols; render(); document.querySelector(`[data-cols="${b.dataset.cols}"]`).focus(); return; }
  if (b.dataset.sort) {
    const k = b.dataset.sort;
    if (st.sort === k) st.dir = -st.dir; else { st.sort = k; st.dir = 1; }
    render(); document.querySelector(`[data-sort="${k}"]`).focus(); return;
  }
  if (b.dataset.x) {
    const k = I.key + b.dataset.x;
    st.open.has(k) ? st.open.delete(k) : st.open.add(k);
    render(); document.querySelector(`[data-x="${CSS.escape(b.dataset.x)}"]`).focus(); return;
  }
  if (b.dataset.jump) {
    const t = b.dataset.jump; st.open.add(I.key + t);
    if (!st.all && ranks(I).value[t] == null) st.all = true;
    render(); const x = document.querySelector(`[data-x="${CSS.escape(t)}"]`); x.scrollIntoView({ block: 'center' }); x.focus();
  }
});
SCOUT_ROOT().addEventListener('change', e => {
  if (scoutChange(e)) return;
  if (e.target.id === 'sic') { st.view[ind().key] = 'sic:' + e.target.value; render(); document.getElementById('sic').focus(); }
  if (e.target.id === 'all') { st.all = e.target.checked; render(); document.getElementById('all').focus(); }
});

const fmtTime = s => { if (!s) return '—'; try { return new Date(s).toLocaleString('th-TH', { dateStyle: 'medium', timeStyle: 'short' }); } catch (e) { return s; } };
function cached() { return (SC.list ? SC.list.industries : []).filter(x => x.run); }
function statusLine() {
  const s = SC.list ? SC.list.status : {};
  const run = s.state === 'running', intr = s.state === 'interrupted';
  const prog = run && s.current ? `กำลังรัน ${esc(s.current)}${s.total_n ? ` ${s.done_n || 0}/${s.total_n} ราย` : ''}${(s.queue || []).length > 1 ? ` · รอคิว ${(s.queue || []).length - 1}` : ''}` : '';
  const fin = s.finished || Object.values(s.runs || {}).map(x => x.finished).sort().pop();
  const last = fin ? `รันเสร็จล่าสุด ${fmtTime(fin)}` : 'ยังไม่เคยรัน';
  return `<span class="spin" style="display:${run ? 'inline-block' : 'none'}"></span>${run ? prog : intr ? `<b>รันค้างไว้</b> (เซิร์ฟเวอร์รีสตาร์ต) เหลือ ${esc((s.queue || []).join(', '))} <button type="button" class="btn" data-scout-resume="1">ทำต่อ</button>` : last}${s.error ? ` <span class="pill warn">ผิดพลาด: ${esc(s.error)}</span>` : ''}`;
}
function controls() {
  const L = SC.list; if (!L) return '';
  const have = cached(), def = new Set(L.default);
  const pick = SC.pick || (SC.pick = new Set(L.default));
  const opt = have.map(x => `<option value="${esc(x.name)}"${x.name === SC.sel ? ' selected' : ''}>${esc(x.name)}${x.held.length ? ' · ในพอร์ต ' + esc(x.held.join(', ')) : ''}</option>`).join('');
  const runOf = x => x.run ? `${fmtTime(x.run.finished)} · ${x.run.members} ราย · ${Math.round(x.run.secs / 60 * 10) / 10} นาที · SEC ${x.run.requests.sec} · ราคา ${x.run.requests.price} · ราคาย้อนหลัง ${x.run.requests.history} · FMP ${x.run.requests.fmp}` : 'ยังไม่มีข้อมูล';
  const list = L.industries.slice().sort((a, b) => (def.has(b.name) - def.has(a.name)) || a.name.localeCompare(b.name));
  const cur = have.find(x => x.name === SC.sel);
  return `<div class="ctl">
    <label class="note" for="scoutInd">อุตสาหกรรมที่แสดง <select id="scoutInd">${opt || '<option value="">ยังไม่มีข้อมูล กดรันก่อน</option>'}</select></label>
    ${cur ? `<button type="button" class="btn" data-scout-run="${esc(cur.name)}">รีเฟรชอุตสาหกรรมนี้</button>` : ''}
    <span class="note" id="scoutStatus" aria-live="polite">${statusLine()}</span>
  </div>
  ${cur ? `<p class="note" style="margin:-6px 0 10px">รันล่าสุด: ${esc(runOf(cur))}</p>` : ''}
  <details class="box" style="margin-bottom:12px"><summary>เลือกอุตสาหกรรมที่จะรัน (${pick.size} ที่เลือก · ค่าเริ่มต้น = อุตสาหกรรมของหุ้นในพอร์ต)</summary>
    <p class="note">งานรันบนเซิร์ฟเวอร์ทีละอุตสาหกรรม อ่าน SEC ราคา T-1 และราคาย้อนหลัง 2 ปี ไม่ใช้ FMP เลย สมาชิกที่อ่านแล้วเก็บไว้ ${7} วัน รันซ้ำหรือรันต่อหลังรีสตาร์ตจึงดึงเฉพาะที่ขาด</p>
    <fieldset style="border:0;margin:0;padding:0"><legend class="sr">อุตสาหกรรม</legend>
    <ul class="mlist" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr));display:grid">${list.map(x => `<li><label><input type="checkbox" data-scout-pick="${esc(x.name)}"${pick.has(x.name) ? ' checked' : ''}> ${esc(x.name)}${x.members != null ? ` <span class="muted">(${x.members} ราย US)</span>` : ''}${x.held.length ? ` <span class="pill hold">${esc(x.held.join(', '))}</span>` : ''}</label><span class="note">${x.run ? 'มีข้อมูล ' + esc(fmtTime(x.run.finished)) : ''}</span></li>`).join('')}</ul></fieldset>
    <div class="wl"><button type="button" class="btn primary" data-scout-runsel="1"${pick.size ? '' : ' disabled'}>รันที่เลือก (${pick.size})</button></div>
  </details>`;
}
function wlBox(I, r) {
  if (r.hold) return '<p class="note">อยู่ในพอร์ตแล้ว</p>';
  const w = SC.watch[r.t], q = SC.quota;
  const cost = q ? `ใช้ FMP ราว ${q.per_ticker} ครั้ง (วันนี้เหลือ ${q.headroom_calls} ครั้ง)` : 'ใช้ FMP ราว 6 ครั้ง';
  const id = `wln-${I.key}-${esc(r.t)}`;
  if (!w) return `<div class="wl"><button type="button" class="btn" data-scout-wl="${esc(r.t)}" aria-describedby="${id}">เพิ่มเข้า watchlist</button><span class="note" id="${id}">เพิ่มแล้วจะมีปุ่มรัน DEEP ให้ (${esc(cost)})</span></div>`;
  if (w.state === 'added') return `<div class="wl"><button type="button" class="btn primary" data-scout-deep="${esc(r.t)}" aria-describedby="${id}">รัน DEEP ${esc(r.t)}</button><span class="note" id="${id}">อยู่ใน watchlist แล้ว · ${esc(cost)}</span></div>`;
  if (w.state === 'running') return `<div class="wl"><span class="spin" style="display:inline-block"></span><span class="note">กำลังรัน DEEP ${esc(r.t)}…</span></div>`;
  return `<div class="wl"><span class="note">${esc(w.msg || '')}</span> <button type="button" class="btn" onclick="showTab(2,true)">ไปแท็บ Watchlist</button></div>`;
}
async function getJSON(u, opt) { const r = await fetch(u, opt); const d = await r.json(); if (!r.ok) throw new Error(d.error || d.detail || r.status); return d; }
async function refreshList() {
  SC.list = await getJSON('/api/scout');
  try { SC.quota = await getJSON('/api/quota'); } catch (e) { SC.quota = null; }
}
function paint() {
  document.getElementById('scoutCtl').innerHTML = controls();
  if (SC.cur) render();
  else document.getElementById('scoutBody').innerHTML = `<div class="empty">${cached().length ? 'กำลังโหลด…' : 'ยังไม่มีข้อมูล เลือกอุตสาหกรรมแล้วกด รันที่เลือก (ค่าเริ่มต้นคืออุตสาหกรรมของหุ้นในพอร์ต)'}</div>`;
}
async function loadView(name) {
  SC.sel = name; SC.cur = null; paint();
  try { const d = await getJSON('/api/scout/view?industry=' + encodeURIComponent(name)); SC.cur = d; SRC = d.sources || []; }
  catch (e) { document.getElementById('scoutBody').innerHTML = `<div class="banner tone t-bear">&#9888; ${esc(String(e.message || e))}</div>`; return; }
  paint();
}
let poll = null;
function watchJob() {
  clearTimeout(poll);
  const s = SC.list && SC.list.status;
  if (!s || s.state !== 'running') return;
  poll = setTimeout(async () => {
    const was = (SC.list.status.runs || {});
    try { await refreshList(); } catch (e) { return; }
    const el = document.getElementById('scoutStatus'); if (el) el.innerHTML = statusLine();
    const now = SC.list.status.runs || {};
    const fresh = Object.keys(now).filter(k => !was[k] || was[k].finished !== now[k].finished);
    if (fresh.length) { if (!SC.sel || fresh.includes(SC.sel)) loadView(SC.sel || fresh[0]); else paint(); }
    watchJob();
  }, 4000);
}
async function startRun(names) {
  try { SC.list.status = await getJSON('/api/scout/run?' + names.map(n => 'industry=' + encodeURIComponent(n)).join('&'), { method: 'POST' }); }
  catch (e) { alert('รันไม่ได้: ' + (e.message || e)); return; }
  paint(); watchJob();
}
function scoutAction(b) {
  const d = b.dataset;
  if (d.scoutRun) { startRun([d.scoutRun]); return true; }
  if (d.scoutRunsel) { startRun([...SC.pick]); return true; }
  if (d.scoutResume) { startRun([]); return true; }
  if (d.scoutWl) {
    const t = d.scoutWl;
    fetch('/api/watchlist/add?ticker=' + encodeURIComponent(t), { method: 'POST' }).then(r => {
      SC.watch[t] = r.ok ? { state: 'added' } : { state: 'done', msg: 'เพิ่มไม่ได้' };
      render(); const x = SCOUT_ROOT().querySelector(`[data-scout-deep="${CSS.escape(t)}"]`); if (x) x.focus();
    });
    return true;
  }
  if (d.scoutDeep) {
    const t = d.scoutDeep, q = SC.quota;
    if (!confirm(`รัน DEEP ${t} ตอนนี้?\nใช้ FMP ราว ${q ? q.per_ticker : 6} ครั้ง${q ? ` จากที่เหลือวันนี้ ${q.headroom_calls} ครั้ง` : ''}`)) return true;
    SC.watch[t] = { state: 'running' }; render();
    getJSON('/api/watchlist/run?ticker=' + encodeURIComponent(t), { method: 'POST' }).then(res => {
      const row = (res.rows || []).find(x => x.ticker === t) || (res.rows || [])[0];
      if (res.quota) SC.quota = res.quota;
      SC.watch[t] = { state: 'done', msg: row ? `DEEP ${t}: ${row.recommendation || ''}${row.composite != null ? ' · ' + row.composite : ''}` : `รันแล้ว: ${(res.errors || []).join('; ') || 'ไม่มีผล'}` };
    }).catch(e => { SC.watch[t] = { state: 'done', msg: 'รันไม่สำเร็จ: ' + (e.message || e) }; }).finally(render);
    return true;
  }
  return false;
}
function scoutChange(e) {
  const t = e.target;
  if (t.id === 'scoutInd') { if (t.value) loadView(t.value); return true; }
  if (t.dataset && t.dataset.scoutPick) {
    t.checked ? SC.pick.add(t.dataset.scoutPick) : SC.pick.delete(t.dataset.scoutPick);
    const b = SCOUT_ROOT().querySelector('[data-scout-runsel]'); if (b) { b.textContent = `รันที่เลือก (${SC.pick.size})`; b.disabled = !SC.pick.size; }
    return true;
  }
  return false;
}
window.loadScout = async function () {
  try { await refreshList(); } catch (e) { document.getElementById('scoutBody').innerHTML = `<div class="banner tone t-bear">&#9888; scout: ${esc(String(e.message || e))}</div>`; return; }
  const have = cached();
  if (!SC.sel && have.length) SC.sel = (have.find(x => x.held.length) || have[0]).name;
  if (SC.sel && (!SC.cur || SC.cur.industry !== SC.sel)) loadView(SC.sel); else paint();
  watchJob();
};
})();
