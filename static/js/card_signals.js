function garpBadge(x){
  if(x.garp_score==null)return '';
  if(x.garp_candidate)return ` <span class="tag" style="background:#0d9488;font-size:calc(8px*var(--fs))" title="GARP candidate (Damodaran S13/S19): cheap (Price) AND quality (Economics) both ≥3. Score ${x.garp_score}/10">GARP ${x.garp_score}</span>`;
  return ` <span class="muted" style="font-size:calc(9px*var(--fs))" title="GARP score (S13/S19) = Economics + Price subscores, /10; candidate needs both ≥3">G ${x.garp_score}</span>`;
}
function momCell(x){
  // PRIMARY = faithful composite (momentum_v2). RSI/MACD/DBBMV kept as SECONDARY in tooltip.
  const m=x.momentum_v2;
  const sec=`RSI ${x.rsi_signal||'—'} (${f(x.rsi,0)}) · MACD ${x.macd_signal||'—'} · DBBMV ${x.dbbmv_signal||'—'}`;
  if(!m||!m.mom_label){
    return `<td title="No momentum yet — run Daily (price/momentum). Secondary: ${sec}"><span class="muted">&mdash;</span></td>`;
  }
  const c=m.components||{}, ck=k=>c[k]===true?'✓':c[k]===false?'✗':'·';
  const splitOnly=m.dividend_adjusted===false, stale=m.stale===true;
  const divWarn=(m.div_warn!==undefined)?m.div_warn:splitOnly;   // R2: warn only for dividend payers
  const q=m.quality||{}, dropped=(q.dropped_nonpos||0)+(q.dropped_spikes||0);
  const warn=divWarn?` ⚠ split-only price (${m.src||'stooq'}) — 12M may understate dividends`
                    :(splitOnly?` · split-only price (${m.src||'stooq'}), non-payer — accurate`:'');
  const cleaned=dropped?` · cleaned ${dropped} bad bar(s)`:'';
  const staleTxt=stale?` · ⏳ STALE ${m.age_days}d old — run Daily`:'';
  const rank=m.mom_bucket?` · rank ${m.mom_bucket}`:'';
  const crash=m.crash_guard===true, rev=(m.reversal&&m.reversal.risk===true);
  const crashTxt=crash?' · \u26A0 S9 crash guard: market risk-off, momentum crash-prone':'';
  const revTxt=rev?` · \u21BB S9 reversal risk (3y ${momPct(m.reversal.cum_return)}, near top)`:'';
  const tip=(`Composite ${m.mom_label} (${m.mom_score}/${m.mom_n} positive)`
    +` · 12-1 ${momPct(m.mom_12_1)} · 6M ${momPct(m.roc_6m)} · 12M ${momPct(m.roc_12m)}`
    +` · ${m.above_sma200===true?'above':m.above_sma200===false?'below':'?'} SMA200 · RSI ${f(m.rsi,0)}`
    +`  ||  12-1>0 ${ck('mom_12_1_pos')}  6M>0 ${ck('roc_6m_pos')}  >SMA200 ${ck('above_sma200')}  RSI>50 ${ck('rsi_gt_50')}`
    +` · source ${m.src||'?'} · ${q.bars||'?'} bars · as of ${m.as_of||'?'}${warn}${cleaned}${staleTxt}${crashTxt}${revTxt}  ||  secondary: ${sec}`).replace(/"/g,'&quot;');
  const marks=(divWarn?'<span style="color:#d97706" title="split-only price, dividend payer — 12M may understate">&#9888;</span>':'')
    +(stale?'<span style="color:#b45309" title="stale momentum — run Daily">&#9203;</span>':'')
    +(crash?'<span style="color:#dc2626" title="S9 crash guard: market risk-off (S&P below 200-DMA) — momentum crash-prone">&#9888;</span>':'')
    +(rev?'<span style="color:#7c3aed" title="S9 long-horizon reversal: ~3y run extreme and near top — reversal-prone">&#8635;</span>':'');
  return `<td title="${tip}">${tag(m.mom_label,MOMV2_COLOR[m.mom_label])}${marks}`
    +`<div class="muted" style="font-size:calc(9px*var(--fs))">6M ${momPct(m.roc_6m)}${rank} &#9432;</div></td>`;
}
function rankCell(x){
  // Cross-sectional momentum rank as its own prominent column (Top/Mid/Bottom by 12-1).
  const m=x.momentum_v2;
  if(!m||!m.mom_bucket){return '<td><span class="muted">&mdash;</span></td>';}
  const pct=(m.mom_rank_pct!=null)?Math.round(m.mom_rank_pct*100):null;
  const sub=pct!=null?`<div class="muted" style="font-size:calc(9px*var(--fs))">${pct} %ile</div>`:'';
  return `<td title="Cross-sectional momentum rank by 12-1 vs the other names${pct!=null?` — ${pct}th percentile (higher = stronger)`:''}">`
    +`${tag(m.mom_bucket,RANK_COLOR[m.mom_bucket])}${sub}</td>`;
}
function flagIcon(x){const fl=x.flags||[];if(!fl.length)return '';return ` <span title="${esc(fl.join('\n'))}" style="cursor:help;color:#d97706;font-size:calc(11px*var(--fs));font-weight:700">⚑${fl.length}</span>`}
const EARN_COLOR={beat:'#16a34a',meet:'#f59e0b',miss:'#dc2626',
                  expand:'#16a34a',flat:'#f59e0b',contract:'#dc2626'};
function _circ(list,kind){
  return (list||[]).map(e=>{
    const c=EARN_COLOR[e.grade]||'#cbd5e1';
    let tip;
    if(kind==='mgn'){
      const m=e.op_margin==null?'—':(e.op_margin*100).toFixed(1)+'%';
      const my=e.op_margin_yoy==null?'—':(e.op_margin_yoy*100).toFixed(1)+'%';
      const dp=e.delta_pp==null?'n/a':(e.delta_pp>=0?'+':'')+e.delta_pp+'pp';
      tip=`${e.quarter||''} · ${(e.grade||'').toUpperCase()} ${dp} YoY\nOp margin ${m} vs yr-ago ${my}`;
    }else{
      const sp=e.surprise_pct==null?'n/a':(e.surprise_pct>=0?'+':'')+e.surprise_pct+'%';
      const act=kind==='eps'?(e.eps_actual??'—'):fmtB(e.rev_actual);
      const est=kind==='eps'?(e.eps_estimate??'—'):fmtB(e.rev_estimate);
      tip=`${e.quarter||''} · ${(e.grade||'').toUpperCase()} ${sp}\n${kind.toUpperCase()} act ${act} vs est ${est}`;
    }
    return `<span class="ecirc" style="background:${c}" title="${esc(tip)}"></span>`;
  }).join('');
}
function earnMiss(st,k){
  // explicit "why is this row empty" marker (never a silent blank):
  //   ⟳ stale-facts  -> old data schema, a Fundamental Refresh will fill it
  //   × unavailable  -> primary AND fallback sources had no data
  const s=(st||{})[k]||{};
  const stale=s.reason==='stale-facts';
  const tip=s.detail||'ไม่มีข้อมูล — แหล่งหลักและสำรองไม่ตอบ';
  return `<span title="${esc(tip)}" style="font-size:calc(8.5px*var(--fs));color:${stale?'#b45309':'#94a3b8'};cursor:help">${stale?'&#10227; refresh':'&times; n/a'}</span>`;
}
function earnCircles(x){
  const eps=x.earnings_surprises||[], rev=x.rev_surprises||[], mgn=x.margin_trend||[];
  const st=x.earn_status;
  if(!eps.length && !rev.length && !mgn.length && !st)return '<span class="muted">&mdash;</span>';
  let h='';
  h+=`<div class="erow"><span class="elbl" title="EPS beat/miss vs analyst estimate — hover จุดสีเพื่อดู Act vs Est">EPS</span>${eps.length?_circ(eps,'eps'):earnMiss(st,'eps')}</div>`;
  h+=`<div class="erow"><span class="elbl" title="Revenue beat/miss vs analyst estimate — hover จุดสีเพื่อดู Act vs Est">Rev</span>${rev.length?_circ(rev,'rev'):earnMiss(st,'rev')}</div>`;
  h+=`<div class="erow"><span class="elbl" title="Operating-margin YoY trend: expand / flat / contract">Mgn</span>${mgn.length?_circ(mgn,'mgn'):earnMiss(st,'mgn')}</div>`;
  const pd=x.pead||{};
  if(pd.bias&&pd.bias!=='neutral'){
    // Tier1: อ่านรู้เรื่องเอง ไม่ต้อง hover (เดิมเป็น tag ▲ up*)
    const up=pd.bias==='up', strong=pd.strength==='strong';
    const bg=up?'#dcfce7':'#fee2e2', col=up?'#166534':'#991b1b';
    const q=pd.quarter?` &middot; ${esc(pd.quarter)}`:'';
    h+=`<div class="erow"><span class="elbl" title="Damodaran S25 (PEAD): ราคามักไหลตามทิศ earnings surprise ล่าสุดต่ออีกหลายสัปดาห์ — ใช้ประกอบจังหวะ ไม่ใช่เหตุผลหลัก">PEAD</span><span style="display:inline-block;font-size:calc(9px*var(--fs));font-weight:700;background:${bg};color:${col};border-radius:4px;padding:1px 6px" title="${esc(pd.note||'')}">drift ${up?'&#8593;':'&#8595;'} ${strong?'strong':'mild'}${q}</span></div>`;
  }
  return h+`<div class="muted" style="font-size:calc(8px*var(--fs))">old→new</div>`;
}
function eqBadge(x){
  if(!x.eq_verdict)return '';
  const m={CLEAN:['#dcfce7','#166534'],REVIEW:['#fef9c3','#854d0e'],LOW:['#fee2e2','#991b1b']};
  const c=m[x.eq_verdict]||['#e2e8f0','#475569'];
  return `<div title="Earnings quality (cash conversion / accruals / SBC)" style="font-size:calc(8px*var(--fs));font-weight:700;background:${c[0]};color:${c[1]};border-radius:4px;padding:1px 4px;display:inline-block;margin-top:2px">EQ ${x.eq_verdict}</div>`;
}
function adviceLine(x){
  // Damodaran ACTION synthesis (value -> quality -> timing) computed server-side
  if(!x.advice)return '';
  return `<div style="font-size:calc(11px*var(--fs));color:#334155;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:5px 8px;margin-top:5px;white-space:normal;text-align:left" title="สังเคราะห์จากทุกค่าบน card ตามลำดับ Damodaran: มูลค่า → คุณภาพ → จังหวะ">&#128161; ${esc(x.advice)}</div>`;
}
// ---- Tier1: moat chip / guard badges / display-level action override (S9) ----
function moatChip(x){
  const s=(x.key_metrics||{}).spread_pct;
  if(s==null)return '';
  const good=s>=10, mid=(s>=0&&s<10);
  const bg=good?'#dcfce7':mid?'#fef9c3':'#fee2e2', col=good?'#166534':mid?'#854d0e':'#991b1b';
  const note=good?'moat แข็งแรง':mid?'สร้างมูลค่าเหนือทุนเล็กน้อย':'ทำลายมูลค่า (ROIC ต่ำกว่า WACC — โตแค่ไหนก็ไม่ช่วย)';
  return ` <span style="display:inline-block;font-size:calc(8px*var(--fs));font-weight:700;background:${bg};color:${col};border-radius:4px;padding:1px 5px" title="ROIC−WACC spread ${s>=0?'+':''}${s}pp — ${note}. คุณภาพวัดที่ spread ไม่ใช่ที่การเติบโต (รายละเอียดใน drawer)">Moat ${s>=0?'+':''}${s}pp</span>`;
}
function dispAction(x){
  // S9: ตลาด RISK-OFF — Action ฝั่งซื้อเชื่อไม่ได้ชั่วคราว override เฉพาะการแสดงผล (ค่า server ไม่แตะ)
  const crash=((x.momentum_v2||{}).crash_guard)===true;
  if(crash&&['STRONG BUY','BUY','ACCUMULATE'].includes(x.action))
    return `<span class="tag" style="background:#64748b;border:1px solid #dc2626" title="S9 crash guard: ตลาด RISK-OFF — momentum พังแรงสุดตอนตลาดกลับทิศ · สัญญาณเดิม: ${esc(x.action)}">WAIT &middot; crash guard</span>`;
  return tag(x.action,ACT_COLOR[x.action]);
}
function guardBadges(x){
  const m=x.momentum_v2||{}; let h='';
  if(m.crash_guard===true)
    h+=`<div style="background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:7px;padding:4px 9px;font-size:calc(11px*var(--fs));margin:4px 0">&#9888; <b>S9 crash guard:</b> ตลาด RISK-OFF (S&amp;P ใต้ 200-DMA) — สัญญาณ momentum เชื่อไม่ได้ชั่วคราว ห้ามไล่ราคา</div>`;
  const rv=m.reversal||{};
  if(rv.risk===true)
    h+=`<div style="background:#fefce8;border:1px solid #fde68a;color:#854d0e;border-radius:7px;padding:4px 9px;font-size:calc(11px*var(--fs));margin:4px 0">&#9888; <b>S9 reversal:</b> วิ่งสะสม ~3 ปี ${momPct(rv.cum_return)} และอยู่ใกล้ยอดของช่วง — โซนกลับตัวระยะยาว อย่าไล่ราคา</div>`;
  return h;
}
// ---- Tier3: asset-class card (S3 bonds / S40-41 pricing assets) — mirror ของ config.ASSET_CLASS_MAP
const ASSET_CLASS={"TLT":"bond","IEF":"bond","SHY":"bond","AGG":"bond","BND":"bond","LQD":"bond","TIP":"bond","GOVT":"bond","GLD":"gold","IAU":"gold","BTC-USD":"crypto","ETH-USD":"crypto","IBIT":"crypto"};
function pricingBadge(ac){
  if(ac==='bond')return `<span style="display:inline-block;font-size:calc(9px*var(--fs));font-weight:700;background:#e0f2fe;color:#075985;border-radius:4px;padding:1px 6px" title="Damodaran S3: ตราสารหนี้ — ความเสี่ยงหลักคืออัตราดอกเบี้ย (duration) ไม่ใช้ FV แบบหุ้น">BOND &middot; rate risk (S3)</span>`;
  return `<span style="display:inline-block;font-size:calc(9px*var(--fs));font-weight:700;background:#f3e8ff;color:#6b21a8;border-radius:4px;padding:1px 6px" title="Damodaran S40-41: ไม่มีกระแสเงินสด → ตีมูลค่า (valuation) ไม่ได้ ได้แต่ตีราคา (pricing) — จำกัดน้ำหนัก อย่าปนกับส่วน value">PRICING ASSET (S40-41)</span>`;
}
