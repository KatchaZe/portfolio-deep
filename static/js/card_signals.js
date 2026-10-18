// Chips and cells on a holding card: momentum, rank, earnings dots, quality badges, guards.

// a small rounded label; `title` must already be attribute-safe
function chip(text,bg,col,title,size=9,pad=6){
  return `<span style="display:inline-block;font-size:calc(${size}px*var(--fs));font-weight:700;background:${bg};color:${col};border-radius:4px;padding:1px ${pad}px" title="${title}">${text}</span>`;
}

function garpBadge(x){
  if(x.garp_score==null)return '';
  if(x.garp_candidate)return ` <span class="tag" style="background:#0d9488;font-size:calc(8px*var(--fs))" title="GARP candidate (Damodaran S13/S19): cheap (Price) AND quality (Economics) both ≥3. Score ${x.garp_score}/10">GARP ${x.garp_score}</span>`;
  return ` <span class="muted" style="font-size:calc(9px*var(--fs))" title="GARP score (S13/S19) = Economics + Price subscores, /10; candidate needs both ≥3">G ${x.garp_score}</span>`;
}

// The composite (momentum_v2) is the signal; RSI / MACD / DBBMV only appear in the tooltip.
function momCell(x){
  const m=x.momentum_v2;
  const sec=`RSI ${x.rsi_signal||'—'} (${fixed(x.rsi,0)}) · MACD ${x.macd_signal||'—'} · DBBMV ${x.dbbmv_signal||'—'}`;
  if(!m||!m.mom_label){
    return `<td title="No momentum yet — run Daily (price/momentum). Secondary: ${sec}">${MUTED_DASH}</td>`;
  }
  const splitOnly=m.dividend_adjusted===false;
  // only a dividend payer is hurt by a split-only price series
  const divWarn=(m.div_warn!==undefined)?m.div_warn:splitOnly;
  const s={divWarn,splitOnly,stale:m.stale===true,crash:m.crash_guard===true,rev:!!(m.reversal&&m.reversal.risk===true)};
  const rank=m.mom_bucket?` · rank ${m.mom_bucket}`:'';
  return `<td title="${momTooltip(m,s,sec)}">${tag(m.mom_label,MOMV2_COLOR[m.mom_label])}${momMarks(s)}`
    +`<div class="muted" style="font-size:calc(9px*var(--fs))">6M ${ratioPct(m.roc_6m)}${rank} &#9432;</div></td>`;
}
function momTooltip(m,s,sec){
  const c=m.components||{}, ck=k=>c[k]===true?'✓':c[k]===false?'✗':'·';
  const q=m.quality||{}, dropped=(q.dropped_nonpos||0)+(q.dropped_spikes||0);
  const warn=s.divWarn?` ⚠ split-only price (${m.src||'stooq'}) — 12M may understate dividends`
                      :(s.splitOnly?` · split-only price (${m.src||'stooq'}), non-payer — accurate`:'');
  const cleaned=dropped?` · cleaned ${dropped} bad bar(s)`:'';
  const staleTxt=s.stale?` · ⏳ STALE ${m.age_days}d old — run Daily`:'';
  const crashTxt=s.crash?' · ⚠ S9 crash guard: market risk-off, momentum crash-prone':'';
  const revTxt=s.rev?` · ↻ S9 reversal risk (3y ${ratioPct(m.reversal.cum_return)}, near top)`:'';
  return (`Composite ${m.mom_label} (${m.mom_score}/${m.mom_n} positive)`
    +` · 12-1 ${ratioPct(m.mom_12_1)} · 6M ${ratioPct(m.roc_6m)} · 12M ${ratioPct(m.roc_12m)}`
    +` · ${m.above_sma200===true?'above':m.above_sma200===false?'below':'?'} SMA200 · RSI ${fixed(m.rsi,0)}`
    +` · vs SPY ${m.excess_12_1!=null?signed(Math.round(m.excess_12_1*100))+'pp':'?'}`
    +`  ||  12-1>0 ${ck('mom_12_1_pos')}  6M>0 ${ck('roc_6m_pos')}  >SMA200 ${ck('above_sma200')}  beats SPY ${ck('beats_mkt_12_1')}`
    +` · source ${m.src||'?'} · ${q.bars||'?'} bars · as of ${m.as_of||'?'}${warn}${cleaned}${staleTxt}${crashTxt}${revTxt}  ||  secondary: ${sec}`).replace(/"/g,'&quot;');
}
function momMarks(s){
  return (s.divWarn?'<span style="color:#d97706" title="split-only price, dividend payer — 12M may understate">&#9888;</span>':'')
    +(s.stale?'<span style="color:#b45309" title="stale momentum — run Daily">&#9203;</span>':'')
    +(s.crash?'<span style="color:#dc2626" title="S9 crash guard: market risk-off (S&P below 200-DMA) — momentum crash-prone">&#9888;</span>':'')
    +(s.rev?'<span style="color:#7c3aed" title="S9 long-horizon reversal: ~3y run extreme and near top — reversal-prone">&#8635;</span>':'');
}
// cross-sectional momentum rank (Top / Mid / Bottom by vol-adjusted 12-1)
function rankCell(x){
  const m=x.momentum_v2;
  if(!m||!m.mom_bucket){return `<td>${MUTED_DASH}</td>`;}
  const pctile=(m.mom_rank_pct!=null)?Math.round(m.mom_rank_pct*100):null;
  const xs=m.excess_12_1!=null?` · vs SPY ${signed(Math.round(m.excess_12_1*100))}pp`:'';
  const sub=pctile!=null?`<div class="muted" style="font-size:calc(9px*var(--fs))">${pctile} %ile${xs}</div>`:'';
  return `<td title="Rank by 12-1 ÷ volatility vs the other names on this tab${pctile!=null?` — ${pctile}th percentile (higher = stronger)`:''}. vs SPY = 12-1 minus SPY over the same dates (same on every tab)">`
    +`${tag(m.mom_bucket,RANK_COLOR[m.mom_bucket])}${sub}</td>`;
}
function flagIcon(x){const fl=x.flags||[];if(!fl.length)return '';return ` <span title="${esc(fl.join('\n'))}" style="cursor:help;color:#d97706;font-size:calc(11px*var(--fs));font-weight:700">⚑${fl.length}</span>`}

const EARN_COLOR={beat:'#16a34a',meet:'#f59e0b',miss:'#dc2626',
                  expand:'#16a34a',flat:'#f59e0b',contract:'#dc2626'};
function earnDots(list,kind){
  return (list||[]).map(e=>{
    const c=EARN_COLOR[e.grade]||'#cbd5e1';
    const tip=kind==='mgn'?marginDotTip(e):surpriseDotTip(e,kind);
    return `<span class="ecirc" style="background:${c}" title="${esc(tip)}"></span>`;
  }).join('');
}
function marginDotTip(e){
  const m=e.op_margin==null?'—':(e.op_margin*100).toFixed(1)+'%';
  const my=e.op_margin_yoy==null?'—':(e.op_margin_yoy*100).toFixed(1)+'%';
  const dp=e.delta_pp==null?'n/a':signed(e.delta_pp)+'pp';
  return `${e.quarter||''} · ${(e.grade||'').toUpperCase()} ${dp} YoY\nOp margin ${m} vs yr-ago ${my}`;
}
function surpriseDotTip(e,kind){
  const sp=e.surprise_pct==null?'n/a':signed(e.surprise_pct)+'%';
  const act=kind==='eps'?(e.eps_actual??'—'):billions(e.rev_actual);
  const est=kind==='eps'?(e.eps_estimate??'—'):billions(e.rev_estimate);
  return `${e.quarter||''} · ${(e.grade||'').toUpperCase()} ${sp}\n${kind.toUpperCase()} act ${act} vs est ${est}`;
}
// Says why an earnings row is empty instead of leaving it blank:
//   ⟳ stale-facts -> old data schema, a Fundamental Refresh will fill it
//   × unavailable -> primary AND fallback sources had no data
function earnMiss(st,k){
  const s=(st||{})[k]||{};
  const stale=s.reason==='stale-facts';
  const tip=s.detail||'ไม่มีข้อมูล — แหล่งหลักและสำรองไม่ตอบ';
  return `<span title="${esc(tip)}" style="font-size:calc(8.5px*var(--fs));color:${stale?'#b45309':'#94a3b8'};cursor:help">${stale?'&#10227; refresh':'&times; n/a'}</span>`;
}
function earnRow(label,title,body){return `<div class="erow"><span class="elbl" title="${title}">${label}</span>${body}</div>`;}
function earnCircles(x){
  const eps=x.earnings_surprises||[], rev=x.rev_surprises||[], mgn=x.margin_trend||[];
  const st=x.earn_status;
  if(!eps.length && !rev.length && !mgn.length && !st)return MUTED_DASH;
  const dots=(list,kind)=>list.length?earnDots(list,kind):earnMiss(st,kind);
  return earnRow('EPS','EPS beat/miss vs analyst estimate — hover จุดสีเพื่อดู Act vs Est',dots(eps,'eps'))
    +earnRow('Rev','Revenue beat/miss vs analyst estimate — hover จุดสีเพื่อดู Act vs Est',dots(rev,'rev'))
    +earnRow('Mgn','Operating-margin YoY trend: expand / flat / contract',dots(mgn,'mgn'))
    +peadRow(x.pead||{})
    +`<div class="muted" style="font-size:calc(8px*var(--fs))">old→new</div>`;
}
// post-earnings drift, spelled out so it reads without hovering
function peadRow(pd){
  if(!pd.bias||pd.bias==='neutral')return '';
  const up=pd.bias==='up', strong=pd.strength==='strong';
  const q=pd.quarter?` &middot; ${esc(pd.quarter)}`:'';
  return earnRow('PEAD','Damodaran S25 (PEAD): ราคามักไหลตามทิศ earnings surprise ล่าสุดต่ออีกหลายสัปดาห์ — ใช้ประกอบจังหวะ ไม่ใช่เหตุผลหลัก',
    chip(`drift ${up?'&#8593;':'&#8595;'} ${strong?'strong':'mild'}${q}`,up?'#dcfce7':'#fee2e2',up?'#166534':'#991b1b',esc(pd.note||'')));
}
function eqBadge(x){
  if(!x.eq_verdict)return '';
  const m={CLEAN:['#dcfce7','#166534'],REVIEW:['#fef9c3','#854d0e'],LOW:['#fee2e2','#991b1b']};
  const c=m[x.eq_verdict]||['#e2e8f0','#475569'];
  return `<div title="Earnings quality (cash conversion / accruals / SBC)" style="font-size:calc(8px*var(--fs));font-weight:700;background:${c[0]};color:${c[1]};border-radius:4px;padding:1px 4px;display:inline-block;margin-top:2px">EQ ${x.eq_verdict}</div>`;
}
// the backend's value -> quality -> timing synthesis
function adviceLine(x){
  if(!x.advice)return '';
  return `<div style="font-size:calc(11px*var(--fs));color:#334155;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:5px 8px;margin-top:5px;white-space:normal;text-align:left" title="สังเคราะห์จากทุกค่าบน card ตามลำดับ Damodaran: มูลค่า → คุณภาพ → จังหวะ">&#128161; ${esc(x.advice)}</div>`;
}
function moatChip(x){
  const s=(x.key_metrics||{}).spread_pct;
  if(s==null)return '';
  const good=s>=10, mid=(s>=0&&s<10);
  const bg=good?'#dcfce7':mid?'#fef9c3':'#fee2e2', col=good?'#166534':mid?'#854d0e':'#991b1b';
  const note=good?'moat แข็งแรง':mid?'สร้างมูลค่าเหนือทุนเล็กน้อย':'ทำลายมูลค่า (ROIC ต่ำกว่า WACC — โตแค่ไหนก็ไม่ช่วย)';
  return ' '+chip(`Moat ${signed(s)}pp`,bg,col,`ROIC−WACC spread ${signed(s)}pp — ${note}. คุณภาพวัดที่ spread ไม่ใช่ที่การเติบโต (รายละเอียดใน drawer)`,8,5);
}
// The server decides the Action once (domain/advice.py), including the S9 crash
// guard's WAIT, so the cell and the advice line always agree.
function dispAction(x){
  if(!x.action)return MUTED_DASH;
  const crash=((x.momentum_v2||{}).crash_guard)===true;
  const t=x.action==='WAIT'&&crash?'S9 crash guard: ตลาด RISK-OFF — พักการเข้าซื้อ':'ดูเหตุผลในบรรทัด 💡 advice';
  return `<span title="${t}">${tag(esc(x.action),ACT_COLOR[x.action])}</span>`;
}
function guardBadges(x){
  const m=x.momentum_v2||{}; let h='';
  if(m.crash_guard===true)
    h+=`<div style="background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:7px;padding:4px 9px;font-size:calc(11px*var(--fs));margin:4px 0">&#9888; <b>S9 crash guard:</b> ตลาด RISK-OFF (S&amp;P ใต้ 200-DMA) — สัญญาณ momentum เชื่อไม่ได้ชั่วคราว ห้ามไล่ราคา</div>`;
  const rv=m.reversal||{};
  if(rv.risk===true)
    h+=`<div style="background:#fefce8;border:1px solid #fde68a;color:#854d0e;border-radius:7px;padding:4px 9px;font-size:calc(11px*var(--fs));margin:4px 0">&#9888; <b>S9 reversal:</b> วิ่งสะสม ~3 ปี ${ratioPct(rv.cum_return)} และอยู่ใกล้ยอดของช่วง — โซนกลับตัวระยะยาว อย่าไล่ราคา</div>`;
  return h;
}
// S3 bonds / S40-41 pricing assets. Must mirror config.ASSET_CLASS_MAP.
const ASSET_CLASS={"TLT":"bond","IEF":"bond","SHY":"bond","AGG":"bond","BND":"bond","LQD":"bond","TIP":"bond","GOVT":"bond","GLD":"gold","IAU":"gold","BTC-USD":"crypto","ETH-USD":"crypto","IBIT":"crypto"};
function pricingBadge(ac){
  if(ac==='bond')return chip('BOND &middot; rate risk (S3)','#e0f2fe','#075985','Damodaran S3: ตราสารหนี้ — ความเสี่ยงหลักคืออัตราดอกเบี้ย (duration) ไม่ใช้ FV แบบหุ้น');
  return chip('PRICING ASSET (S40-41)','#f3e8ff','#6b21a8','Damodaran S40-41: ไม่มีกระแสเงินสด → ตีมูลค่า (valuation) ไม่ได้ ได้แต่ตีราคา (pricing) — จำกัดน้ำหนัก อย่าปนกับส่วน value');
}
