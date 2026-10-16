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
    const r=await fetch('/api/allocation/whatif',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(items)});
    const d=await r.json();
    makePie('pieTickerAfter',(d.after&&d.after.by_ticker)||[]);makePie('pieSectorAfter',(d.after&&d.after.by_sector)||[]);
    document.getElementById('afterWrap').style.display='block';
    if(d.quota)renderQuota(d.quota);
    document.getElementById('whatifMsg').textContent='Added: '+(d.added||[]).map(a=>a.ticker+' $'+a.amount).join(', ');
  }catch(e){document.getElementById('whatifMsg').textContent='Error: '+e}
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
  el.innerHTML=`&#129517; Cash stance (S32/33): <b>${m2[0]}</b> — จาก market valuation <b>${esc(v.regime.toUpperCase())}</b> · เป็น tilt เบา ๆ ผ่าน allocation ไม่ใช่สวิตช์เข้า-ออกตลาด (S34 บอกว่าแบบนั้นล้มเหลว)`;
}
