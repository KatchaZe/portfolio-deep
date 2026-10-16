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
function cellContent(html){try{const t=document.createElement('table');t.innerHTML='<tbody><tr>'+html+'</tr></tbody>';const td=t.querySelector('td');return td?td.innerHTML:html;}catch(e){return html;}}
function cardCell(lbl,html){return `<div style="min-width:96px;flex:1 1 96px"><div style="font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase">${lbl}</div><div style="font-size:calc(12px*var(--fs));margin-top:2px">${html}</div></div>`;}
function pcard(x,watch){
  const chg=x.change==null?'':` <span class="${x.change>=0?'up':'down'}" style="font-size:calc(11px*var(--fs))">${x.change>=0?'+':''}${fixed(x.change)}%</span>`;
  const reco=`<span style="font-size:calc(10px*var(--fs));font-weight:700;color:${RECO_COLOR[x.recommendation]||'#64748b'}">${x.recommendation||''}</span>`;
  const rmFn=watch?`removeWatch('${x.ticker}')`:`removeHolding('${x.ticker}')`;
  const ac=ASSET_CLASS[x.ticker];
  // Value-first (S5): ราคา/P&L เป็นบริบทมุมขวาแบบจาง — มูลค่า (gauge) คือพระเอกของ card
  const right=`<div style="text-align:right"><div style="font-size:calc(12px*var(--fs));font-weight:600;color:#475569">${fixed(x.price)}${chg}</div>`
    +(watch?'':`<div class="muted" style="font-size:calc(10px*var(--fs));margin-top:2px">P/L ${x.pl==null?'&mdash;':`${x.pl>=0?'+':'-'}${money(Math.abs(x.pl)).replace(/<[^>]*>/g,'')} (${x.pl_pct>=0?'+':''}${fixed(x.pl_pct,1)}%)`}</div>`)+`</div>`;
  const head=`<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
    <div>${dot(x.confidence_tier)}<b style="font-size:calc(14px*var(--fs))">${x.ticker}</b>${flagIcon(x)}${ac?' '+pricingBadge(ac):''} <span class="rm" title="remove" onclick="${rmFn}">&times;</span>
      <div class="muted" style="font-size:calc(10px*var(--fs))">${esc(x.company)} &middot; ${esc(x.sector)}</div></div>
    ${right}</div>`;
  const extra=watch
    ? `<div style="margin-top:8px"><button onclick="promote('${x.ticker}')" style="border:0;border-radius:6px;padding:6px 11px;background:#0d9488;color:#fff;font-weight:700;cursor:pointer;font-size:calc(11px*var(--fs))">+ Portfolio</button></div>`
    : `<details style="margin-top:6px"><summary style="font-size:calc(11px*var(--fs));color:#64748b;cursor:pointer">WACC / Ke / EVA + DEEP sub-scores</summary>${deepDrawer(x)}</details>`;
  if(ac){
    // S3/S40-41: bond/gold/crypto — ไม่มีกระแสเงินสดแบบหุ้น จึงไม่แสดง FV/GARP/EQ (เกม pricing ไม่ใช่ valuation)
    const note=ac==='bond'
      ?'ตราสารหนี้ — ความเสี่ยงหลักคืออัตราดอกเบี้ย (duration) ดูผลใน Allocation &rarr; Bond rate risk'
      :'ไม่แสดง FV / Upside / GARP — สินทรัพย์นี้เล่นเกม <b>pricing</b> ไม่ใช่ valuation (S40-41): กำไรมาจากคนถัดไปจ่ายแพงกว่าเท่านั้น จำกัดน้ำหนัก';
    const grid0=`<div style="display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 6px">
      ${cardCell('Momentum',cellContent(momCell(x)))}
      ${cardCell('Rank',cellContent(rankCell(x)))}
      ${cardCell('Action',dispAction(x))}</div>`;
    return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px">${head}
      <div class="muted" style="font-size:calc(11px*var(--fs));margin:8px 0 2px">${note}</div>${grid0}${guardBadges(x)}${adviceLine(x)}${extra}</div>`;
  }
  // REV-16: the S20 panel renders WITH the value block but must not decide the grid
  // layout below — `vg` alone still answers "is there a value visual?".
  const vg=valueGauge(x);
  const yp=youngPanel(x);
  const deep=`<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:8px 0 4px"><b style="font-size:calc(16px*var(--fs))">${fixed(x.composite,2)}</b><span style="font-size:calc(11px*var(--fs))">${x.stars||''}</span>${reco}${moatChip(x)}${eqBadge(x)}${garpBadge(x)}</div>`;
  const grid=vg
    ? `<div style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0">
    ${cardCell('Momentum',cellContent(momCell(x)))}
    ${cardCell('Rank',cellContent(rankCell(x)))}
    ${cardCell('Action',dispAction(x))}</div>`
    : `<div style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0">
    ${cardCell('Momentum',cellContent(momCell(x)))}
    ${cardCell('Rank',cellContent(rankCell(x)))}
    ${cardCell('Action',dispAction(x))}
    ${cardCell('Anchor FV',anchorCell(x))}
    ${cardCell('Upside',upsideCell(x))}</div>`;
  const earn=`<div style="margin:6px 0"><div style="font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase">Earnings &middot; EPS / Rev / Mgn &middot; 5q</div><div style="margin-top:2px;padding-left:8px">${earnCircles(x)}</div></div>`;
  const verdict=`<div class="muted" style="font-size:calc(12px*var(--fs));border-top:0.5px solid #e2e8f0;padding-top:6px;margin-top:4px">${esc(x.verdict)}</div>`+adviceLine(x);
  // T5 อยู่ใต้ Earnings circles: วงกลม = beat/miss รายไตรมาส (เทียบ "คาดการณ์"),
  // แถบเทรนด์ = โตต่อเนื่องไหมจริง ๆ (เทียบ "ตัวเอง") — สองคำถามคนละอันแต่ต่อเนื่องกัน
  return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px">${head}${vg}${yp}${deep}${grid}${guardBadges(x)}${earn}${trendStrip(x)}${verdict}${extra}</div>`;
}
function renderPortfolio(d){
  // เก็บ payload ล่าสุดไว้ให้ Allocation/Risk ใช้ (Portfolio MoS, Story, Cash stance) — client-side ล้วน
  window.__last={rows:d.rows||[],market:d.market,benchmark:d.benchmark,philosophy:d.philosophy};
  renderMomBanner(d.momentum_meta);
  renderAssumptions(d.market);
  renderMarket(d.market);
  renderBenchmark(d.benchmark);
  renderPhilosophy(d.philosophy);
  renderSynth(d);
  // P4: the old <table> row builder was dead code (card layout replaced it) — removed.
  const t=d.totals||{};
  const __cards=(d.rows||[]).map(x=>pcard(x,false)).join('');
  const __tot=`<div style="display:flex;gap:18px;flex-wrap:wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin-bottom:10px;font-size:calc(13px*var(--fs))"><div><span class="muted" style="font-size:calc(11px*var(--fs))">Market value</span> <b>${money(t.market_value)}</b></div><div><span class="muted" style="font-size:calc(11px*var(--fs))">P/L</span> ${t.pl==null?'<span class="muted">&mdash;</span>':`<b class="${t.pl>=0?'up':'down'}">${t.pl>=0?'+':''}${money(Math.abs(t.pl))} ${upDownPct(t.pl_pct)}</b>`}</div></div>`;
  document.getElementById('p1').innerHTML = (d.rows&&d.rows.length)?(__tot+`<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(calc(330px*var(--fs)),1fr));gap:12px">${__cards}</div>`):'<div class="muted" style="padding:10px;font-size:calc(13px*var(--fs))">No holdings yet — add one above.</div>';
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
async function refresh(){
  const b=document.getElementById('btnRefresh'),sp=document.getElementById('spR');
  b.disabled=true;sp.style.display='inline-block';document.getElementById('summary').textContent='Refreshing fundamentals (SEC + FMP + Yahoo)…';
  try{const r=await fetch('/api/refresh',{method:'POST'});const d=await r.json();
    const rs=d.result||{};const rfWarn=rs.rf_live===false?' ⚠ Rf fallback (live 10Y yield unavailable)':'';
    document.getElementById('summary').textContent=`Refreshed ${(rs.refreshed||[]).length}, errors ${(rs.errors||[]).length}, FMP calls ${rs.fmp_calls} (Rf ${rs.rf_pct}%${rfWarn}). ${(rs.errors||[]).join('; ')}`;
    renderPortfolio(d.view);renderQuota(d.quota);__riskCache=null;
  }catch(e){document.getElementById('summary').textContent='Error: '+e}finally{b.disabled=false;sp.style.display='none'}
}
async function daily(){
  const b=document.getElementById('btnDaily'),sp=document.getElementById('spD');
  b.disabled=true;sp.style.display='inline-block';document.getElementById('summary').textContent='Updating price + momentum (Yahoo)…';
  try{const r=await fetch('/api/daily',{method:'POST'});renderPortfolio(await r.json());document.getElementById('summary').textContent='Daily updated.';__riskCache=null;}
  catch(e){document.getElementById('summary').textContent='Error: '+e}finally{b.disabled=false;sp.style.display='none'}
}
