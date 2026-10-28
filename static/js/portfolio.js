// The My Portfolio tab: loading, the quota badge, the render entry point and the
// add / remove / refresh / daily actions. The card itself lives in card.js.

async function load(){
  try{
    const r=await fetch('/api/portfolio');
    if(r.status===401){document.getElementById('p1').innerHTML='<div class="empty"><b>This app is protected by a token.</b><span>Open <b>/?token=YOUR_APP_TOKEN</b> once (the APP_TOKEN you set on the server) — it will be remembered after that.</span></div>';return;}
    const d=await r.json();
    document.getElementById('ver').textContent='v'+(d.version||'7.3');
    renderQuota(d.quota); renderPortfolio(d);
  }catch(e){document.getElementById('p1').innerHTML='<div class="banner tone t-bear">&#9888; Could not load the portfolio ('+esc(String(e))+') — server may be cold-starting; retry in a few seconds.</div>';}
}
function renderQuota(q){
  if(!q)return; const el=document.getElementById('quota');
  el.className='quota'+(q.warn?' warn':'');
  el.textContent=q.exhausted?`FMP today ${q.used}/${q.cap} · ⛔ FMP เกินโควตาวันนี้ — refresh จะใช้ Yahoo/ข้อมูลรอบก่อน (FMP ตัดสิทธิ์จนถึงวันใหม่)`
    :`FMP today ${q.used}/${q.cap} (${q.pct}%) · holdings ${q.holdings} · เหลือ ${q.headroom_calls} call ≈ ${q.headroom_tickers} หุ้น`+(q.reserved?` (${q.reserved} reserved)`:'')+(q.warn?' · ⚠ near quota — add fewer / refresh later':'');
}

function renderPortfolio(d){
  // Allocation and the Risk Desk read this for Portfolio MoS, the story and the cash stance
  const rows=d.rows||[], t=d.totals||{};
  rows.forEach(r=>{r._w=t.market_value&&r.market_value!=null?r.market_value/t.market_value*100:null;});
  window.__last={rows,market:d.market,benchmark:d.benchmark,philosophy:d.philosophy,totals:t};
  renderMomBanner(d.momentum_meta);
  renderAssumptions(d.market);
  renderMarket(d.market);
  renderBenchmark(d.benchmark);
  renderPhilosophy(d.philosophy);
  renderSynth(d);
  const p1=document.getElementById('p1');
  if(!rows.length){p1.innerHTML='<div class="empty"><b>ยังไม่มีหุ้นในพอร์ต</b><span>เปิด "เครื่องมือ" ด้านบนแล้วเพิ่ม ticker + จำนวนหุ้น + ต้นทุน</span></div>';return;}
  p1.innerHTML=summaryBand(rows,t,d.benchmark,d.philosophy)+cardToolbar(rows.length);
  drawCards();
}
async function saveCash(){
  const v=document.getElementById('cashIn').value, sp=document.getElementById('spCash'), msg=document.getElementById('addMsg');
  if(v===''||Number(v)<0){msg.textContent='ใส่ยอด cash เป็น USD (0 ขึ้นไป)';return;}
  sp.style.display='inline-block';
  try{
    const r=await fetch('/api/cash?usd='+encodeURIComponent(v),{method:'POST'}); const d=await r.json();
    if(!r.ok){msg.textContent='✗ '+((d&&(d.detail||d.error))||r.status);return;}
    renderPortfolio(d.view); __riskCache=null; msg.textContent='✓ cash $'+Number(v).toLocaleString()+' saved';
    document.getElementById('cashIn').value='';
  }catch(e){msg.textContent='✗ '+e}finally{sp.style.display='none'}
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
