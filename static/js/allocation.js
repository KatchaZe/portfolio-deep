// The Allocation tab outside the Risk Desk: money per holding against the whole book, the
// cost-basis doughnuts, the what-if calculator and the cash stance.
function buildWhatifInputs(){
  let h='';for(let i=0;i<5;i++)h+=`<div class="wirow"><label class="sr" for="wft${i}">ticker ${i+1}</label><input id="wft${i}" class="wf-t inp ucase" placeholder="TICKER" autocomplete="off"><label class="sr" for="wfa${i}">จำนวนเงิน ${i+1}</label><input id="wfa${i}" class="wf-a inp" type="number" step="any" placeholder="buy amount $"></div>`;
  document.getElementById('whatifInputs').innerHTML=h;
}
// %-of-portfolio per holding (market value, from the My Portfolio payload) and by sector
function renderAllocTop(){
  const el=document.getElementById('allocTop'); if(!el)return;
  const L=window.__last||{}, rows=[...(L.rows||[])].filter(r=>r.market_value!=null).sort((a,b)=>b.market_value-a.market_value), t=L.totals||{};
  if(!rows.length){el.innerHTML='';return;}
  const tot=t.total_value||t.market_value||rows.reduce((s,r)=>s+r.market_value,0), top=rows[0].market_value/tot*100;
  const bars=rows.map(r=>{const w=r.market_value/tot*100;return `<button type="button" class="wb" data-go="${esc(r.ticker)}" aria-label="${esc(r.ticker)} ${nf(w,1)}% ${usd(r.market_value,0)} ไปที่การ์ด"><span class="num" style="font-weight:600">${esc(r.ticker)}</span><span class="t"><span class="f" style="width:${(w/top*100).toFixed(1)}%;background:var(${RECO_COLOR[r.recommendation]||'--muted'})"></span></span><span class="num">${usd(r.market_value,0)}</span><b class="num">${nf(w,1)}%</b></button>`;}).join('');
  const bySec={}; rows.forEach(r=>{const k=r.sector||'อื่น ๆ';bySec[k]=(bySec[k]||0)+r.market_value;});
  if(t.cash)bySec['เงินสด']=t.cash;
  const secs=Object.entries(bySec).sort((a,b)=>b[1]-a[1]).map(([k,v])=>({l:k,v:v/tot*100}));
  el.innerHTML=secHTML('A','สัดส่วนเงินรายตัว เทียบทั้งพอร์ต','[CALC] ราคาปิด '+esc(rows[0].price_date||''),
      `<div class="wbars">${bars}</div><p class="note" style="margin:8px 0 0">สีแท่ง = rating (เขียว BUY · น้ำเงิน ACCUMULATE · เหลือง HOLD · แดง SELL) · รวม ${usd(tot)}${t.cash?` (รวมเงินสด ${usd(t.cash)})`:''} · กดชื่อเพื่อไปที่การ์ด</p>`,'s8')
    +secHTML('B','น้ำหนักตาม sector','',hbarsHTML(secs,{label:'น้ำหนักตาม sector'}),'s4');
}
async function loadAllocation(){
  if(!document.getElementById('whatifInputs').innerHTML)buildWhatifInputs();
  renderAllocTop();
  try{
    const r=await fetch('/api/allocation');const d=await r.json();
    const bt=(d.before&&d.before.by_ticker)||[];
    document.getElementById('allocEmpty').textContent=bt.length?'':'No holdings with cost yet — add shares + avg cost in My Portfolio first.';
    makePie('pieTicker',bt);makePie('pieSector',(d.before&&d.before.by_sector)||[]);
  }catch(e){document.getElementById('allocEmpty').textContent='⚠ allocation load failed: '+String(e);}
}
async function calcWhatif(){
  const ts=[...document.querySelectorAll('.wf-t')].map(e=>e.value.trim().toUpperCase());
  const as=[...document.querySelectorAll('.wf-a')].map(e=>e.value);
  const items=[];for(let i=0;i<ts.length;i++){if(ts[i]&&as[i])items.push({ticker:ts[i],amount:parseFloat(as[i])});}
  if(!items.length){document.getElementById('whatifMsg').textContent='Enter at least one ticker + amount.';return;}
  document.getElementById('whatifMsg').textContent='Calculating…';
  try{
    const tol=(document.getElementById('riskTol')||{}).value||20, hz=(document.getElementById('riskHorizon')||{}).value||1;
    const r=await fetch(`/api/allocation/whatif?risk_tolerance_pct=${encodeURIComponent(tol)}&horizon_years=${encodeURIComponent(hz)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(items)});
    const d=await r.json();
    makePie('pieTickerAfter',(d.after&&d.after.by_ticker)||[]);makePie('pieSectorAfter',(d.after&&d.after.by_sector)||[]);
    document.getElementById('afterWrap').style.display='block';
    if(d.quota)renderQuota(d.quota);
    document.getElementById('whatifMsg').textContent='Added: '+(d.added||[]).map(a=>a.ticker+' $'+a.amount).join(', ');
    document.getElementById('whatifRisk').innerHTML=whatifRiskHTML(d.risk);
  }catch(e){document.getElementById('whatifMsg').textContent='Error: '+e}
}
// S2: the risk the purchase ADDS (market value, the Risk Desk's own engine), not its standalone risk
function whatifRiskHTML(r){
  if(!r)return '';
  if(r.error)return `<div class="banner tone t-bear">คำนวณความเสี่ยงหลังซื้อไม่ได้: ${esc(r.error)}</div>`;
  const b=r.before||{},a=r.after||{},dd=r.delta||{};
  const row=(lab,k,unit,goodDown)=>{const v=k==='port_beta'&&a[k]!=null&&b[k]!=null?Math.round((a[k]-b[k])*1000)/1000:dd[k];const c=v==null||v===0?'':((v<0)===goodDown?'up':'down');
    return `<tr><th scope="row" class="l" style="color:var(--ink);font-size:var(--t-sm);font-weight:400">${lab}</th><td class="n">${b[k]??'—'}${unit}</td><td class="n">${a[k]??'—'}${unit}</td><td class="n ${c}" style="font-weight:700">${v==null?'—':(v>0?'+':'')+v+unit}</td></tr>`;};
  const names=(r.names||[]).map(n=>`<tr><th scope="row" class="l num" style="color:var(--ink)">${esc(n.ticker)}</th><td class="n">${pctFixed(n.capital_pct)}</td><td class="n">${pctFixed(n.risk_pct)}</td><td>${esc(n.rating||'—')}</td><td class="n">${n.net_mos_pct==null?'—':n.net_mos_pct+'%'}</td><td class="l">${n.add_blocker?'&#9888; '+esc(pp2pct(n.add_blocker)):'&#10003; ผ่านเกณฑ์ MoS'}${n.over_pricing_cap?' · &#9888; pricing asset เกินเพดาน 5% (S40-41)':''}${n.proxy_history?' · ประวัติราคาไม่พอ (proxy)':''}</td></tr>`).join('');
  return `<div class="box" style="margin-top:12px"><h3>ความเสี่ยงหลังซื้อ <span class="note">S2: ดูที่สิ่งที่ตัวใหม่เพิ่มให้พอร์ต · ตามมูลค่าตลาด (doughnut ด้านบนคิดตามต้นทุน จึงไม่เท่ากัน)</span></h3>
    <p class="note" style="margin:0">${esc(r.basis||'')}${(r.unpriced||[]).length?' · ไม่มีราคา: '+r.unpriced.map(esc).join(', '):''}</p>
    <div class="tw" tabindex="0" role="region" aria-label="ความเสี่ยงก่อนและหลังซื้อ"><table><thead><tr><th class="l"><span class="sr">ตัวชี้วัด</span></th><th class="n">ก่อน</th><th class="n">หลัง</th><th class="n">Δ</th></tr></thead><tbody>
      ${row('Vol ทั้งพอร์ต','port_vol_pct','%',true)}${row('ENB (normal)','enb','',false)}${row('ENB (crisis)','enb_crisis','',false)}
      ${row('Stress แย่สุด','worst_stress_pct','%',false)}${row('VaR 95%','var95_pct','%',true)}${row('β','port_beta','',true)}${row('Cash','cash_pct','%',false)}
    </tbody></table></div>
    ${names?`<div class="tw" tabindex="0" role="region" aria-label="ตัวที่ซื้อ"><table><thead><tr><th class="l">ตัวที่ซื้อ</th><th class="n">%เงิน</th><th class="n">%ความเสี่ยง</th><th class="l">Rating</th><th class="n">MoS หลังหัก</th><th class="l">เกณฑ์</th></tr></thead><tbody>${names}</tbody></table></div>`:''}</div>`;
}
// S32/33: the market overlay turned into a light allocation tilt
function renderCashStance(){
  const el=document.getElementById('cashStance'); if(!el)return;
  const v=((window.__last||{}).market||{}).valuation||{};
  if(!v.regime){el.style.display='none';return;}
  const map={cheap:['&#8593; เพิ่มการลงทุน (ตลาดถูกเทียบความเสี่ยง)','t-bull'],
             fair:['= คงเดิม — ไม่มี timing tilt','t-muted'],
             expensive:['&#8595; เพิ่มสัดส่วน cash / ลด high-beta','t-bear']};
  const m2=map[v.regime]; if(!m2){el.style.display='none';return;}
  el.className='banner tone '+m2[1]; el.style.display='block';
  const tt=(window.__last||{}).totals||{};
  const have=tt.cash?` · cash ตอนนี้ <b>${tt.cash_pct}%</b> ของพอร์ต (${money(tt.cash)})`:' · ยังไม่ได้กรอก cash ในเครื่องมือด้านบน';
  el.innerHTML=`<b>Cash stance (S32/33):</b> <b>${m2[0]}</b> — จาก market valuation <b>${esc(v.regime.toUpperCase())}</b>${have} · เป็น tilt เบา ๆ ผ่าน allocation ไม่ใช่สวิตช์เข้า-ออกตลาด (S34 บอกว่าแบบนั้นล้มเหลว)`+
    (v.implied_erp_pct!=null&&v.erp_low_pct!=null?`<div class="note" style="margin-top:2px">ERP วันนี้ <b>${v.implied_erp_pct}%</b> · ต่ำกว่า ${v.erp_low_pct}% = แพง · สูงกว่า ${v.erp_high_pct}% = ถูก <span title="${esc(v.band_source||'')}">(${esc(v.band_source||'')})</span>`+
     (v.market_pe!=null?` · S&amp;P P/E <b>${v.market_pe}</b> ${v.market_pe_live?'(SPY, Yahoo)':(v.market_pe_live_date?`(ค่าล่าสุดที่ดึงได้ ${esc(v.market_pe_live_date)} — ดึง SPY ไม่ได้)`:'(ค่าที่บันทึกไว้ — ดึง SPY ไม่ได้)')}`:'')+`</div>`:'');
}
