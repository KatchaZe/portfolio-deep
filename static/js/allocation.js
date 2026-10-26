// The cost-basis pies, the what-if calculator and the cash stance on the Allocation tab.
function buildWhatifInputs(){
  let h='';for(let i=0;i<5;i++)h+=`<div style="display:flex;gap:6px;margin:3px 0"><input class="wf-t" placeholder="TICKER" style="width:90px;text-transform:uppercase;padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:calc(12px*var(--fs))"><input class="wf-a" type="number" step="any" placeholder="buy amount $" style="width:130px;padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:calc(12px*var(--fs))"></div>`;
  document.getElementById('whatifInputs').innerHTML=h;
}
async function loadAllocation(){
  if(!document.getElementById('whatifInputs').innerHTML)buildWhatifInputs();
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
  if(r.error)return `<div class="down" style="font-size:calc(12px*var(--fs))">คำนวณความเสี่ยงหลังซื้อไม่ได้: ${esc(r.error)}</div>`;
  const b=r.before||{},a=r.after||{},dd=r.delta||{};
  const row=(lab,k,unit,goodDown)=>{const v=k==='port_beta'&&a[k]!=null&&b[k]!=null?Math.round((a[k]-b[k])*1000)/1000:dd[k];const col=v==null||v===0?'#475569':((v<0)===goodDown?'#166534':'#991b1b');
    return `<tr><td class="l">${lab}</td><td>${b[k]??'—'}${unit}</td><td>${a[k]??'—'}${unit}</td><td style="color:${col};font-weight:700">${v==null?'—':(v>0?'+':'')+v+unit}</td></tr>`;};
  const names=(r.names||[]).map(n=>`<tr><td class="l"><b>${esc(n.ticker)}</b></td><td>${pctFixed(n.capital_pct)}</td><td>${pctFixed(n.risk_pct)}</td><td>${esc(n.rating||'—')}</td><td>${n.net_mos_pct==null?'—':n.net_mos_pct+'%'}</td><td class="l">${n.add_blocker?'⚠ '+esc(n.add_blocker):'✓ ผ่านเกณฑ์ MoS'}${n.over_pricing_cap?' · ⚠ pricing asset เกินเพดาน 5% (S40-41)':''}${n.proxy_history?' · ประวัติราคาไม่พอ (proxy)':''}</td></tr>`).join('');
  return `<div style="margin-top:12px;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;background:#fff">
    <div style="font-size:calc(13px*var(--fs));font-weight:700">ความเสี่ยงหลังซื้อ (S2: ดูที่สิ่งที่ตัวใหม่เพิ่มให้พอร์ต) <span class="muted" style="font-weight:600;font-size:calc(10px*var(--fs))">· ตามมูลค่าตลาด (พายด้านบนคิดตามต้นทุน จึงไม่เท่ากัน)</span></div>
    <div class="muted" style="font-size:calc(10px*var(--fs));margin-bottom:6px">${esc(r.basis||'')}${(r.unpriced||[]).length?' · ไม่มีราคา: '+r.unpriced.map(esc).join(', '):''}</div>
    <table style="font-size:calc(12px*var(--fs))"><thead><tr><th class="l"></th><th>ก่อน</th><th>หลัง</th><th>Δ</th></tr></thead><tbody>
      ${row('Vol ทั้งพอร์ต','port_vol_pct','%',true)}${row('ENB (normal)','enb','',false)}${row('ENB (crisis)','enb_crisis','',false)}
      ${row('Stress แย่สุด','worst_stress_pct','%',false)}${row('VaR 95%','var95_pct','%',true)}${row('β','port_beta','',true)}${row('Cash','cash_pct','%',false)}
    </tbody></table>
    ${names?`<table style="font-size:calc(12px*var(--fs));margin-top:8px"><thead><tr><th class="l">ตัวที่ซื้อ</th><th>%เงิน</th><th>%ความเสี่ยง</th><th>Rating</th><th>MoS หลังหัก</th><th class="l">เกณฑ์</th></tr></thead><tbody>${names}</tbody></table>`:''}</div>`;
}
// S32/33: the market overlay turned into a light allocation tilt
function renderCashStance(){
  const el=document.getElementById('cashStance'); if(!el)return;
  const v=((window.__last||{}).market||{}).valuation||{};
  if(!v.regime){el.style.display='none';return;}
  const map={cheap:['&#8593; เพิ่มการลงทุน (ตลาดถูกเทียบความเสี่ยง)','#166534','#f0fdf4','#bbf7d0'],
             fair:['= คงเดิม — ไม่มี timing tilt','#475569','#f8fafc','#e2e8f0'],
             expensive:['&#8595; เพิ่มสัดส่วน cash / ลด high-beta','#991b1b','#fef2f2','#fecaca']};
  const m2=map[v.regime]; if(!m2){el.style.display='none';return;}
  el.style.cssText=`display:block;margin:2px 0 10px;padding:7px 11px;border-radius:8px;font-size:calc(12px*var(--fs));background:${m2[2]};border:1px solid ${m2[3]};color:${m2[1]}`;
  const tt=(window.__last||{}).totals||{};
  const have=tt.cash?` · cash ตอนนี้ <b>${tt.cash_pct}%</b> ของพอร์ต (${money(tt.cash)})`:' · ยังไม่ได้กรอก cash ใน My Portfolio';
  el.innerHTML=`&#129517; Cash stance (S32/33): <b>${m2[0]}</b> — จาก market valuation <b>${esc(v.regime.toUpperCase())}</b>${have} · เป็น tilt เบา ๆ ผ่าน allocation ไม่ใช่สวิตช์เข้า-ออกตลาด (S34 บอกว่าแบบนั้นล้มเหลว)`+
    (v.implied_erp_pct!=null&&v.erp_low_pct!=null?`<div style="opacity:.8;margin-top:2px">ERP วันนี้ <b>${v.implied_erp_pct}%</b> · ต่ำกว่า ${v.erp_low_pct}% = แพง · สูงกว่า ${v.erp_high_pct}% = ถูก <span title="${esc(v.band_source||'')}">(${esc(v.band_source||'')})</span>`+
     (v.market_pe!=null?` · S&amp;P P/E <b>${v.market_pe}</b> ${v.market_pe_live?'(SPY, Yahoo)':(v.market_pe_live_date?`(ค่าล่าสุดที่ดึงได้ ${esc(v.market_pe_live_date)} — ดึง SPY ไม่ได้)`:'(ค่าที่บันทึกไว้ — ดึง SPY ไม่ได้)')}`:'')+`</div>`:'');
}
