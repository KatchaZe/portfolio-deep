// The My Portfolio tab: the holding card (the watchlist reuses it), the totals row, and
// the add / remove / refresh / daily actions.

async function load(){
  try{
    const r=await fetch('/api/portfolio');
    if(r.status===401){document.getElementById('p1').innerHTML='<div style="padding:14px;font-size:calc(13px*var(--fs))">🔒 This app is protected by a token.<br>Open <b>/?token=YOUR_APP_TOKEN</b> once (the APP_TOKEN you set on the server) — it will be remembered after that.</div>';return;}
    const d=await r.json();
    document.getElementById('ver').textContent='v'+(d.version||'7.3');
    renderQuota(d.quota); renderPortfolio(d);
  }catch(e){document.getElementById('p1').innerHTML='<div class="down" style="padding:14px;font-size:calc(13px*var(--fs))">&#9888; Could not load the portfolio ('+esc(String(e))+') — server may be cold-starting; retry in a few seconds.</div>';}
}
function renderQuota(q){
  if(!q)return; const el=document.getElementById('quota');
  el.className='quota'+(q.warn?' warn':'');
  el.textContent=`FMP today ${q.used}/${q.cap} (${q.pct}%) · holdings ${q.holdings} · ~${q.headroom_tickers} ticker-fetches left`+(q.warn?' · ⚠ near quota — add fewer / refresh later':'');
}

// momCell / rankCell build table cells; a card keeps only what is inside the <td>
function cellContent(html){try{const t=document.createElement('table');t.innerHTML='<tbody><tr>'+html+'</tr></tbody>';const td=t.querySelector('td');return td?td.innerHTML:html;}catch(e){return html;}}
function cardCell(lbl,html){return `<div style="min-width:96px;flex:1 1 96px"><div style="${CAPS_LABEL}">${lbl}</div><div style="font-size:calc(12px*var(--fs));margin-top:2px">${html}</div></div>`;}
const CARD_BOX='background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px';
// S3 / S40-41: bonds, gold and crypto have no equity cash flows, so their card shows no
// FV / GARP / EQ (pricing, not valuation)
const ASSET_CLASS_NOTE={
  bond:'ตราสารหนี้ — ความเสี่ยงหลักคืออัตราดอกเบี้ย (duration) ดูผลใน Allocation &rarr; Bond rate risk',
  other:'ไม่แสดง FV / Upside / GARP — สินทรัพย์นี้เล่นเกม <b>pricing</b> ไม่ใช่ valuation (S40-41): กำไรมาจากคนถัดไปจ่ายแพงกว่าเท่านั้น จำกัดน้ำหนัก',
};

// one holding, or one watchlist name when watch is true
function pcard(x,watch){
  const head=cardHead(x,watch);
  const extra=watch
    ? `<div style="margin-top:8px"><button onclick="promote('${x.ticker}')" style="border:0;border-radius:6px;padding:6px 11px;background:#0d9488;color:#fff;font-weight:700;cursor:pointer;font-size:calc(11px*var(--fs))">+ Portfolio</button></div>`
    : `<details style="margin-top:6px"><summary style="font-size:calc(11px*var(--fs));color:#64748b;cursor:pointer">WACC / Ke / EVA + DEEP sub-scores</summary>${deepDrawer(x)}</details>`;
  const ac=ASSET_CLASS[x.ticker];
  if(ac){
    return `<div style="${CARD_BOX}">${head}
      <div class="muted" style="font-size:calc(11px*var(--fs));margin:8px 0 2px">${ASSET_CLASS_NOTE[ac==='bond'?'bond':'other']}</div>${signalGrid(x,'8px 0 6px','      ','')}${guardBadges(x)}${adviceLine(x)}${extra}</div>`;
  }
  // Value first (S5). The S20 panel sits with the value block but does not change the
  // grid: only a value gauge moves Anchor FV and Upside out of it.
  const vg=valueGauge(x);
  const reco=`<span style="font-size:calc(10px*var(--fs));font-weight:700;color:${RECO_COLOR[x.recommendation]||'#64748b'}">${x.recommendation||''}</span>`;
  const deep=`<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:8px 0 4px"><b style="font-size:calc(16px*var(--fs))">${fixed(x.composite,2)}</b><span style="font-size:calc(11px*var(--fs))">${x.stars||''}</span>${reco}${moatChip(x)}${eqBadge(x)}${garpBadge(x)}</div>`;
  const valueCells=vg?'':`
    ${cardCell('Anchor FV',anchorCell(x))}
    ${cardCell('Upside',upsideCell(x))}`;
  // earnings dots answer "beat the estimate?", the trend strip below them "improving?"
  const earn=`<div style="margin:6px 0"><div style="${CAPS_LABEL}">Earnings &middot; EPS / Rev / Mgn &middot; 5q</div><div style="margin-top:2px;padding-left:8px">${earnCircles(x)}</div></div>`;
  const verdict=`<div class="muted" style="font-size:calc(12px*var(--fs));border-top:0.5px solid #e2e8f0;padding-top:6px;margin-top:4px">${esc(x.verdict)}</div>`+adviceLine(x);
  return `<div style="${CARD_BOX}">${head}${vg}${youngPanel(x)}${deep}${signalGrid(x,'6px 0','    ',valueCells)}${guardBadges(x)}${earn}${trendStrip(x)}${verdict}${extra}</div>`;
}
function signalGrid(x,margin,indent,more){
  return `<div style="display:flex;flex-wrap:wrap;gap:10px;margin:${margin}">
${indent}${cardCell('Momentum',cellContent(momCell(x)))}
${indent}${cardCell('Rank',cellContent(rankCell(x)))}
${indent}${cardCell('Action',dispAction(x))}${more}</div>`;
}
// ticker, badges and remove button on the left; price and P/L as faint context on the right
function cardHead(x,watch){
  const chg=x.change==null?'':` <span class="${x.change>=0?'up':'down'}" style="font-size:calc(11px*var(--fs))">${x.change>=0?'+':''}${fixed(x.change)}%</span>`;
  const rmFn=watch?`removeWatch('${x.ticker}')`:`removeHolding('${x.ticker}')`;
  const ac=ASSET_CLASS[x.ticker];
  const pl=x.pl==null?'&mdash;':`${x.pl>=0?'+':'-'}${money(Math.abs(x.pl))} (${x.pl_pct>=0?'+':''}${fixed(x.pl_pct,1)}%)`;
  const right=`<div style="text-align:right"><div style="font-size:calc(12px*var(--fs));font-weight:600;color:#475569">${fixed(x.price)}${chg}</div>`
    +(watch?'':`<div class="muted" style="font-size:calc(10px*var(--fs));margin-top:2px">P/L ${pl}</div>`)+`</div>`;
  return `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
    <div>${dot(x.confidence_tier)}<b style="font-size:calc(14px*var(--fs))">${x.ticker}</b>${flagIcon(x)}${ac?' '+pricingBadge(ac):''} <span class="rm" title="remove" onclick="${rmFn}">&times;</span>
      <div class="muted" style="font-size:calc(10px*var(--fs))">${esc(x.company)} &middot; ${esc(x.sector)}</div></div>
    ${right}</div>`;
}
const CARD_GRID='display:grid;grid-template-columns:repeat(auto-fit,minmax(calc(330px*var(--fs)),1fr));gap:12px';
function renderPortfolio(d){
  // Allocation and the Risk Desk read this for Portfolio MoS, the story and the cash stance
  window.__last={rows:d.rows||[],market:d.market,benchmark:d.benchmark,philosophy:d.philosophy};
  renderMomBanner(d.momentum_meta);
  renderAssumptions(d.market);
  renderMarket(d.market);
  renderBenchmark(d.benchmark);
  renderPhilosophy(d.philosophy);
  renderSynth(d);
  document.getElementById('p1').innerHTML = (d.rows&&d.rows.length)
    ? portfolioTotals(d.totals||{})+`<div style="${CARD_GRID}">${d.rows.map(x=>pcard(x,false)).join('')}</div>`
    : '<div class="muted" style="padding:10px;font-size:calc(13px*var(--fs))">No holdings yet — add one above.</div>';
}
function portfolioTotals(t){
  const pl=t.pl==null?MUTED_DASH:`<b class="${t.pl>=0?'up':'down'}">${t.pl>=0?'+':''}${money(Math.abs(t.pl))} ${upDownPct(t.pl_pct)}</b>`;
  return `<div style="display:flex;gap:18px;flex-wrap:wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin-bottom:10px;font-size:calc(13px*var(--fs))"><div><span class="muted" style="font-size:calc(11px*var(--fs))">Market value</span> <b>${money(t.market_value)}</b></div><div><span class="muted" style="font-size:calc(11px*var(--fs))">P/L</span> ${pl}</div></div>`;
}

async function addHolding(){
  const t=document.getElementById('t').value.trim().toUpperCase();
  const sh=document.getElementById('sh').value, ac=document.getElementById('ac').value;
  if(!t)return; const sp=document.getElementById('spA'),msg=document.getElementById('addMsg');
  sp.style.display='inline-block'; msg.textContent='Fetching '+t+'…';
  try{
    let u='/api/holding?ticker='+encodeURIComponent(t);
    if(sh!=='')u+='&shares='+encodeURIComponent(sh);
    if(ac!=='')u+='&avg_cost='+encodeURIComponent(ac);
    const r=await fetch(u,{method:'POST'}); const d=await r.json();
    if(r.status===401){msg.textContent='🔒 unauthorized — open /?token=YOUR_APP_TOKEN';return;}
    renderPortfolio(d.view||d); if(d.quota)renderQuota(d.quota);
    const errs=(d.result&&d.result.errors)||[];
    msg.textContent=errs.length?('⚠ '+t+': '+errs.join('; ')):('✓ '+t+' added/updated');
    document.getElementById('t').value='';document.getElementById('sh').value='';document.getElementById('ac').value='';
  }catch(e){msg.textContent='✗ '+e}finally{sp.style.display='none'}
}
async function removeHolding(t){if(!confirm('Remove '+t+'?'))return;await fetch('/api/holding/remove?ticker='+encodeURIComponent(t),{method:'POST'});load();}

// a header run button: disabled with its spinner on while `work` runs; the summary line
// shows the busy text, then what `work` returns, or the error
async function runFromHeader(btnId,spinId,busyText,work){
  const b=document.getElementById(btnId),sp=document.getElementById(spinId),out=document.getElementById('summary');
  b.disabled=true;sp.style.display='inline-block';out.textContent=busyText;
  try{out.textContent=await work();}
  catch(e){out.textContent='Error: '+e}finally{b.disabled=false;sp.style.display='none'}
}
function refresh(){
  return runFromHeader('btnRefresh','spR','Refreshing fundamentals (SEC + FMP + Yahoo)…',async()=>{
    const d=await (await fetch('/api/refresh',{method:'POST'})).json();
    const rs=d.result||{};const rfWarn=rs.rf_live===false?' ⚠ Rf fallback (live 10Y yield unavailable)':'';
    const text=`Refreshed ${(rs.refreshed||[]).length}, errors ${(rs.errors||[]).length}, FMP calls ${rs.fmp_calls} (Rf ${rs.rf_pct}%${rfWarn}). ${(rs.errors||[]).join('; ')}`;
    renderPortfolio(d.view);renderQuota(d.quota);__riskCache=null;
    return text;
  });
}
function daily(){
  return runFromHeader('btnDaily','spD','Updating price + momentum (Yahoo)…',async()=>{
    renderPortfolio(await (await fetch('/api/daily',{method:'POST'})).json());__riskCache=null;
    return 'Daily updated.';
  });
}
