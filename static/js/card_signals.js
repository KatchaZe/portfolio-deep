// Chips and cells on a holding card: momentum, rank, earnings dots, quality badges, guards.

// a small rounded label in a theme tone; `title` must already be attribute-safe
function chip(text,tok,title){
  return `<span class="tag" style="${tone(tok)}"${title?` title="${title}"`:''}>${text}</span>`;
}

function garpBadge(x){
  if(x.garp_score==null)return '';
  if(x.garp_candidate)return chip(`GARP ${x.garp_score}`,'--bull',`GARP candidate (Damodaran S13/S19): cheap (Price) AND quality (Economics) both ≥3. Score ${x.garp_score}/10`);
  return `<span class="note" title="GARP score (S13/S19) = Economics + Price subscores, /10; candidate needs both ≥3">G ${x.garp_score}</span>`;
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
    +`<div class="note">6M ${ratioPct(m.roc_6m)}${rank}</div></td>`;
}
function momTooltip(m,s,sec){
  const c=m.components||{}, ck=k=>c[k]===true?'✓':c[k]===false?'✗':'·';
  const q=m.quality||{}, dropped=(q.dropped_nonpos||0)+(q.dropped_spikes||0);
  const warn=s.divWarn?` ⚠ split-only price (${m.src||'stooq'}) — 12M may understate dividends`
                      :(s.splitOnly?` · split-only price (${m.src||'stooq'}), non-payer — accurate`:'');
  const cleaned=dropped?` · cleaned ${dropped} bad bar(s)`:'';
  const staleTxt=s.stale?` · STALE ${m.age_days}d old — run Daily`:'';
  const crashTxt=s.crash?' · ⚠ S9 crash guard: market risk-off, momentum crash-prone':'';
  const revTxt=s.rev?` · ↻ S9 reversal risk (3y ${ratioPct(m.reversal.cum_return)}, near top)`:'';
  return (`Composite ${m.mom_label} (${m.mom_score}/${m.mom_n} positive)`
    +` · 12-1 ${ratioPct(m.mom_12_1)} · 6M ${ratioPct(m.roc_6m)} · 12M ${ratioPct(m.roc_12m)}`
    +` · ${m.above_sma200===true?'above':m.above_sma200===false?'below':'?'} SMA200 · RSI ${fixed(m.rsi,0)}`
    +` · vs SPY ${m.excess_12_1!=null?signed(Math.round(m.excess_12_1*100))+'%':'?'}`
    +`  ||  12-1>0 ${ck('mom_12_1_pos')}  6M>0 ${ck('roc_6m_pos')}  >SMA200 ${ck('above_sma200')}  beats SPY ${ck('beats_mkt_12_1')}`
    +` · source ${m.src||'?'} · ${q.bars||'?'} bars · as of ${m.as_of||'?'}${warn}${cleaned}${staleTxt}${crashTxt}${revTxt}  ||  secondary: ${sec}`).replace(/"/g,'&quot;');
}
function momMarks(s){
  return (s.divWarn?` <span class="warnc" title="split-only price, dividend payer — 12M may understate">&#9888;</span>`:'')
    +(s.stale?` ${chip('stale','--warn','stale momentum — run Daily')}`:'')
    +(s.crash?` <span class="down" title="S9 crash guard: market risk-off (S&P below 200-DMA) — momentum crash-prone">&#9888;</span>`:'')
    +(s.rev?` <span style="color:color-mix(in srgb,var(--violet) 80%,var(--ink))" title="S9 long-horizon reversal: ~3y run extreme and near top — reversal-prone">&#8635;</span>`:'');
}
// cross-sectional momentum rank (Top / Mid / Bottom by vol-adjusted 12-1)
function rankCell(x){
  const m=x.momentum_v2;
  if(!m||!m.mom_bucket){return `<td>${MUTED_DASH}</td>`;}
  const pctile=(m.mom_rank_pct!=null)?Math.round(m.mom_rank_pct*100):null;
  const xs=m.excess_12_1!=null?` · vs SPY ${signed(Math.round(m.excess_12_1*100))}%`:'';
  const sub=pctile!=null?`<div class="note">${pctile} %ile${xs}</div>`:'';
  return `<td title="Rank by 12-1 ÷ volatility vs the other names on this tab${pctile!=null?` — ${pctile}th percentile (higher = stronger)`:''}. vs SPY = 12-1 minus SPY over the same dates (same on every tab)">`
    +`${tag(m.mom_bucket,RANK_COLOR[m.mom_bucket])}${sub}</td>`;
}
function flagIcon(x){const fl=x.flags||[];if(!fl.length)return '';return ` <span class="tag" style="${tone('--warn')};cursor:help" title="${esc(fl.map(pp2pct).join('\n'))}">&#9873; ${fl.length}</span>`}
// a ticker the last refresh skipped (FMP quota): say so on the card, not only in a toast
function skippedBadge(x){if(!x.skipped)return '';return ` ${chip('ข้อมูลรอบก่อน','--warn',esc((x.flags||[])[0]||''))}`}

const EARN_TOK={beat:'--bull',meet:'--base',miss:'--bear',expand:'--bull',flat:'--base',contract:'--bear'};
const EARN_GLYPH={beat:'B',meet:'M',miss:'X',expand:'+',flat:'=',contract:'−'};
function earnDots(list,kind){
  return `<span class="dots">${(list||[]).map(e=>{
    const tip=kind==='mgn'?marginDotTip(e):surpriseDotTip(e,kind);
    return `<i class="ecirc" style="background:var(${EARN_TOK[e.grade]||'--muted'})" title="${esc(tip)}">${EARN_GLYPH[e.grade]||'?'}</i>`;
  }).join('')}</span>`;
}
function marginDotTip(e){
  const m=e.op_margin==null?'—':(e.op_margin*100).toFixed(1)+'%';
  const my=e.op_margin_yoy==null?'—':(e.op_margin_yoy*100).toFixed(1)+'%';
  const dp=e.delta_pp==null?'n/a':signed(e.delta_pp)+'%';
  return `${e.quarter||''} · ${(e.grade||'').toUpperCase()} ${dp} YoY\nOp margin ${m} vs yr-ago ${my}`;
}
function surpriseDotTip(e,kind){
  const sp=e.surprise_pct==null?'n/a':signed(e.surprise_pct)+'%';
  const act=kind==='eps'?(e.eps_actual??'—'):billions(e.rev_actual);
  const est=kind==='eps'?(e.eps_estimate??'—'):billions(e.rev_estimate);
  return `${e.quarter||''} · ${(e.grade||'').toUpperCase()} ${sp}\n${kind.toUpperCase()} act ${act} vs est ${est}`;
}
// Says why an earnings row is empty instead of leaving it blank:
//   refresh -> old data schema, a Fundamental Refresh will fill it
//   n/a     -> primary AND fallback sources had no data
function earnMiss(st,k){
  const s=(st||{})[k]||{};
  const stale=s.reason==='stale-facts';
  const tip=s.detail||'ไม่มีข้อมูล — แหล่งหลักและสำรองไม่ตอบ';
  return `<span title="${esc(tip)}" class="${stale?'warnc':'muted'}" style="font-size:var(--t-xs);cursor:help">${stale?'&#10227; refresh':'n/a'}</span>`;
}
function earnRow(label,title,body){return `<div class="el"><span class="cap" title="${title}">${label}</span>${body}</div>`;}
// the card's three status rows: last four quarters as dots, then the latest result in words
function earnLine(label,list,kind,st){
  const last=list&&list.length?list[list.length-1]:null;
  const val=!last?'':kind==='mgn'?(last.delta_pp!=null?sgn(last.delta_pp,1):''):(last.surprise_pct!=null?sgn(last.surprise_pct,1):'');
  const grade=last?`<b style="color:color-mix(in srgb,var(${EARN_TOK[last.grade]||'--muted'}) 72%,var(--ink))">${esc(last.grade||'')}</b> <span class="num">${val}</span> <span class="note">${esc(qlab(last.quarter))}</span>`:'';
  return `<div class="el"><span class="cap">${label}</span>${list&&list.length?earnDots(list.slice(-4),kind):earnMiss(st,kind)}<span class="elv">${grade}</span></div>`;
}
// '2026-06-30' -> "Q2'26"; anything else is shown as given
function qlab(q){const m=String(q||'').match(/^(\d{4})-(\d{2})/);return m?`Q${Math.floor((+m[2]-1)/3)+1}'${m[1].slice(2)}`:(q||'');}
function earnCircles(x){
  const eps=x.earnings_surprises||[], rev=x.rev_surprises||[], mgn=x.margin_trend||[];
  const st=x.earn_status;
  if(!eps.length && !rev.length && !mgn.length && !st)return MUTED_DASH;
  return `<div class="earn3" aria-label="ผลงบเทียบ consensus ไตรมาสล่าสุด">${earnLine('EPS',eps,'eps',st)}${earnLine('Rev',rev,'rev',st)}${earnLine('Mgn',mgn,'mgn',st)}</div>`;
}
// post-earnings drift, spelled out so it reads without hovering
function peadRow(pd){
  if(!pd.bias||pd.bias==='neutral')return '';
  const up=pd.bias==='up', strong=pd.strength==='strong';
  const q=pd.quarter?` &middot; ${esc(pd.quarter)}`:'';
  return earnRow('PEAD','Damodaran S25 (PEAD): ราคามักไหลตามทิศ earnings surprise ล่าสุดต่ออีกหลายสัปดาห์ — ใช้ประกอบจังหวะ ไม่ใช่เหตุผลหลัก',
    chip(`drift ${up?'&#8593;':'&#8595;'} ${strong?'strong':'mild'}${q}`,up?'--bull':'--bear',esc(pd.note||'')));
}
function eqBadge(x){
  if(!x.eq_verdict)return '';
  const m={CLEAN:'--bull',REVIEW:'--warn',LOW:'--bear'};
  return chip('EQ '+esc(x.eq_verdict),m[x.eq_verdict]||'--muted','Earnings quality (cash conversion / accruals / SBC)');
}
// the backend's value -> quality -> timing synthesis
function adviceLine(x){
  if(!x.advice)return '';
  return `<div class="advice" title="สังเคราะห์จากทุกค่าบน card ตามลำดับ Damodaran: มูลค่า → คุณภาพ → จังหวะ">${esc(pp2pct(x.advice))}</div>`;
}
// only the Action clause of the advice, for the collapsed card
function adviceAction(x){
  const a=pp2pct(x.advice||''); if(!a)return '';
  const i=a.lastIndexOf('Action:');
  return `<div class="advice" title="${esc(a)}">${esc(i>=0?a.slice(i+7).trim():a.split('|').pop().trim())}</div>`;
}
function moatTok(s){return s>=10?'--bull':s>=0?'--base':'--bear';}
function moatChip(x){
  const s=(x.key_metrics||{}).spread_pct;
  if(s==null)return '';
  const note=s>=10?'moat แข็งแรง':s>=0?'สร้างมูลค่าเหนือทุนเล็กน้อย':'ทำลายมูลค่า (ROIC ต่ำกว่า WACC — โตแค่ไหนก็ไม่ช่วย)';
  return ' '+chip(`Moat ${signed(s)}%`,moatTok(s),`ROIC−WACC spread ${signed(s)}% — ${note}. คุณภาพวัดที่ spread ไม่ใช่ที่การเติบโต (ดูแท็บ DEEP & ทุน)`);
}
// The server decides the Action once (domain/advice.py), including the S9 crash
// guard's WAIT, so the cell and the advice line always agree.
function dispAction(x){
  if(!x.action)return MUTED_DASH;
  const crash=((x.momentum_v2||{}).crash_guard)===true;
  const t=x.action==='WAIT'&&crash?'S9 crash guard: ตลาด RISK-OFF — พักการเข้าซื้อ':'ดูเหตุผลในบรรทัด advice';
  return `<span title="${t}">${tag(esc(x.action),ACT_COLOR[x.action])}</span>`;
}
function guardBadges(x){
  const m=x.momentum_v2||{}; let h='';
  if(m.crash_guard===true)
    h+=`<div class="guard tone t-bear">&#9888; <b>S9 crash guard:</b> ตลาด RISK-OFF (S&amp;P ใต้ 200-DMA) — สัญญาณ momentum เชื่อไม่ได้ชั่วคราว ห้ามไล่ราคา</div>`;
  const rv=m.reversal||{};
  if(rv.risk===true)
    h+=`<div class="guard tone t-warn">&#9888; <b>S9 reversal:</b> วิ่งสะสม ~3 ปี ${ratioPct(rv.cum_return)} และอยู่ใกล้ยอดของช่วง — โซนกลับตัวระยะยาว อย่าไล่ราคา</div>`;
  return h;
}
// S3 bonds / S40-41 pricing assets. Must mirror config.ASSET_CLASS_MAP.
const ASSET_CLASS={"TLT":"bond","IEF":"bond","SHY":"bond","AGG":"bond","BND":"bond","LQD":"bond","TIP":"bond","GOVT":"bond","GLD":"gold","IAU":"gold","BTC-USD":"crypto","ETH-USD":"crypto","IBIT":"crypto"};
function pricingBadge(ac){
  if(ac==='bond')return chip('BOND &middot; rate risk (S3)','--info','Damodaran S3: ตราสารหนี้ — ความเสี่ยงหลักคืออัตราดอกเบี้ย (duration) ไม่ใช้ FV แบบหุ้น');
  return chip('PRICING ASSET (S40-41)','--violet','Damodaran S40-41: ไม่มีกระแสเงินสด → ตีมูลค่า (valuation) ไม่ได้ ได้แต่ตีราคา (pricing) — จำกัดน้ำหนัก อย่าปนกับส่วน value');
}
