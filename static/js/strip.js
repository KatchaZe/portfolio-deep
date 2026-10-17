// shown only while momentum is stale or missing
function renderMomBanner(mm){
  const el=document.getElementById('momBanner'); if(!el)return;
  if(!mm||!mm.stale){el.style.display='none';return;}
  const age=mm.age_days==null?'':` (${mm.age_days} days old)`;
  const asof=mm.as_of?`as of <b>${mm.as_of}</b>${age}`:'<b>not yet computed</b>';
  const extra=mm.stale_count>1?` · ${mm.stale_count} holdings outdated`:'';
  el.style.cssText='display:block;margin:8px 0;padding:8px 12px;border-radius:8px;font-size:calc(12px*var(--fs));'
    +'background:#fef3c7;color:#92400e;border:1px solid #fde68a';
  el.innerHTML=`&#9888; Momentum ${asof}${extra} — click <b>&#9654; Run Daily (price/momentum)</b> to refresh.`;
}
// Command strip: four tiles (regime S9, market value S32/33, vs S&P S16/S35, philosophy
// S1/S42) and one line that turns them into today's action.
function stripTile(label,val,sub,bg,bd,col){return `<div style="background:${bg||'#f8fafc'};border:1px solid ${bd||'#e2e8f0'};border-radius:10px;padding:8px 11px"><div style="${TILE_LABEL}">${label}</div><div style="font-size:calc(14px*var(--fs));font-weight:800;color:${col||'#0f172a'}">${val}</div><div class="muted" style="font-size:calc(10px*var(--fs));margin-top:1px;line-height:1.5">${sub||''}</div></div>`;}
// the strip slot for one renderer: reveals the strip, lets the tiles join its grid
function stripSlot(id){
  const el=document.getElementById(id); if(!el)return null;
  const w=document.getElementById('cmdStrip'); if(w)w.style.display='grid';
  el.style.cssText='display:contents';
  return el;
}
function renderMarket(mk){
  const el=stripSlot('mktRegime'); if(!el)return;
  if(!mk||!mk.regime){el.innerHTML=stripTile('Regime (S9)','&mdash;','กด &#9654; Run Daily เพื่อคำนวณ')+stripTile('Market value (S32/33)','&mdash;','ต้องมี regime ก่อน');return;}
  const off=mk.regime==='risk_off';
  const dist=(mk.dist_pct!=null)?`${signed(mk.dist_pct)}% vs 200-DMA`:'';
  const t1=stripTile('Regime (S9)',off?'&#9888; RISK-OFF':'&#9679; RISK-ON',
    dist+(off?' · momentum crash-prone — อย่าไล่ราคา':' · สัญญาณ momentum เชื่อถือได้ขึ้น'),
    off?'#fef2f2':'#f0fdf4',off?'#fecaca':'#bbf7d0',off?'#991b1b':'#166534');
  const v=mk.valuation||{}; let t2;
  if(v.regime){
    const col={cheap:'#166534',fair:'#475569',expensive:'#991b1b'}[v.regime]||'#475569';
    t2=stripTile('Market value (S32/33)',v.regime.toUpperCase(),
      `implied ERP ${v.implied_erp_pct}% · E/P ${v.earnings_yield_pct}% · Fed spread ${v.fed_spread_pct}% &rarr; ${esc(v.tilt||'')} <span style="color:#cbd5e1">(tilt ไม่ใช่สวิตช์เข้าออก)</span>`
      +(v.rf_live===false?` <span style="color:#b45309;font-weight:700" title="ดึงอัตรา 10Y ไม่ได้ จึงใช้ค่าสำรอง ${v.rf_pct}% — Fed spread อาจคลาดจากวันนี้">&#9888; Rf สำรอง ${v.rf_pct}%</span>`:''),
      '#f8fafc','#e2e8f0',col);
  }else t2=stripTile('Market value (S32/33)','&mdash;','ERP/PE ยังคำนวณไม่ได้ — เช็ค config');
  el.innerHTML=t1+t2;
}
function renderBenchmark(b){
  const el=stripSlot('benchmark'); if(!el)return;
  if(!b||(b.portfolio_12m==null&&b.market_12m==null)){el.innerHTML=stripTile('vs S&P 12M (S16/S35)','&mdash;','กด &#9654; Run Daily เพื่อคำนวณ');return;}
  const fmt=v=>v==null?'—':signed(v)+'%';
  const ex=b.excess_pp, lag=ex!=null&&ex<0;
  el.innerHTML=stripTile('vs S&P 12M (S16/S35)',ex==null?'&mdash;':signed(ex)+' pp',
    `พอร์ต ${fmt(b.portfolio_12m)} vs S&amp;P ${fmt(b.market_12m)} · MV-weighted, illustrative — active ส่วนใหญ่แพ้ index หลังหัก cost/tax`,
    '#f8fafc','#e2e8f0',lag?'#b45309':'#166534');
}
function renderPhilosophy(p){
  const el=stripSlot('philosophy'); if(!el)return;
  if(!p||!p.running){el.innerHTML=stripTile('Philosophy (S1/S42)','&mdash;','ยังไม่มีข้อมูลพอร์ต');return;}
  const pr=p.profile||{};
  const sub=`avg DEEP ${p.avg_composite??'—'} · momentum ${p.strong_momentum_pct}% · GARP ${p.garp_candidates} — profile <b>${esc(pr.role||'?')}</b>/${esc(pr.horizon||'?')}/${esc(pr.tax_status||'?')}`
    +((p.notes&&p.notes.length)?` · <span style="color:#991b1b">${esc(p.notes.join(' · '))}</span>`:'')
    +` <span style="color:#cbd5e1">(แก้ profile: data/philosophy_profile.json)</span>`;
  el.innerHTML=stripTile('Philosophy (S1/S42)',(p.fit_ok?'&#10003; ':'&#9888; ')+esc(p.running),sub,'#faf5ff','#e9d5ff',p.fit_ok?'#4b3869':'#991b1b');
}
// S42: the four tiles read as one sentence, "what to do today"
function renderSynth(d){
  const el=document.getElementById('stripSynth'); if(!el)return;
  const mk=(d&&d.market)||{}, v=mk.valuation||{}, b=(d&&d.benchmark)||{}, p=(d&&d.philosophy)||{};
  if(!mk.regime && !p.running){el.style.display='none';return;}
  const parts=[];
  if(mk.regime)parts.push(mk.regime==='risk_off'?'<b>ตลาด RISK-OFF</b> — crash guard เปิด ห้ามไล่ราคา':'ตลาด RISK-ON');
  if(v.regime==='fair')parts.push('valuation FAIR &rarr; <b>ไม่มี tilt</b>');
  else if(v.regime==='cheap')parts.push('<b>ตลาดถูก</b> &rarr; '+esc(v.tilt||''));
  else if(v.regime==='expensive')parts.push('<b>ตลาดแพง</b> &rarr; '+esc(v.tilt||''));
  if(p.running)parts.push(p.fit_ok?'พอร์ตตรงปรัชญา &rarr; เดินตามสัญญาณ DEEP ปกติ':'<b>&#9888; พอร์ตเพี้ยนจาก profile</b> — ทบทวนก่อนเทรดใหม่ (S42)');
  if(b.excess_pp!=null)parts.push('vs index '+signed(b.excess_pp)+'pp'+(b.excess_pp<0?' — อย่าเพิ่ม turnover เพื่อไล่ตาม (S6/S35)':''));
  el.style.cssText='display:block;background:#0f172a;color:#e2e8f0;border-radius:8px;padding:7px 12px;font-size:calc(12px*var(--fs));margin:0 0 8px';
  el.innerHTML='&#129517; <b>วันนี้:</b> '+parts.join(' · ');
}
