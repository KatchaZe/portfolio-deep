// The Risk Desk on the Allocation tab: capital weight against risk weight, concentration,
// stress, downside, suitability and the rebalance plan, led by a five-line story.
function lossTok(v){return v<=-15?'--bear':v<0?'--warn':'--bull';}
function scoreTok(s){return s>=60?'--bull':s>=40?'--warn':'--bear';}
const tokText=t=>`color:color-mix(in srgb,var(${t}) 78%,var(--ink))`;

// one /api/risk payload serves the Risk Desk and the Correlation tab, keyed by its inputs
let __riskCache=null;
async function fetchRiskPayload(en,force){
  const tol=(document.getElementById('riskTol')||{}).value||20, hz=(document.getElementById('riskHorizon')||{}).value||1;
  // the holdings are part of the key: adding, removing or resizing a position (any
  // path, including the watchlist promote) must not serve the old portfolio's desk
  const hold=((window.__last||{}).rows||[]).map(r=>r.ticker+':'+(r.shares??'')).sort().join(',')
    +'|cash:'+(((window.__last||{}).totals||{}).cash??'');
  const key=`${tol}|${hz}|${en}|${hold}`;
  if(!force&&__riskCache&&__riskCache.key===key)return __riskCache.data;
  const r=await fetch(`/api/risk?risk_tolerance_pct=${encodeURIComponent(tol)}&horizon_years=${encodeURIComponent(hz)}&enrich=${en}`);
  const txt=await r.text(); let d;
  try{d=JSON.parse(txt);}catch(_){d={status:'error',message:'Server '+r.status+': '+txt.slice(0,160)};}
  if(d&&d.status!=='error')__riskCache={key,data:d};
  return d;
}
async function loadRisk(force){
  const body=document.getElementById('riskBody'); if(!body)return;
  const sp=document.getElementById('spRisk'); if(sp)sp.style.display='inline-block';
  const en=document.getElementById('riskEnrich').value||'auto';
  body.innerHTML='<div class="empty">Computing risk… (price history + covariance + stress)</div>';
  try{
    const d=await fetchRiskPayload(en,!!force);
    if(d.quota)renderQuota(d.quota); renderRisk(d);
  }catch(e){body.innerHTML='<div class="banner tone t-bear">Error: '+esc(String(e))+'</div>';}
  finally{if(sp)sp.style.display='none';}
}
// historical and hypothetical stress scenarios, biggest loss first
function scenariosWorstFirst(st){return [...(st.historical||[]),...(st.hypothetical||[])].sort((a,b)=>a.loss_pct-b.loss_pct);}
function worstScenario(st){return scenariosWorstFirst(st)[0];}
// per-holding capital vs risk share: covariance-based when the backend has it, else beta-based
function capRiskRows(cv){
  const cov=cv.covariance_based||[];
  if(cov.length && cov[0].signed_risk_pct!=null) return cov.map(x=>({ticker:x.ticker,cap:x.capital_pct,risk:x.signed_risk_pct,diff:x.diff_pp,abs:x.abs_risk_share_pct}));
  return (cv.beta_based||[]).map(x=>({ticker:x.ticker,cap:x.capital_pct,risk:x.risk_pct,diff:x.diff_pp,abs:x.risk_pct}));
}
// S4 portfolio margin of safety on the card's own definition (V − P)/V, applied to the
// WHOLE book: total fair value of the holdings that have one against what they cost at
// today's price. Averaging each name's ratio let one tiny fair value swamp the book
// (TMDX at −920% put the portfolio at −190.7%). Net upside is a return on price, so its
// market-value-weighted average is the portfolio's.
function portMoS(){
  const rows=(window.__last&&window.__last.rows)||[];
  let P=0,V=0,n=0,wn=0,nets=0;
  for(const r of rows){
    const mv=r.market_value,u=mosOf(r);
    if(mv&&u!=null&&u<100){P+=mv;V+=mv*100/(100-u);n++;if(r.net_upside_pct!=null){nets+=mv*r.net_upside_pct;wn+=mv;}}
  }
  if(!V||!n)return null;
  return {gross:Math.round((V-P)/V*1000)/10,net:wn?Math.round(nets/wn*10)/10:null,n,of:rows.length};
}
function portMoSCard(){
  const m=portMoS();
  if(!m)return ['Portfolio MoS (S4)','&mdash;','','ต้องมี Fair Value ต่อหุ้นก่อน (Run Fundamental Refresh)'];
  return ['Portfolio MoS (S4)',signed(m.gross)+'%',m.gross>=15?'up':m.gross>=0?'':'down',`${m.net!=null?`net upside หลัง cost/tax ${signed(m.net)}% · `:''}${m.n}/${m.of} ตัวมี FV`,
    `margin of safety ระดับพอร์ต = (Σ fair value − Σ มูลค่าตลาด) / Σ fair value ของ ${m.n}/${m.of} ตัวที่มี FV · net = upside หลังค่าเทรด/ภาษี — ส่วนเผื่อรวมของพอร์ตต่อความผิดพลาดในการประเมิน (S4)`];
}
// Portfolio Story: the seven Risk Desk sections read through Damodaran's five principles,
// headline finding first
function riskStory(d){
  const L=window.__last||{}; const mk=L.market||{},v=mk.valuation||{},b=L.benchmark||{},p=L.philosophy||{};
  const s=d.snapshot||{},cv=d.capital_vs_risk||{},rows=capRiskRows(cv),st=d.stress||{},vr=st.var||{};
  const suit=d.suitability||{},dv=d.diversification||{},reb=d.rebalance||{},dn=d.downside||{},al=d.allocation||{};
  const worst=worstScenario(st);
  const top=rows[0], sleeve=((al.by_sleeve||[])[0])||null, firstAct=(reb.trades||[])[0];
  const finding=suit.within_tolerance===false
    ?`<p style="margin:6px 0 10px;font-size:var(--t-md)"><b>FINDING หลัก:</b> stress drawdown <b class="down num">${pctFixed(suit.binding_drawdown_pct)}</b> เกินขีดรับได้ <b class="num">${pctFixed(-(suit.tolerance_pct||0))}</b>${firstAct?` &rarr; Action แรก: <b>${esc(firstAct.action)} ${esc(firstAct.ticker)} ${pctFixed(firstAct.from_pct)} &rarr; ${pctFixed(firstAct.to_pct)}</b>`:''} <span class="note">(รายละเอียด: ส่วน 6+7)</span></p>`
    :`<p style="margin:6px 0 10px;font-size:var(--t-md)">&#10003; Drawdown ผูกพัน ${pctFixed(suit.binding_drawdown_pct)} ยังอยู่ในขีดรับได้ ${pctFixed(-(suit.tolerance_pct||0))}</p>`;
  const rws=[storyPhilosophy(p,sleeve,top),storyDownside(s,dn,worst,vr),storyBenchmark(b),storyTiming(v,dv),storyCosts(reb)];
  return finding+`<div class="story">${rws.join('')}</div>
    <p class="note" style="margin:6px 0 0">อ่าน 5 บรรทัดนี้ก่อน — ส่วน 1–7 ด้านล่างคือห้องเครื่อง (หลักฐาน/รายละเอียด) ของแต่ละบรรทัด</p>`;
}
function storyRow(t,ref,txt,warn){return `<div${warn?' class="warnrow"':''}><span><b>${t}</b><br><span class="note">${ref}</span></span><span>${txt}</span></div>`;}
function storyPhilosophy(p,sleeve,top){
  const bits=[];
  if(p.running)bits.push(`พอร์ตกำลังเล่น <b>${esc(p.running)}</b>${p.fit_ok?' &#10003; ตรง profile':' &#9888; ไม่ตรง profile'}`);
  const conc=sleeve&&sleeve.value>=40;
  if(conc)bits.push(`เงินกระจุก <b>${esc(sleeve.label)} ${pctFixed(sleeve.value)}</b>`);
  if(top)bits.push(`ตัวขับความเสี่ยงหลัก <b>${esc(top.ticker)}</b> (${pctFixed(top.abs)} ของความเสี่ยงรวม)`);
  return storyRow('&#9312; รู้ว่าเล่นปรัชญาอะไร','S1/S42 · ส่วน 2·4',bits.join(' · ')||'<span class="muted">—</span>',conc);
}
function storyDownside(s,dn,worst,vr){
  const err=(dn.vol_pct==null)&&dn.tag&&String(dn.tag).indexOf('error')>=0;
  const bits=[`Vol <b>${pctFixed(s.port_vol_pct)}</b> แต่ขาลงจริง: ${worst?`<b>${esc(worst.label)} ~${pctFixed(worst.loss_pct)}</b> · `:''}VaR95 <b>${pctFixed(vr.var95_pct)}</b> · CVaR <b>${pctFixed(vr.cvar95_pct)}</b>`];
  if(err)bits.push('<span class="down">5b คำนวณไม่ได้ (returns ว่าง) — ลอง Run risk แบบ data=auto อีกครั้ง</span>');
  return storyRow('&#9313; วัดที่ขาลง ไม่ใช่ vol','S2/S4 · ส่วน 5·5b',bits.join(' · '),err);
}
function storyBenchmark(b){
  const t=b.excess_pp==null?'<span class="muted">ยังไม่มีข้อมูล benchmark — กด Run Daily</span>'
    :`12M: พอร์ต ${signed(b.portfolio_12m)}% vs S&amp;P ${signed(b.market_12m)}% (<b>${signed(b.excess_pp)}%</b>)${b.excess_pp<0?' — อย่าเพิ่ม turnover เพื่อไล่ตาม':''}`;
  return storyRow('&#9314; ถ่อมตัวเทียบ index','S16/S35 · แถบบน',t,false);
}
function storyTiming(v,dv){
  const noBallast=((dv.score&&dv.score.sub&&dv.score.sub.gap_coverage)===0);
  const bits=[v.regime?`ตลาด <b>${esc(v.regime.toUpperCase())}</b> &rarr; ${esc(v.tilt||'')}`:'<span class="muted">market overlay ยังไม่มีข้อมูล</span>'];
  if(noBallast)bits.push(`<b>ไม่มี ballast</b> (bond/gold/cash 0%) · DR crisis ${dv.dr_crisis??'—'} — การกระจายแทบไม่ช่วยตอนวิกฤต`);
  return storyRow('&#9315; Timing = tilt เท่านั้น','S32-34 · S3/S38-41',bits.join(' · '),noBallast);
}
function storyCosts(reb){
  let t;
  if(reb.no_trade)t='NO TRADE — ไม่มีต้นทุน/ภาษีต้องจ่าย พอร์ตอยู่ใน policy band';
  else{
    const c=reb.costs||{}, turn=c.turnover_pct??(reb.trades||[]).reduce((a2,x2)=>a2+Math.abs(x2.amount_pct||0),0)/2;
    t=`แผน rebalance ขยับ ~<b>${Number(turn).toFixed(1)}%</b> ของพอร์ต — ภาษีกำไร <b>${money(c.tax_usd)}</b> + ค่าเทรด ${money(c.trading_usd)} &rarr; <b>ทำเป็นขั้น</b> เริ่มจาก Must ก่อน อย่ารวดเดียว`;
  }
  return storyRow('&#9316; หัก cost/tax ก่อนเชื่อ','S6 · ส่วน 7',t,!reb.no_trade);
}
function renderRisk(d){
  const body=document.getElementById('riskBody');
  if(!d||d.status!=='ok'){body.innerHTML='<div class="empty">'+esc((d&&d.message)||'No risk data — add holdings with price first.')+'</div>';return;}
  const cv=d.capital_vs_risk||{},m=d.meta||{},suit=d.suitability||{};
  body.innerHTML=`<div class="grid12">
    <section class="pnl s12 finding${suit.within_tolerance===false?'':' ok'}" aria-labelledby="rkStory"><h2 id="rkStory">Portfolio Story <span class="note">สรุปตาม 5 หลัก Damodaran · อ่านก่อน 1 นาที</span></h2>${riskStory(d)}</section>
    ${secHTML(1,'Portfolio Snapshot','[CALC]',riskSnapshotHTML(d))}
    ${secHTML(2,'Capital Allocation (by risk driver)','[CALC]',riskAllocationHTML(d),'s6')}
    ${secHTML(3,'Capital Weight vs Risk Weight',esc(m.cov_tag||''),capitalVsRiskHTML(cv),'s6')}
    ${secHTML(4,'Concentration Map','[CALC]',concentrationHTML(d,cv),'s6')}
    ${secHTML(5,'Stress Test','[JUDG-SCENARIO] ภาพประกอบ ไม่ใช่การพยากรณ์',stressHTML(d),'s6')}
    ${secHTML('5b','Downside Risk (S2/S4)','[CALC]',downsideHTML(d),'s8')}
    ${secHTML(6,'Suitability (headline finding)','',suitabilityHTML(d),'s4')}
    ${secHTML(7,'Rebalancing Plan','[CALC]',rebalanceHTML(d,cv))}
    ${secHTML(null,'Bottom line','',panelExplain(d)+panelLegend(m))}</div>`;
}
function riskSnapshotHTML(d){
  const s=d.snapshot,dv=d.diversification||{};
  return kpisHTML([['Value',usd(s.total_value,0)],['Positions',s.n_positions],
    [s.cash_pct?'Est. Vol (ทั้งพอร์ต)':'Est. Vol (annual)',pctFixed(s.port_vol_pct)],
    s.cash_pct?['Cash',pctFixed(s.cash_pct),'',money(s.cash_value)]:null,
    ['Severe drawdown',pctFixed(s.severe_drawdown_pct),s.severe_drawdown_pct<0?'down':''],
    ['Portfolio β (valuation)',s.port_beta],
    ['Diversification',`<span style="${tokText(scoreTok(s.diversification_score))}">${s.diversification_score}/100</span>`,'',esc((dv.score&&dv.score.band)||'')]])
    +`<p class="note" style="margin:8px 0 0">${esc(s.scope||'')} · DR normal <b>${dv.dr_normal??'—'}</b> / crisis <b>${dv.dr_crisis??'—'}</b>${dv.score&&dv.score.tag?' · '+esc(dv.score.tag):''}</p>`;
}
function riskAllocationHTML(d){
  const al=d.allocation||{};
  return `<div class="dgrid"><div class="box"><h3>ตาม risk sleeve</h3>${donutHTML(al.by_sleeve||[],'สัดส่วนตาม risk sleeve')}</div>
    <div class="box"><h3 title="ตามแหล่งรายได้ (revenue mix ชุดเดียวกับที่ valuation ใช้คิด CRP) ไม่ใช่สกุลที่หุ้นซื้อขาย — ADR อย่าง ASML/TSM/NVO ซื้อขายเป็น USD แต่หาเงินเป็น EUR/TWD/DKK (S2 cash-flow risk)">ตามสกุลเงินที่หารายได้</h3>${donutHTML(foldSmall(al.by_currency||[]),'สัดส่วนตามสกุลเงินรายได้')}</div></div>${countryRiskHTML(d)}`;
}
// slices under 1.5% folded into one, so a 19-currency donut stays readable
function foldSmall(rows,min=1.5){
  const big=(rows||[]).filter(r=>r.value>=min), rest=(rows||[]).filter(r=>r.value<min).reduce((a,r)=>a+r.value,0);
  return rest>0?[...big,{label:'อื่น ๆ (<'+min+'%)',value:Math.round(rest*10)/10}]:big;
}
// portfolio country risk premium, weighted by where each holding earns (the CRP its valuation used)
function countryRiskHTML(d){
  const cr=(((d||{}).allocation||{}).country_risk)||null; if(!cr)return '';
  const rows=(cr.rows||[]).filter(r=>(r.crp_pct||0)>0).slice(0,6);
  const miss=cr.weight_without_crp_pct?` · ${pctFixed(cr.weight_without_crp_pct)} ของพอร์ตไม่มี CRP`:'';
  return `<div class="box" style="margin-top:12px"><h3>Country risk ของพอร์ต (CRP) <span class="num">+${plainNum(cr.crp_pct,2)}%</span></h3>
    <p class="note" style="margin:0">Σ น้ำหนัก × CRP ที่ valuation ใช้ (ถ่วงตามรายได้รายประเทศ)${miss}</p>
    ${rows.length?`<div class="tw"><table><thead><tr><th class="l">Ticker</th><th class="n">CRP</th><th class="l">รายได้นอกสหรัฐ</th></tr></thead><tbody>${rows.map(r=>`<tr><th scope="row" class="l num" style="color:var(--ink)">${esc(r.ticker)}</th><td class="n">+${plainNum(r.crp_pct,2)}%</td><td class="l muted" title="${esc(r.basis||'')}">${r.non_us_pct??'—'}%</td></tr>`).join('')}</tbody></table></div>`:''}</div>`;
}
function capitalVsRiskHTML(cv){
  const rows=capRiskRows(cv).slice().sort((a,b)=>(b.abs??b.risk)-(a.abs??a.risk));
  return pairBarsHTML(rows.map(x=>({l:x.ticker,a:x.cap,b:x.risk,tok:x.risk<0?'--bull':'--bear',av:pctFixed(x.cap),bv:pctFixed(x.risk),d:x.diff==null?'—':sgn(x.diff,1),dcls:x.diff>=3?'down':x.diff<=-3?'up':'muted',
      title:`${x.ticker}: เงิน ${pctFixed(x.cap)} → ความเสี่ยง ${pctFixed(x.risk)}`})),'เงินที่ลงเทียบความเสี่ยงที่สร้าง')
    +`<p class="note" style="margin:8px 0 0">แท่งเทา = %เงินที่ลง · แท่งแดง = %ความเสี่ยงที่สร้าง (จาก covariance) · เขียว = ตัวช่วยลดความเสี่ยง · ส่วนต่าง ≥ +3% = กินความเสี่ยงเกินเงิน. ${capRiskHint(cv)}</p>`;
}
function concentrationHTML(d,cv){
  const c=d.concentration||{}, dv=d.diversification||{};
  return kpisHTML([['HHI',c.hhi??'—'],['Effective N',c.eff_n??'—','',c.n?`จาก ${c.n} ตัว`:''],['Top-5',pctFixed(c.top5_pct)],['Top-10',pctFixed(c.top10_pct)],['ENB (bets)',dv.enb??'—','',dv.enb_crisis!=null?`crisis ${dv.enb_crisis}`:''],['Risk balance Nₑ',cv.risk_balance_n??cv.enb_abs??'—']])
    +`<div style="margin-top:12px">${hbarsHTML((((d.allocation||{}).by_sector)||[]).map(x=>({l:x.label,v:x.value})),{label:'น้ำหนักตาม sector'})}</div>
    <p style="font-size:var(--t-sm);margin:8px 0 0">${hiddenConc(d.allocation,cv)}</p>
    <p class="note" style="margin:4px 0 0">Effective N = เชิงเศรษฐกิจถือจริงกี่ตัว · ENB = เดิมพันอิสระหลังคิด correlation (ยิ่งน้อยยิ่งกระจุก)</p>`;
}
function stressHTML(d){
  const st=d.stress||{},vr=st.var||{},sc=scenariosWorstFirst(st);
  return hbarsHTML(sc.map(x=>({l:x.label,v:x.loss_pct,tok:lossTok(x.loss_pct),cls:x.loss_pct<0?'down':'',title:x.worst_contributor?'ตัวที่เจ็บสุด: '+x.worst_contributor:''})),{fmt:v=>pctFixed(v),label:'ขาดทุนตาม scenario'})
    +kpisHTML([['VaR 95%',pctFixed(vr.var95_pct),'down'],['VaR 99%',pctFixed(vr.var99_pct),'down'],['CVaR 95%',pctFixed(vr.cvar95_pct),'down'],
      ...(st.var_hist&&st.var_hist.var95_pct!=null?[['Hist VaR 95%',pctFixed(st.var_hist.var95_pct),'down'],['Hist VaR 99%',pctFixed(st.var_hist.var99_pct),'down'],['Hist CVaR 95%',pctFixed(st.var_hist.cvar95_pct),'down']]:[])])
    +(st.var_hist&&st.var_hist.var95_pct!=null?`<p class="note" style="margin:6px 0 0">Historical VaR (P1-7, S2/S4): จาก returns จริง ${st.var_hist.n} วัน — เห็น fat tail ตามที่เคยเกิด ไม่สมมุติ normal</p>`:'')
    +`<p class="note" style="margin:6px 0 0">${esc(st.tag||'')} · Reverse stress: ${esc((st.reverse&&st.reverse.note)||'—')} · β = ตัวเดียวกับที่ valuation ใช้ (bottom-up; หุ้นระยะเริ่มต้นใช้ regression ที่สูงกว่า) (business risk, S4)</p>`
    +betaTableHTML((d.meta||{}).beta_check);
}
// the beta each stress runs on, beside the measured ones: information, not a warning
function betaTableHTML(bc){
  const ks=Object.keys(bc||{}); if(!ks.length)return '';
  const rows=ks.sort().map(t=>{const b=bc[t];return `<tr><th scope="row" class="l num" style="color:var(--ink)">${esc(t)}</th><td class="n">${plainNum(b.used,2)}</td><td class="n">${plainNum(b.fmp,2)}</td><td class="n">${plainNum(b.calc,2)}</td><td class="n">${plainNum(b.downside,2)}</td><td class="l note">${esc(b.source||'')}</td></tr>`;}).join('');
  return `<details style="margin-top:8px"><summary class="note" style="cursor:pointer">β ที่ stress ใช้ เทียบ β ที่วัดจากราคา (${ks.length} ตัว)</summary>
    <p class="note" style="margin:4px 0">Damodaran S4: regression β ของหุ้นตัวเดียวคลาดเคลื่อนสูง จึงใช้ bottom-up β ของอุตสาหกรรม (re-lever ตาม D/E) · downside β = วัดเฉพาะวันที่ S&amp;P ลง (S2) ใช้ดูว่าขาลงจริงแรงกว่าที่สมมติไหม</p>
    <div class="tw"><table><thead><tr><th class="l">Ticker</th><th class="n">ใช้ (bottom-up)</th><th class="n">FMP</th><th class="n">daily vs SPY</th><th class="n">downside</th><th class="l">ที่มา</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}
// S2/S4: total risk paired with its downside, next to the portfolio margin of safety
function downsideHTML(d){
  const s=d.snapshot, dn=d.downside||{};
  const rr=d.rate_risk||{}, pa=(d.meta&&d.meta.pricing_assets)||[];
  const rateLine=rr.has_bonds?`<p class="note" style="margin:6px 0 0">Bond rate risk (S3): +100bps &rarr; <b>${rr.loss_pct}%</b> on portfolio · ${esc(rr.tag||'')}</p>`:'';
  const pricingLine=pa.length?`<p class="note warnc" style="margin:6px 0 0">&#9888; ${esc(pa.join(', '))} = pricing plays (no cash flow, S40-41) — high uncertainty, cap weight</p>`:'';
  const beta=s.port_beta, dnb=dn.downside_beta;
  const asym=(dnb!=null&&beta!=null&&Number(dnb)>Number(beta));
  const pair=(la,fa,lb,fb)=>`<div class="pairk"><div><span class="cap">${la}</span><b>${fa}</b></div><span aria-hidden="true">&rarr;</span><div><span class="cap">${lb}</span><b class="warnc">${fb}</b></div></div>`;
  return `<div class="dnrow"><div>${pair('Vol',pctFixed(dn.vol_pct),'Downside dev',pctFixed(dn.semidev_pct))+pair('β',beta??'—','Downside β',dnb??'—')}</div>${kpisHTML([['Sortino',dn.sortino??'—'],portMoSCard()])}</div>`
    +(asym?'<p class="note warnc" style="margin:2px 0 0">&#9888; downside &beta; &gt; &beta; — พอร์ตนี้ "ขาลงแรงกว่าขาขึ้น"</p>':'')
    +`<p class="note" style="margin:6px 0 0">${esc(dn.tag||'')} · วัดความเสี่ยง “ขาลง” แยกจากความผันผวนรวม — Sortino สูง=ดี · downside β = แรงตกเทียบ S&amp;P เฉพาะวันตลาดลง (${dn.n||0} วัน)</p>${rateLine}${pricingLine}`;
}
function suitabilityHTML(d){
  const suit=d.suitability||{}, ok=suit.within_tolerance;
  const tol=suit.tolerance_pct||0, dd=Math.abs(suit.binding_drawdown_pct||0), mx=Math.max(dd,tol)*1.1||1;
  return `<div class="suit" role="img" aria-label="drawdown ที่ผูกพัน ${nf(dd,1)}% เทียบขีดรับได้ ${nf(tol,0)}%"><span class="t"><span class="f" style="width:${(dd/mx*100).toFixed(1)}%;background:var(${ok?'--bull':'--bear'})"></span><span class="lim" style="left:${(tol/mx*100).toFixed(1)}%"></span></span></div>
    <div class="scalelab" style="margin-top:4px"><span><i class="lg px"></i>ขีดรับได้ <b>${pctFixed(-tol)}</b></span><span>drawdown ที่ผูกพัน <b class="${ok?'':'down'}">${pctFixed(suit.binding_drawdown_pct)}</b></span></div>
    <p style="margin:10px 0 0;font-size:var(--t-md)"><b class="${ok?'up':'down'}">${ok?'&#10003; อยู่ในขีดที่รับได้':'&#9888; เกินระดับที่รับได้'}</b> — ${esc(suit.verdict||'')}</p>`;
}
function rebalanceHTML(d,cv){
  const reb=d.rebalance||{};
  const blocked=(d.position_sizing||[]).filter(r=>r.add_blocker&&String(r.action).startsWith('HOLD (ไม่เพิ่ม'));
  const rule=reb.rule?`<p class="note" style="margin:8px 0 0">กฎ: ${esc(reb.rule)} (S4/S12 · S35 · S6)${blocked.length?` · ไม่เพิ่ม ${blocked.length} ตัว: `+blocked.map(r=>`<span title="${esc(pp2pct(r.add_blocker))}">${esc(r.ticker)}</span>`).join(', '):''}</p>`:'';
  const ps=(d.position_sizing||[]);
  const sizing=ps.length?`<details style="margin-top:10px"><summary class="note" style="cursor:pointer">Position sizing — เพดานต่อตัว (${ps.length} ตัว)</summary><div class="tw"><table><thead><tr><th class="l">Ticker</th><th class="n">ตอนนี้</th><th class="n">Soft / Hard max</th><th class="n">ส่วนแบ่งความเสี่ยง</th><th class="l">Action</th><th class="l">ติดที่</th></tr></thead><tbody>
    ${ps.map(x=>`<tr><th scope="row" class="l num" style="color:var(--ink)">${esc(x.ticker)}</th><td class="n">${pctFixed(x.current_pct)}</td><td class="n">${pctFixed(x.soft_max_pct,0)} / ${pctFixed(x.hard_max_pct,0)}</td><td class="n">${pctFixed(x.abs_risk_share_pct)}</td><td class="l">${tag(esc(String(x.action||'').split(' (')[0]),ACT_COLOR[String(x.action||'').split(' ')[0]])}</td><td class="l note">${esc(pp2pct(x.add_blocker||x.binding_cap||'–'))}</td></tr>`).join('')}</tbody></table></div></details>`:'';
  if(reb.no_trade)return `<div class="banner tone t-bull" style="font-weight:700">NO TRADE — พอร์ตยังอยู่ใน policy band</div>${rule}${sizing}`;
  const af=reb.after||{}, c=reb.costs||{};
  return kpisHTML([['Vol now',pctFixed(reb.vol_now_pct??cv.port_vol_pct)],['Vol after',pctFixed(af.port_vol_pct),'up'],['Cash after',pctFixed(reb.cash_pct_after)],['ภาษี + ค่าเทรด',money(c.total_usd)],
      ['Top risk now',(capRiskRows(cv)[0]||{}).ticker||'—'],['Top risk after',af.top_risk||'—'],['Stress worst after',pctFixed(af.stress_worst_pct),af.stress_worst_pct<0?'down':'']])
    +`<div class="tw" tabindex="0" role="region" aria-label="รายการ rebalance" style="margin-top:10px"><table><thead><tr><th class="l">Ticker</th><th class="l">Action</th><th class="n">From</th><th class="n">To</th><th class="n">Δ</th><th class="n">$</th><th class="n" title="กำไรที่ realize จาก avg cost">กำไร realize</th><th class="n">ภาษี</th><th class="n">ค่าเทรด</th><th class="l">Priority</th></tr></thead><tbody>
      ${(reb.trades||[]).map(t=>`<tr><th scope="row" class="l num" style="color:var(--ink)">${esc(t.ticker)}</th><td class="l">${tag(esc(t.action),ACT_COLOR[t.action])}</td><td class="n">${pctFixed(t.from_pct)}</td><td class="n">${pctFixed(t.to_pct)}</td><td class="n">${pctFixed(t.amount_pct)}</td><td class="n">${money(t.amount_usd)}</td><td class="n">${t.action==='TRIM'?money(t.realized_gain_usd):'—'}</td><td class="n">${money(t.tax_usd)}</td><td class="n">${money(t.cost_usd)}</td><td class="l">${esc(t.priority||'')}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="note" style="margin:6px 0 0">เงินที่ไม่มีตัวไหนผ่านเกณฑ์รับ → cash ${pctFixed(reb.to_cash_pct)} · turnover ${pctFixed(c.turnover_pct)} · ภาษีกำไร ${money(c.tax_usd)} + ค่าเทรด ${money(c.trading_usd)} (S6)</p>${rule}${sizing}`;
}
function capRiskHint(cv){const r=capRiskRows(cv);const big=r.filter(x=>x.diff!=null&&x.diff>=3).sort((a,b)=>b.diff-a.diff)[0];return big?`${big.ticker} ถือเงิน ${pctFixed(big.cap)} แต่สร้างความเสี่ยง ${pctFixed(big.risk)} (มากกว่าน้ำหนักเงิน +${big.diff}%).`:'';}
function hiddenConc(alloc,cv){
  const sl=(alloc&&alloc.by_sleeve)||[]; const top=sl[0];
  let out=[]; if(top&&top.value>=40)out.push(`&#9888; กระจุกที่ <b>${esc(top.label)}</b> ${pctFixed(top.value)} ของพอร์ต`);
  const ccy=(alloc&&alloc.by_currency)||[]; if(ccy[0]&&ccy[0].value>=80)out.push(`USD/${esc(ccy[0].label)} exposure ${pctFixed(ccy[0].value)} — FX single-currency`);
  const hi=capRiskRows(cv)[0]; if(hi)out.push(`ตัวขับความเสี่ยงหลัก: <b>${esc(hi.ticker)}</b> (${pctFixed(hi.abs)} ของความเสี่ยงรวม)`);
  return out.length?out.join(' · '):'<span class="muted">ไม่พบ concentration เด่นเกินเกณฑ์</span>';
}
function panelExplain(d){
  const cv=d.capital_vs_risk||{},rows=capRiskRows(cv),st=d.stress||{},suit=d.suitability||{},dv=d.diversification||{},reb=d.rebalance||{};
  const worst=worstScenario(st);
  const top=rows[0],bigGap=rows.filter(x=>x.diff!=null&&x.diff>0).sort((a,b)=>b.diff-a.diff)[0];
  const firstAct=(reb.trades||[])[0];
  const li=[];
  if(top)li.push(`พอร์ตขับเคลื่อนโดย <b>${esc(top.ticker)}</b> — สร้าง ${pctFixed(top.abs)} ของความเสี่ยงรวม`);
  if(bigGap&&bigGap.ticker!==(top||{}).ticker)li.push(`<b>${esc(bigGap.ticker)}</b> เงินน้อยแต่เสี่ยงเกินน้ำหนัก (+${bigGap.diff}%)`);
  if(worst)li.push(`Scenario เจ็บสุด: <b>${esc(worst.label)}</b> ~${pctFixed(worst.loss_pct)}`);
  li.push(`Diversification ${d.snapshot.diversification_score}/100 — DR crisis ${dv.dr_crisis??'—'} (ใกล้ 1 = แทบไม่ช่วยตอนวิกฤต)`);
  if((dv.score&&dv.score.sub&&dv.score.sub.gap_coverage)===0)li.push(`ขาด ballast: ไม่มี bond / gold / cash ถ่วงพอร์ต`);
  li.push(suit.within_tolerance?`Drawdown ยังอยู่ในขีดที่รับได้`:`&#9888; Drawdown อาจเกินที่รับได้ — เรื่องนี้สำคัญสุด`);
  li.push(reb.no_trade?`Action: NO TRADE`:(firstAct?`Action แรก: ${esc(firstAct.action)} ${esc(firstAct.ticker)} ${pctFixed(firstAct.amount_pct)}`:'Action: review'));
  return `<ul style="margin:0;padding-left:18px;font-size:var(--t-sm);line-height:1.7">`+li.map(x=>`<li>${x}</li>`).join('')+`</ul>`;
}
function panelLegend(m){
  const src=m.sources||{}; const srcTxt=Object.keys(src).map(t=>`${t}:${src[t]}`).join(' · ');
  return `<p class="note" style="margin:10px 0 0;border-top:1px dashed var(--line);padding-top:8px">
    Tags: [STORED]=จาก data model · [CALC]=คำนวณ · [JUDG-PROXY]=ค่าสมมติ asset-class · [JUDG-SCENARIO]=stress illustrative.
    Covariance: <b>${esc(m.cov_mode||'?')}</b> ${esc(m.cov_tag||'')}${m.quota_degraded?' · &#9888; degraded to free source (quota)':''}<br>price source — ${esc(srcTxt)}</p>`;
}
