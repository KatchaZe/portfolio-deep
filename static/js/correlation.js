// ===================== CORRELATION MONITOR (tab6, additive — new fns only) =====================
let __corr=null, corrMode='normal';
async function loadCorr(force){
  const body=document.getElementById('corrBody'); if(!body)return;
  const sp=document.getElementById('spCorr'); if(sp)sp.style.display='inline-block';
  const en=(document.getElementById('corrEnrich')||{}).value||'auto';
  body.innerHTML='<div class="muted" style="font-size:calc(12px*var(--fs));padding:10px">Computing correlation… (price history + covariance)</div>';
  try{
    const d=await fetchRiskPayload(en,!!force);   // shared with Risk Desk — no second /api/risk build
    __corr=d; if(d.quota)renderQuota(d.quota); renderCorr(d);
  }catch(e){body.innerHTML='<div class="down" style="padding:10px;font-size:calc(12px*var(--fs))">Error: '+esc(String(e))+'</div>';}
  finally{if(sp)sp.style.display='none';}
}
function setCorrMode(m){corrMode=m;
  const a=document.getElementById('cmNormal'),b=document.getElementById('cmCrisis');
  if(a){a.style.background=m==='normal'?'#0d9488':'#fff';a.style.color=m==='normal'?'#fff':'#475569';}
  if(b){b.style.background=m==='crisis'?'#dc2626':'#fff';b.style.color=m==='crisis'?'#fff':'#475569';}
  if(__corr)renderCorr(__corr);
}
function corrColor(v){
  if(v==null)return['#e2e8f0','#94a3b8']; if(v<0)return['#0d9488','#fff'];
  if(v>=0.9)return['#b91c1c','#fff']; if(v>=0.8)return['#dc2626','#fff']; if(v>=0.65)return['#f97316','#fff'];
  if(v>=0.5)return['#f59e0b','#3b2a00']; if(v>=0.35)return['#eab308','#3b2a00']; if(v>=0.2)return['#84cc16','#1a2e05'];
  return['#22c55e','#052e16'];
}
function corrCell(v){const c=corrColor(v);return `<td style="background:${c[0]};color:${c[1]}" title="${plainNum(v,2)} (${corrMode})">${plainNum(v,2)}</td>`;}

function renderCorr(d){
  const body=document.getElementById('corrBody'); if(!body)return;
  if(!d||d.status==='insufficient'){body.innerHTML='<div class="muted" style="padding:10px;font-size:calc(12px*var(--fs))">'+esc((d&&d.message)||'ต้องมี ≥2 holding ที่มีราคา — เพิ่ม holding หรือกด Run Daily ก่อน')+'</div>';return;}
  const co=d.correlation;
  if(!co||co.status==='error'){body.innerHTML='<div class="down" style="padding:10px;font-size:calc(12px*var(--fs))">Correlation unavailable: '+esc((co&&co.message)||'—')+'</div>';return;}
  // heatmap gets the FULL page width (all holdings visible without scrolling), summaries sit underneath
  body.innerHTML = corrProxyWarnHTML(co) + corrPhilHTML(co) + corrHeatHTML(co)
    + `<div class="cqgrid">${corrSectorHTML(co,d)}${corrPairsHTML(co)}</div>`
    + corrBenchHTML(co) + corrLegendHTML(co);
}

function corrProxyBadge(co){
  const cm=((co||{}).meta||{}).cov_mode;
  if(!cm||cm==='realized')return '';
  return `<span class="tag" style="background:#d97706;margin-right:4px" title="history ไม่พอ — ใช้ค่าสมมติ equity↔equity 0.60 / ข้าม class 0.20">${cm==='proxy'?'PROXY':'MIXED'}</span>`;
}
function corrProxyWarnHTML(co){
  const m=(co||{}).meta||{}; const cm=m.cov_mode;
  if(!cm||cm==='realized')return '';
  const miss=(m.proxy_tickers||[]).map(esc).join(', ');
  const what=cm==='proxy'?'<b>ทั้งตาราง</b>เป็นค่าสมมติ (ดึงราคาย้อนหลังไม่ได้เลย)':'บางคู่เป็นค่าสมมติ (history ไม่ครบ)';
  return `<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:10px;padding:8px 12px;margin-bottom:8px;font-size:calc(12px*var(--fs));color:#92400e">&#9888; <b>${cm==='proxy'?'PROXY':'MIXED'} — ประมาณการ ไม่ใช่ correlation ที่คำนวณจริง</b> · ${what} · คู่สมมติ: equity↔equity 0.60 / ข้าม class 0.20${miss?' · ขาด history: '+miss:''} · กด Run correlation ใหม่ได้ — ระบบจะ retry เฉพาะตัวที่ขาด</div>`;
}

function corrPhilHTML(co){
  const ph=co.philosophy||{}, g=ph.gauge||{}, mode=corrMode;
  const enb=(mode==='crisis'?g.enb_crisis:g.enb), ratio=(g.eff_n&&enb)?(enb/g.eff_n):null;
  const rc=ratio==null?['#94a3b8','—']:ratio>=0.7?['#16a34a','ดี']:ratio>=0.5?['#d97706','เฝ้าระวัง']:['#dc2626','กระจุก'];
  const ap=(co.avg_pairwise||{})[mode], dwn=((co.benchmark||{}).downside||{}).downside;
  const story=mode==='crisis'?ph.story_crisis:ph.story_normal, frag=g.fragility_pct;
  const pills=(ph.pillars||[]).map(p=>`<div class="cqpill"><div style="font-size:calc(11px*var(--fs));font-weight:700">${esc(p.name)} <span class="cqchip" style="background:#ede9fe;color:#6d28d9">${esc(p.s)}</span></div><div style="font-size:calc(11px*var(--fs));margin-top:2px">${esc(p.value)}</div><div class="muted" style="font-size:calc(9px*var(--fs))">${esc(p.note)}</div></div>`).join('');
  const mg=(co.marginal||[]); let a1='';
  if(mg.length){a1=`<div class="cqlbl" style="margin-top:12px">A1 · %Capital vs %Risk (S2/S4 marginal) · แดง = %risk เกิน %capital</div>
    <div style="overflow-x:auto;margin-top:4px"><table class="cqhm"><tr><th class="row"></th>${mg.map(r=>`<th class="col">${esc(r.ticker)}</th>`).join('')}</tr>
    <tr><th class="row" style="font-size:calc(9px*var(--fs))">%Cap</th>${mg.map(r=>`<td style="background:#94a3b8;color:#fff">${plainNum(r.cap_pct,0)}</td>`).join('')}</tr>
    <tr><th class="row" style="font-size:calc(9px*var(--fs))">%Risk</th>${mg.map(r=>{const drv=(r.risk_pct||0)>(r.cap_pct||0);return `<td style="background:${drv?'#dc2626':'#16a34a'};color:#fff" title="%risk ${plainNum(r.risk_pct,0)} vs %cap ${plainNum(r.cap_pct,0)}">${plainNum(r.risk_pct,0)}</td>`;}).join('')}</tr></table></div>`;}
  return `<div class="cqcard" style="border-color:#ddd6fe">
    <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px">
      <div><b style="font-size:calc(13px*var(--fs))">&#129517; Diversification Philosophy</b> <span class="muted" style="font-size:calc(10px*var(--fs))">· ราย "พอร์ต" ตามเสา Damodaran — คู่กับ VALUE→QUALITY→TIMING ราย "หุ้น" ที่ Allocation</span></div>
      <span class="tag" style="background:${mode==='crisis'?'#dc2626':'#0d9488'}">${mode.toUpperCase()}</span></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
      <div class="cqrcard" style="min-width:calc(150px*var(--fs))"><div class="cqlbl">ENB ÷ Eff N (${mode})</div><b style="color:${rc[0]};font-size:calc(20px*var(--fs))">${plainNum(ratio,2)}</b> <span style="color:${rc[0]};font-size:calc(11px*var(--fs))">${rc[1]}</span><div class="muted" style="font-size:calc(9px*var(--fs))">ENB ${plainNum(enb,1)} / Eff N ${plainNum(g.eff_n,1)}</div></div>
      <div class="cqrcard" style="min-width:calc(160px*var(--fs))"><div class="cqlbl">Fragility ΔENB · N→Crisis</div><b style="color:#dc2626;font-size:calc(20px*var(--fs))">${plainNum(g.enb,1)}→${plainNum(g.enb_crisis,1)}</b><div class="muted" style="font-size:calc(9px*var(--fs))">${g.fragility_text||(frag!=null?('การกระจายหาย ~'+frag+'% ตอนวิกฤต'):'corr วิ่งเข้า 1')}</div></div>
      <div class="cqrcard" style="min-width:calc(150px*var(--fs))"><div class="cqlbl">avg corr (${mode})</div><b style="font-size:calc(20px*var(--fs))">${plainNum(ap,2)}</b><div class="muted" style="font-size:calc(9px*var(--fs))">downside corr ${plainNum(dwn,2)} · S4</div></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(calc(210px*var(--fs)),1fr));gap:8px;margin-top:10px">${pills}</div>
    <div class="cqleg" style="background:#f5f3ff;border-color:#ddd6fe;color:#4b3869;margin-top:10px">${esc(story||'')}</div>${a1}</div>`;
}

function corrHeatHTML(co){
  const order=co.order||[], mode=corrMode, mat=(mode==='crisis'?co.crisis_matrix:co.matrix)||[], secs=co.sectors||{};
  let h=`<table class="cqhm"><tr><th></th>`;
  order.forEach((c,j)=>{const sep=(j>0&&secs[c]!==secs[order[j-1]])?'border-left:2px solid #0f172a22':'';h+=`<th class="col" style="${sep}">${esc(c)}</th>`;});
  h+='</tr>';
  order.forEach((r,i)=>{
    const sepR=(i>0&&secs[r]!==secs[order[i-1]])?'border-top:2px solid #0f172a22':'';
    h+=`<tr><th class="row" style="${sepR}" title="${esc(secs[r]||'')}">${esc(r)}</th>`;
    order.forEach((c,j)=>{const v=(mat[i]||[])[j]; const sc=corrColor(v); const bd=(j>0&&secs[c]!==secs[order[j-1]])?'border-left:2px solid #0f172a22;':'';
      const isD=(i===j); const bg=isD?'#0f172a':sc[0], fg=isD?'#fff':sc[1];
      h+=`<td style="${bd}${sepR?'border-top:2px solid #0f172a22;':''}background:${bg};color:${fg}" title="${esc(r)} × ${esc(c)} = ${plainNum(v,2)} (${mode})">${plainNum(v,2)}</td>`;});
    h+='</tr>';
  });
  h+='</table>';
  return `<div class="cqcard"><div style="display:flex;justify-content:space-between;align-items:baseline"><div><b style="font-size:calc(13px*var(--fs))">Correlation Heatmap — รายตัว</b> <span class="muted" style="font-size:calc(10px*var(--fs))">(จัดกลุ่มตาม sector)</span></div><span>${corrProxyBadge(co)}<span class="tag" style="background:${mode==='crisis'?'#dc2626':'#0d9488'}">${mode.toUpperCase()}</span></span></div>
    <div style="overflow-x:auto;margin-top:8px">${h}</div>
    <div style="display:flex;align-items:center;gap:0;margin-top:8px;font-size:calc(10px*var(--fs));color:#64748b"><span>ต่ำ</span>
      <i style="width:24px;height:11px;display:inline-block;background:#22c55e"></i><i style="width:24px;height:11px;display:inline-block;background:#84cc16"></i><i style="width:24px;height:11px;display:inline-block;background:#eab308"></i><i style="width:24px;height:11px;display:inline-block;background:#f59e0b"></i><i style="width:24px;height:11px;display:inline-block;background:#f97316"></i><i style="width:24px;height:11px;display:inline-block;background:#dc2626"></i>
      <span>สูง</span><span class="muted" style="margin-left:auto">hover ดูค่า · เขียวเข้ม=corr ลบ (diversifier)</span></div></div>`;
}

function corrSectorHTML(co,d){
  const mode=corrMode, sa=((co.sector_avg||{})[mode])||{}, bysec=((d.allocation||{}).by_sector)||[];
  const rows=bysec.map(s=>{const a=(sa[s.label]||{}).avg, w=s.value;
    const ac=a==null?'#94a3b8':a>=0.7?'#dc2626':a>=0.5?'#d97706':'#16a34a', wc=w>=45?'#dc2626':w>=30?'#d97706':'#2563eb';
    return `<div style="display:flex;align-items:center;gap:8px;margin:6px 0"><div style="width:calc(110px*var(--fs));flex:none;font-size:calc(12px*var(--fs));font-weight:600">${esc(s.label)}</div>
      <div style="flex:1;background:#eef2f7;border-radius:6px;height:16px;overflow:hidden"><div style="height:100%;border-radius:6px;width:${Math.min(w,100)}%;background:${wc}"></div></div>
      <div style="width:calc(120px*var(--fs));flex:none;text-align:right;font-size:calc(11px*var(--fs))"><b>${plainNum(w,1)}%</b> · <span style="color:${ac}">corr ${a==null?'—':plainNum(a,2)}</span></div></div>`;}).join('');
  return `<div class="cqcard"><b style="font-size:calc(13px*var(--fs))">Sector Summary</b><div class="muted" style="font-size:calc(10px*var(--fs));margin-bottom:6px">แถบ = %น้ำหนักพอร์ต · ขวา = avg corr ในกลุ่ม</div>${rows||'<span class="muted" style="font-size:calc(11px*var(--fs))">—</span>'}</div>`;
}

function corrPairsHTML(co){
  const mode=corrMode, p=((co.pairs||{})[mode])||{high:[],low:[]};
  const row=x=>{const c=corrColor(x[2]);return `<tr><td style="padding:4px 6px;border-top:.5px solid #e2e8f0"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;background:${c[0]}"></span>${esc(x[0])}–${esc(x[1])}</td><td style="padding:4px 6px;border-top:.5px solid #e2e8f0;text-align:right;font-weight:700">${plainNum(x[2],2)}</td></tr>`;};
  return `<div class="cqcard"><b style="font-size:calc(13px*var(--fs))">คู่สัมพันธ์ สูงสุด / ต่ำสุด</b><div class="muted" style="font-size:calc(10px*var(--fs));margin-bottom:4px">คู่ต่ำ = ตัวช่วยกระจายจริง</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><div><div class="cqlbl" style="color:#dc2626">สูงสุด</div><table style="width:100%;border-collapse:collapse;font-size:calc(12px*var(--fs))">${(p.high||[]).map(row).join('')}</table></div>
    <div><div class="cqlbl" style="color:#16a34a">ต่ำสุด</div><table style="width:100%;border-collapse:collapse;font-size:calc(12px*var(--fs))">${(p.low||[]).map(row).join('')}</table></div></div></div>`;
}

function corrBenchHTML(co){
  const b=co.benchmark; if(!b)return '';
  const mode=corrMode, refs=b.refs||[], order=co.order||[];
  const pv=(o)=>o?(mode==='crisis'?o.crisis:o.normal):null;
  const chips=refs.map(r=>{const v=pv((b.portfolio||{})[r.sym]); const c=corrColor(v);
    return `<div style="flex:1 1 130px;min-width:130px;background:${c[0]};color:${c[1]};border-radius:10px;padding:8px 12px" title="พอร์ต × ${esc(r.name)} = ${plainNum(v,2)} (${mode})"><div style="font-size:calc(10px*var(--fs));opacity:.9">${esc(r.name)} · ${esc(r.sym)}</div><b style="font-size:calc(19px*var(--fs))">${plainNum(v,2)}</b></div>`;}).join('');
  let hb=`<table class="cqhm"><tr><th></th>${refs.map(r=>`<th class="col">${esc(r.sym)}</th>`).join('')}</tr>`;
  order.forEach(t=>{hb+=`<tr><th class="row">${esc(t)}</th>${refs.map(r=>corrCell(pv(((b.holdings||{})[t]||{})[r.sym])) ).join('')}</tr>`;});
  hb+='</table>';
  let rr=`<table class="cqhm"><tr><th></th>${refs.map(r=>`<th class="col">${esc(r.sym)}</th>`).join('')}</tr>`;
  refs.forEach(a=>{rr+=`<tr><th class="row">${esc(a.sym)}</th>`+refs.map(bb=>{if(a.sym===bb.sym)return '<td style="background:#0f172a;color:#fff">1.00</td>';const key=(b.refref||{})[a.sym+'-'+bb.sym]||(b.refref||{})[bb.sym+'-'+a.sym];return corrCell(pv(key));}).join('')+'</tr>';});
  rr+='</table>';
  return `<div class="cqcard"><div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px"><div><b style="font-size:calc(13px*var(--fs))">Benchmark &amp; Asset-Class Correlation</b> <span class="muted" style="font-size:calc(10px*var(--fs))">· S&amp;P 500 · Nasdaq-100 · Gold · Bitcoin(IBIT)</span></div><span class="tag" style="background:${mode==='crisis'?'#dc2626':'#0d9488'}">${mode.toUpperCase()}</span></div>
    <div class="cqlbl" style="margin-top:10px">A · พอร์ตทั้งก้อน vs benchmark</div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">${chips}</div>
    <div class="cqgrid" style="margin-top:12px"><div><div class="cqlbl">B · แต่ละหุ้น vs benchmark</div><div style="overflow-x:auto;margin-top:4px">${hb}</div></div>
    <div><div class="cqlbl">C · สินทรัพย์ vs สินทรัพย์</div><div style="overflow-x:auto;margin-top:4px">${rr}</div></div></div>
    <div class="cqleg" style="background:#fffbeb;border-color:#fde68a;color:#78350f;margin-top:12px"><b>อ่านการ์ดนี้</b> — Crisis floor เฉพาะคู่ equity↔equity (รวม SPY/QQQ/IBIT) เข้า ~0.9 · <b>Gold ไม่ถูก floor</b> (non-equity) → ถ้ายังต่ำตอน Crisis = ตัวถ่วงจริง · IBIT = spot-BTC ETF (แทน BTC-USD, ปฏิทินตรงวันหุ้น) และถูก floor เพราะ engine นับ crypto เป็น equity-beta ตอนวิกฤต.</div></div>`;
}

function corrLegendHTML(co){
  const roll=((co.rolling||{}).port_vs_spy)||[]; let spark='';
  if(roll.length>2){const w=180,h=28,mn=Math.min(...roll),mx=Math.max(...roll),rg=(mx-mn)||1;
    const pts=roll.map((v,i)=>`${(i/(roll.length-1)*w).toFixed(1)},${(h-((v-mn)/rg)*h).toFixed(1)}`).join(' ');
    spark=`<div style="margin-top:6px"><span class="muted" style="font-size:calc(11px*var(--fs))">A7 · Rolling 60d corr พอร์ต vs S&amp;P (${plainNum(roll[0],2)}→${plainNum(roll[roll.length-1],2)}):</span>
      <svg width="${w}" height="${h}" style="vertical-align:middle;margin-left:6px"><polyline points="${pts}" fill="none" stroke="#2563eb" stroke-width="1.5"/></svg>
      <span class="muted" style="font-size:calc(10px*var(--fs))"> ${roll[roll.length-1]>roll[0]?'▲ กำลังไต่ขึ้น (เตือน)':'▼/flat'}</span></div>`;}
  return `<div class="cqleg" style="background:#f1f5f9;border-color:#e2e8f0;color:#334155;margin-top:2px">
    <b>Damodaran S2–4:</b> heatmap = marginal risk เชิงภาพ (ยิ่งแดง=วิ่งตามกัน) · ปุ่ม <b>Crisis</b> = correlation วิ่งเข้า ~0.9 ตอนตลาดพัง (การกระจายหาย) · ENB ต่ำกว่า Eff N เพราะ correlation กินการกระจาย.${spark}</div>`;
}
