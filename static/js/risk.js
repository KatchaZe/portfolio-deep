// The Risk Desk on the Allocation tab: capital weight against risk weight, concentration,
// stress, downside, suitability and the rebalance plan, led by a five-line story.
function riskCard(label,val,color){return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:8px 11px;min-width:96px"><div style="${TILE_LABEL}">${label}</div><div style="font-size:calc(17px*var(--fs));font-weight:800;color:${color||'#0f172a'}">${val}</div></div>`;}
function lossColor(v){return v<=-30?'#dc2626':v<=-15?'#ea580c':v<0?'#d97706':'#16a34a';}
function scoreColor(s){return s>=60?'#16a34a':s>=40?'#d97706':'#dc2626';}
function riskSection(title,inner,note){return `<div style="margin:14px 0"><div style="font-size:calc(13px*var(--fs));font-weight:800;margin:0 0 6px">${title}${note?` <span class="muted" style="font-weight:600;font-size:calc(10px*var(--fs))">${note}</span>`:''}</div>${inner}</div>`;}

// one /api/risk payload serves the Risk Desk and the Correlation tab, keyed by its inputs
let __riskCache=null;
async function fetchRiskPayload(en,force){
  const tol=(document.getElementById('riskTol')||{}).value||20, hz=(document.getElementById('riskHorizon')||{}).value||1;
  // the holdings are part of the key: adding, removing or resizing a position (any
  // path, including the watchlist promote) must not serve the old portfolio's desk
  const hold=((window.__last||{}).rows||[]).map(r=>r.ticker+':'+(r.shares??'')).sort().join(',');
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
  body.innerHTML='<div class="muted" style="font-size:calc(12px*var(--fs));padding:10px">Computing risk… (price history + covariance + stress)</div>';
  try{
    const d=await fetchRiskPayload(en,!!force);
    if(d.quota)renderQuota(d.quota); renderRisk(d);
  }catch(e){body.innerHTML='<div class="down" style="padding:10px;font-size:calc(12px*var(--fs))">Error: '+esc(String(e))+'</div>';}
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
// S4 portfolio margin of safety: market-value-weighted MoS (V − P)/V of the holdings
// that have a fair value — the same definition each card shows
function portMoS(){
  const rows=(window.__last&&window.__last.rows)||[];
  let ws=0,g=0,n=0,wn=0,nets=0;
  for(const r of rows){
    const mv=r.market_value,u=mosOf(r);
    if(mv&&u!=null){ws+=mv;g+=mv*u;n++;if(r.net_upside_pct!=null){nets+=mv*r.net_upside_pct;wn+=mv;}}
  }
  if(!ws||!n)return null;
  return {gross:Math.round(g/ws*10)/10,net:wn?Math.round(nets/wn*10)/10:null,n,of:rows.length};
}
function portMoSCard(){
  const m=portMoS();
  if(!m)return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:8px 11px;min-width:calc(160px*var(--fs));flex:1 1 160px"><div style="${TILE_LABEL}">Portfolio MoS (S4)</div><div style="font-size:calc(15px*var(--fs));font-weight:800" class="muted">&mdash;</div><div class="muted" style="font-size:calc(10px*var(--fs))">ต้องมี Fair Value ต่อหุ้นก่อน (Run Fundamental Refresh)</div></div>`;
  const col=m.gross>=15?'#166534':m.gross>=0?'#475569':'#991b1b';
  return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:8px 11px;min-width:calc(160px*var(--fs));flex:1 1 160px" title="margin of safety ระดับพอร์ต = ค่าเฉลี่ยถ่วงน้ำหนัก MV ของ MoS (FV − ราคา)/FV รายตัว (${m.n}/${m.of} ตัวที่มี FV) · net = upside หลังค่าเทรด/ภาษี — ส่วนเผื่อรวมของพอร์ตต่อความผิดพลาดในการประเมิน (S4)">
    <div style="${TILE_LABEL}">Portfolio MoS (S4)</div>
    <div style="font-size:calc(17px*var(--fs));font-weight:800;color:${col}">${signed(m.gross)}%</div>
    <div class="muted" style="font-size:calc(10px*var(--fs))">${m.net!=null?`net upside หลัง cost/tax ${signed(m.net)}% · `:''}${m.n}/${m.of} ตัวมี FV</div></div>`;
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
    ?`<div style="background:#fef2f2;border:2px solid #dc2626;border-radius:10px;padding:9px 12px;font-size:calc(12.5px*var(--fs));margin-bottom:8px">&#128308; <b>FINDING หลัก:</b> stress drawdown <b>${pctFixed(suit.binding_drawdown_pct)}</b> เกินขีดรับได้ <b>${pctFixed(-(suit.tolerance_pct||0))}</b>${firstAct?` &rarr; Action แรก: <b>${esc(firstAct.action)} ${esc(firstAct.ticker)} ${pctFixed(firstAct.from_pct)} &rarr; ${pctFixed(firstAct.to_pct)}</b>`:''} <span class="muted">(รายละเอียด: ส่วน 6+7)</span></div>`
    :`<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:8px 12px;font-size:calc(12px*var(--fs));margin-bottom:8px">&#9989; Drawdown ผูกพัน ${pctFixed(suit.binding_drawdown_pct)} ยังอยู่ในขีดรับได้ ${pctFixed(-(suit.tolerance_pct||0))}</div>`;
  const rws=[storyPhilosophy(p,sleeve,top),storyDownside(s,dn,worst,vr),storyBenchmark(b),storyTiming(v,dv),storyCosts(reb)];
  return finding+`<div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden">${rws.join('')}</div>
    <div class="muted" style="font-size:calc(10px*var(--fs));margin-top:5px">อ่าน 5 บรรทัดนี้ก่อน — ส่วน 1–7 ด้านล่างคือห้องเครื่อง (หลักฐาน/รายละเอียด) ของแต่ละบรรทัด</div>`;
}
function storyRow(t,ref,txt,warn){return `<div style="display:flex;gap:8px;padding:6px 10px;border-top:.5px solid #e2e8f0;font-size:calc(12px*var(--fs))${warn?';background:#fffbeb':''}"><span style="flex:none;width:152px"><b>${t}</b><br><span class="muted" style="font-size:calc(9px*var(--fs))">${ref}</span></span><span style="line-height:1.6">${txt}</span></div>`;}
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
  if(err)bits.push('<span style="color:#991b1b">5b คำนวณไม่ได้ (returns ว่าง) — ลอง Run risk แบบ data=auto อีกครั้ง</span>');
  return storyRow('&#9313; วัดที่ขาลง ไม่ใช่ vol','S2/S4 · ส่วน 5·5b',bits.join(' · '),err);
}
function storyBenchmark(b){
  const t=b.excess_pp==null?'<span class="muted">ยังไม่มีข้อมูล benchmark — กด Run Daily</span>'
    :`12M: พอร์ต ${signed(b.portfolio_12m)}% vs S&amp;P ${signed(b.market_12m)}% (<b>${signed(b.excess_pp)}pp</b>)${b.excess_pp<0?' — อย่าเพิ่ม turnover เพื่อไล่ตาม':''}`;
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
    const turn=(reb.trades||[]).reduce((a2,x2)=>a2+Math.abs(x2.amount_pct||0),0)/2;
    t=`แผน rebalance ขยับ ~<b>${turn.toFixed(1)}%</b> ของพอร์ต — ค่าเทรด ~${(turn/100*0.2).toFixed(2)}% + ภาษีกำไรที่ realize (15%) &rarr; <b>ทำเป็นขั้น</b> เริ่มจาก Must ก่อน อย่ารวดเดียว`;
  }
  return storyRow('&#9316; หัก cost/tax ก่อนเชื่อ','S6 · ส่วน 7',t,!reb.no_trade);
}
function renderRisk(d){
  const body=document.getElementById('riskBody');
  if(!d||d.status!=='ok'){body.innerHTML='<div class="muted" style="padding:10px;font-size:calc(12px*var(--fs))">'+esc((d&&d.message)||'No risk data — add holdings with price first.')+'</div>';return;}
  const cv=d.capital_vs_risk||{},m=d.meta||{};
  body.innerHTML = riskSection('&#128214; Portfolio Story — สรุปตาม 5 หลัก Damodaran','<div>'+riskStory(d)+'</div>','อ่านก่อน 1 นาที')
    + riskSection('1 · Portfolio Snapshot',riskSnapshotHTML(d))
    + riskSection('2 · Capital Allocation (by risk driver)',riskAllocationHTML())
    + riskSection('3 · Capital Weight vs Risk Weight',capitalVsRiskHTML(cv),m.cov_tag||'')
    + riskSection('4 · Concentration Map',concentrationHTML(d,cv))
    + riskSection('5 · Stress Test',stressHTML(d),'[JUDG-SCENARIO]')
    + riskSection('5b · Downside Risk (S2/S4)',downsideHTML(d))
    + riskSection('6 · Suitability (headline finding)',suitabilityHTML(d))
    + riskSection('7 · Rebalancing Plan',rebalanceHTML(d,cv))
    + riskSection('Bottom line',panelExplain(d))
    + panelLegend(m);
  drawRiskCharts(d);
}
function riskSnapshotHTML(d){
  const s=d.snapshot,dv=d.diversification||{};
  return `<div style="display:flex;gap:8px;flex-wrap:wrap">
    ${riskCard('Value','$'+Math.round(s.total_value).toLocaleString())}
    ${riskCard('Positions',s.n_positions)}
    ${riskCard('Est. Vol (annual)',pctFixed(s.port_vol_pct),'#0f172a')}
    ${riskCard('Severe drawdown',pctFixed(s.severe_drawdown_pct),lossColor(s.severe_drawdown_pct))}
    ${riskCard('Portfolio β',s.port_beta)}
    ${riskCard('Diversification',s.diversification_score+'/100',scoreColor(s.diversification_score))}</div>
    <div class="muted" style="font-size:calc(11px*var(--fs));margin-top:5px">${esc(s.scope||'')} · DR normal <b>${dv.dr_normal??'—'}</b> / crisis <b>${dv.dr_crisis??'—'}</b> — ${esc((dv.score&&dv.score.band)||'')}</div>`;
}
function riskAllocationHTML(){
  return `<div style="display:flex;gap:24px;flex-wrap:wrap">
    <div style="width:280px;height:290px"><div class="muted" style="font-size:calc(11px*var(--fs));text-align:center">By risk sleeve</div><div style="height:270px;position:relative"><canvas id="rcSleeve"></canvas></div></div>
    <div style="width:280px;height:290px"><div class="muted" style="font-size:calc(11px*var(--fs));text-align:center">By currency</div><div style="height:270px;position:relative"><canvas id="rcCcy"></canvas></div></div></div>`;
}
function capitalVsRiskHTML(cv){
  return `<div style="max-width:760px;height:${Math.max(90,capRiskRows(cv).length*22)}px;position:relative"><canvas id="rcCapRisk"></canvas></div>
    <div class="muted" style="font-size:calc(11px*var(--fs));margin-top:4px">แท่งเทา = %เงินที่ลง · แท่งแดง = %ความเสี่ยงที่สร้าง (เขียว = ตัวช่วยลดความเสี่ยง). ${capRiskHint(cv)}</div>`;
}
function concentrationHTML(d,cv){
  const c=d.concentration||{};
  return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
    ${riskCard('HHI',c.hhi??'—')}${riskCard('Effective N',c.eff_n??'—')}${riskCard('Top-5',pctFixed(c.top5_pct))}${riskCard('Top-10',pctFixed(c.top10_pct))}${riskCard('ENB (bets)',(((d.diversification||{}).enb)??'—'))}${riskCard('Risk balance Nₑ',(cv.risk_balance_n??cv.enb_abs??'—'))}</div>
    <div style="width:300px;max-width:100%;height:110px;position:relative"><canvas id="rcSector"></canvas></div>
    <div style="font-size:calc(11px*var(--fs));margin-top:6px">${hiddenConc(d.allocation,cv)}</div>`;
}
function stressHTML(d){
  const st=d.stress||{},vr=st.var||{};
  return `<div style="max-width:760px;height:${Math.max(100,(((st.historical||[]).length)+((st.hypothetical||[]).length))*20)}px;position:relative"><canvas id="rcStress"></canvas></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">${riskCard('VaR 95%',pctFixed(vr.var95_pct),'#dc2626')}${riskCard('VaR 99%',pctFixed(vr.var99_pct),'#dc2626')}${riskCard('CVaR 95%',pctFixed(vr.cvar95_pct),'#991b1b')}</div>
    ${(st.var_hist&&st.var_hist.var95_pct!=null)?`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">${riskCard('Hist VaR 95%',pctFixed(st.var_hist.var95_pct),'#7c3aed')}${riskCard('Hist VaR 99%',pctFixed(st.var_hist.var99_pct),'#7c3aed')}${riskCard('Hist CVaR 95%',pctFixed(st.var_hist.cvar95_pct),'#6d28d9')}</div><div class="muted" style="font-size:calc(10px*var(--fs));margin-top:2px">Historical VaR (P1-7, S2/S4): จาก returns จริง ${st.var_hist.n} วัน — เห็น fat tail ตามที่เคยเกิด ไม่สมมุติ normal</div>`:''}
    <div class="muted" style="font-size:calc(11px*var(--fs));margin-top:5px">${esc(st.tag||'')} · Reverse stress: ${esc((st.reverse&&st.reverse.note)||'—')}</div>`;
}
// S2/S4: total risk paired with its downside, next to the portfolio margin of safety
function downsideHTML(d){
  const s=d.snapshot;
  const dn=d.downside||{};
  const rr=d.rate_risk||{}, pa=(d.meta&&d.meta.pricing_assets)||[];
  const rateLine=rr.has_bonds?`<div class="muted" style="font-size:calc(11px*var(--fs));margin-top:4px">Bond rate risk (S3): +100bps &rarr; <b>${rr.loss_pct}%</b> on portfolio · ${esc(rr.tag||'')}</div>`:'';
  const pricingLine=pa.length?`<div class="muted" style="font-size:calc(11px*var(--fs));margin-top:4px;color:#b45309">&#9888; ${esc(pa.join(', '))} = pricing plays (no cash flow, S40-41) — high uncertainty, cap weight</div>`:'';
  const beta=s.port_beta, dnb=dn.downside_beta;
  const asym=(dnb!=null&&beta!=null&&Number(dnb)>Number(beta));
  const pairCard=`<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:8px 11px;min-width:230px;flex:1 1 230px">
      <div style="${TILE_LABEL}">Total &rarr; Downside (S2/S4)</div>
      <div style="font-size:calc(13px*var(--fs));margin-top:3px">Vol <b>${pctFixed(dn.vol_pct)}</b> &rarr; Downside dev <b style="color:#b45309">${pctFixed(dn.semidev_pct)}</b> &middot; Sortino <b>${dn.sortino??'—'}</b></div>
      <div style="font-size:calc(13px*var(--fs))">&beta; <b>${beta??'—'}</b> &rarr; Downside &beta; <b style="color:#b45309">${dnb??'—'}</b></div>
      ${asym?'<div style="font-size:calc(10px*var(--fs));color:#b45309;margin-top:2px">&#9888; downside &beta; &gt; &beta; — พอร์ตนี้ "ขาลงแรงกว่าขาขึ้น"</div>':''}</div>`;
  return `<div style="display:flex;gap:8px;flex-wrap:wrap">
    ${pairCard}${portMoSCard()}</div>
    <div class="muted" style="font-size:calc(11px*var(--fs));margin-top:5px">${esc(dn.tag||'')} · วัดความเสี่ยง “ขาลง” แยกจากความผันผวนรวม — Sortino สูง=ดี · downside β = แรงตกเทียบ S&P เฉพาะวันตลาดลง (${dn.n||0} วัน)</div>${rateLine}${pricingLine}`;
}
function suitabilityHTML(d){
  const suit=d.suitability||{}; const ok=suit.within_tolerance;
  return `<div style="border-radius:9px;padding:11px 13px;font-size:calc(12.5px*var(--fs));font-weight:600;background:${ok?'#dcfce7':'#fee2e2'};color:${ok?'#166534':'#991b1b'};border:1px solid ${ok?'#86efac':'#fecaca'}">
    ${ok?'✅':'⛔'} Suitability: drawdown ที่ผูกพัน <b>${pctFixed(suit.binding_drawdown_pct)}</b> เทียบขีดรับได้ <b>${pctFixed(-(suit.tolerance_pct||0))}</b> — <b>${esc(suit.verdict||'')}</b></div>`;
}
function rebalanceHTML(d,cv){
  const reb=d.rebalance||{};
  if(reb.no_trade)return `<div style="border-radius:9px;padding:10px 13px;background:#dcfce7;color:#166534;border:1px solid #86efac;font-size:calc(12.5px*var(--fs));font-weight:700">NO TRADE — พอร์ตยังอยู่ใน policy band</div>`;
  const af=reb.after||{};
  return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      ${riskCard('Vol now',pctFixed(cv.port_vol_pct))}${riskCard('Vol after',pctFixed(af.port_vol_pct),'#16a34a')}
      ${riskCard('Top risk now',(capRiskRows(cv)[0]||{}).ticker||'—')}${riskCard('Top risk after',af.top_risk||'—')}
      ${riskCard('Stress worst after',pctFixed(af.stress_worst_pct),lossColor(af.stress_worst_pct))}</div>
      <table style="font-size:calc(12px*var(--fs))"><thead><tr><th class="l">Ticker</th><th>Action</th><th>From</th><th>To</th><th>Δ</th><th class="l">Priority</th></tr></thead><tbody>
      ${(reb.trades||[]).map(t=>`<tr><td class="l"><b>${t.ticker}</b></td><td><span class="tag" style="background:${t.action==='TRIM'?'#ea580c':'#0d9488'}">${t.action}</span></td><td>${pctFixed(t.from_pct)}</td><td>${pctFixed(t.to_pct)}</td><td>${pctFixed(t.amount_pct)}</td><td class="l">${t.priority}</td></tr>`).join('')}
      </tbody></table>`;
}
function capRiskHint(cv){const r=capRiskRows(cv);const big=r.filter(x=>x.diff!=null&&x.diff>=3).sort((a,b)=>b.diff-a.diff)[0];return big?`${big.ticker} ถือเงิน ${pctFixed(big.cap)} แต่สร้างความเสี่ยง ${pctFixed(big.risk)} (มากกว่าน้ำหนักเงิน +${big.diff}pp).`:'';}
function hiddenConc(alloc,cv){
  const sl=(alloc&&alloc.by_sleeve)||[]; const top=sl[0];
  let out=[]; if(top&&top.value>=40)out.push(`⚠ กระจุกที่ <b>${esc(top.label)}</b> ${pctFixed(top.value)} ของพอร์ต`);
  const ccy=(alloc&&alloc.by_currency)||[]; if(ccy[0]&&ccy[0].value>=80)out.push(`USD/${esc(ccy[0].label)} exposure ${pctFixed(ccy[0].value)} — FX single-currency`);
  const hi=capRiskRows(cv)[0]; if(hi)out.push(`ตัวขับความเสี่ยงหลัก: <b>${hi.ticker}</b> (${pctFixed(hi.abs)} ของความเสี่ยงรวม)`);
  return out.length?out.join(' · '):'<span class="muted">ไม่พบ concentration เด่นเกินเกณฑ์</span>';
}
function panelExplain(d){
  const cv=d.capital_vs_risk||{},rows=capRiskRows(cv),st=d.stress||{},suit=d.suitability||{},dv=d.diversification||{},reb=d.rebalance||{};
  const worst=worstScenario(st);
  const top=rows[0],bigGap=rows.filter(x=>x.diff!=null&&x.diff>0).sort((a,b)=>b.diff-a.diff)[0];
  const firstAct=(reb.trades||[])[0];
  const li=[];
  if(top)li.push(`พอร์ตขับเคลื่อนโดย <b>${top.ticker}</b> — สร้าง ${pctFixed(top.abs)} ของความเสี่ยงรวม`);
  if(bigGap&&bigGap.ticker!==(top||{}).ticker)li.push(`<b>${bigGap.ticker}</b> เงินน้อยแต่เสี่ยงเกินน้ำหนัก (+${bigGap.diff}pp)`);
  if(worst)li.push(`Scenario เจ็บสุด: <b>${esc(worst.label)}</b> ~${pctFixed(worst.loss_pct)}`);
  li.push(`Diversification ${d.snapshot.diversification_score}/100 — DR crisis ${dv.dr_crisis??'—'} (ใกล้ 1 = แทบไม่ช่วยตอนวิกฤต)`);
  if((dv.score&&dv.score.sub&&dv.score.sub.gap_coverage)===0)li.push(`ขาด ballast: ไม่มี bond / gold / cash ถ่วงพอร์ต`);
  li.push(suit.within_tolerance?`Drawdown ยังอยู่ในขีดที่รับได้`:`⛔ Drawdown อาจเกินที่รับได้ — เรื่องนี้สำคัญสุด`);
  li.push(reb.no_trade?`Action: NO TRADE`:(firstAct?`Action แรก: ${firstAct.action} ${firstAct.ticker} ${pctFixed(firstAct.amount_pct)}`:'Action: review'));
  return `<ul style="margin:2px 0 0;padding-left:18px;font-size:calc(12px*var(--fs));line-height:1.7">`+li.map(x=>`<li>${x}</li>`).join('')+`</ul>`;
}
function panelLegend(m){
  const src=m.sources||{}; const srcTxt=Object.keys(src).map(t=>`${t}:${src[t]}`).join(' · ');
  return `<div style="margin-top:14px;border-top:1px dashed #e2e8f0;padding-top:8px;font-size:calc(11px*var(--fs));color:var(--grey)">
    Tags: [STORED]=จาก data model · [CALC]=คำนวณ · [JUDG-PROXY]=ค่าสมมติ asset-class · [JUDG-SCENARIO]=stress illustrative.
    Covariance: <b>${esc(m.cov_mode||'?')}</b> ${esc(m.cov_tag||'')}${m.quota_degraded?' · ⚠ degraded to free source (quota)':''}<br>price source — ${esc(srcTxt)}</div>`;
}
function drawRiskCharts(d){
  const al=d.allocation||{};
  makePie('rcSleeve',al.by_sleeve||[]); makePie('rcCcy',al.by_currency||[]);
  const r=capRiskRows(d.capital_vs_risk||{}).slice(0,10);
  percentBarChart('rcCapRisk',r.map(x=>x.ticker),[
    {label:'%Capital',data:r.map(x=>x.cap),backgroundColor:'#94a3b8',maxBarThickness:9},
    {label:'%Risk',data:r.map(x=>x.risk),backgroundColor:r.map(x=>x.risk<0?'#16a34a':'#dc2626'),maxBarThickness:9}],
    {labels:{font:{size:Math.round(10*__FS)},boxWidth:12}},10);
  const sec=(al.by_sector||[]);
  percentBarChart('rcSector',sec.map(x=>x.label),[{label:'%',data:sec.map(x=>x.value),backgroundColor:'#2563eb',maxBarThickness:12}],{display:false},9);
  const sc=scenariosWorstFirst(d.stress||{});
  percentBarChart('rcStress',sc.map(x=>x.label),[{label:'Est. loss %',data:sc.map(x=>x.loss_pct),backgroundColor:sc.map(x=>lossColor(x.loss_pct)),maxBarThickness:11}],{display:false},9);
}
// horizontal bar chart on a % axis
function percentBarChart(id,labels,datasets,legend,yFont){
  mkChart(id,{type:'bar',data:{labels,datasets},
    options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend},scales:{x:{ticks:{callback:v=>v+'%',font:{size:Math.round(9*__FS)}}},y:{ticks:{font:{size:Math.round(yFont*__FS)}}}}}});
}
