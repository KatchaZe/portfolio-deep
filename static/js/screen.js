async function loadScreen(){
  const el=document.getElementById('garpScreen'); if(!el)return;
  try{const r=await fetch('/api/screen'); const d=await r.json(); renderScreen(d.items||[]);}catch(e){el.innerHTML='';}
}
// Tier3: GARP quadrant map (S13/S19) — "ถูก × ดี" เป็นแนวคิด 2 มิติ ต้องเห็นเป็นภาพ
function garpQuadrant(items){
  const pts=(items||[]).filter(i=>i.economics!=null&&i.price!=null);
  if(!pts.length)return '';
  // plot area is INSET inside a padded viewBox so a ticker scoring 5 (top/right edge) never gets clipped
  const PX0=44, PX1=320, PY0=22, PY1=182, VW=344, VH=212, MIDY=(PY0+PY1)/2;
  const X=v=>PX0+Math.max(0,Math.min(5,v))/5*(PX1-PX0), Y=v=>PY1-Math.max(0,Math.min(5,v))/5*(PY1-PY0);
  const x3=X(3), y3=Y(3);
  const clampX=v=>Math.max(PX0,Math.min(PX1,v)), clampY=v=>Math.max(PY0,Math.min(PY1,v));
  // near an edge the label must grow inward, otherwise a centred label spills outside the viewBox
  const anchorFor=x=>x>PX1-22?'end':x<PX0+22?'start':'middle';
  // white halo behind each ticker so it stays legible on top of the quadrant captions / green fill
  const lbl=(x,y,t,fs,an)=>`<text x="${x}" y="${y}" font-size="${fs}" text-anchor="${an}" font-weight="700" fill="#fff" stroke="#fff" stroke-width="2.6" stroke-linejoin="round">${t}</text>`
    +`<text x="${x}" y="${y}" font-size="${fs}" text-anchor="${an}" font-weight="700" fill="#334155">${t}</text>`;
  // scores are integer subscores 0-5, so ties land on the exact same coordinate — jitter the dots
  // around the true position and stack their labels so every holding stays readable
  const groups=new Map();
  pts.forEach(i=>{const k=i.economics+'|'+i.price; if(!groups.has(k))groups.set(k,[]); groups.get(k).push(i);});
  let dots='';
  groups.forEach(g=>{
    const cx=X(g[0].economics), cy=Y(g[0].price), n=g.length, r=n>1?Math.min(12,5+1.6*n):0;
    // fan the jitter INWARD when the group sits on an edge, so no dot gets clamped back onto its neighbour
    const ix=cx<=PX0+2?1:cx>=PX1-2?-1:0, iy=cy<=PY0+2?1:cy>=PY1-2?-1:0, onEdge=(ix||iy);
    const base=onEdge?Math.atan2(iy,ix):-Math.PI/2, span=onEdge?Math.PI/2:2*Math.PI;
    g.forEach((i,idx)=>{
      const a=n<2?0:(onEdge?base+span*(idx/(n-1)-0.5):base+span*idx/n);
      const dx=clampX(cx+(n>1?r*Math.cos(a):0)), dy=clampY(cy+(n>1?r*Math.sin(a):0));
      dots+=`<circle cx="${dx.toFixed(1)}" cy="${dy.toFixed(1)}" r="5" fill="${i.candidate?'#0d9488':'#94a3b8'}"><title>${esc(i.ticker)} — Quality ${i.economics} · Cheap ${i.price}${i.candidate?' · GARP candidate':''}</title></circle>`;
    });
    const lx=clampX(cx).toFixed(1), an=anchorFor(cx);
    if(n===1){
      const above=cy-9>=10;   // flip below the dot when putting it above would clip the top edge
      dots+=lbl(lx,(above?cy-9:cy+15).toFixed(1),esc(g[0].ticker),8.5,an);
    }else{
      const down=(cy+r+9*n+4)<=VH-14;   // stack downward unless it would run off the bottom
      const y0=down?cy+r+11:cy-r-9-9*(n-1);
      g.forEach((i,idx)=>{dots+=lbl(lx,(y0+9*idx).toFixed(1),esc(i.ticker),7.5,an);});
    }
  });
  return `<svg viewBox="0 0 ${VW} ${VH}" style="width:100%;max-width:460px;display:block;margin:6px 0 4px">
    <rect x="${PX0}" y="${PY0}" width="${PX1-PX0}" height="${PY1-PY0}" fill="#f8fafc" stroke="#e2e8f0"/>
    <rect x="${x3}" y="${PY0}" width="${(PX1-x3).toFixed(1)}" height="${(y3-PY0).toFixed(1)}" fill="#dcfce7" opacity=".5"/>
    <line x1="${x3}" y1="${PY0}" x2="${x3}" y2="${PY1}" stroke="#94a3b8" stroke-dasharray="4 3"/>
    <line x1="${PX0}" y1="${y3}" x2="${PX1}" y2="${y3}" stroke="#94a3b8" stroke-dasharray="4 3"/>
    <text x="${((x3+PX1)/2).toFixed(0)}" y="${PY0+12}" font-size="8.5" fill="#166534" text-anchor="middle">ถูก × ดี ★</text>
    <text x="${((PX0+x3)/2).toFixed(0)}" y="${PY0+12}" font-size="8.5" fill="#9a3412" text-anchor="middle">ถูกแต่คุณภาพต่ำ (trap?)</text>
    <text x="${((x3+PX1)/2).toFixed(0)}" y="${PY1-6}" font-size="8.5" fill="#475569" text-anchor="middle">ดีแต่แพง — รอ pullback</text>
    <text x="${((PX0+x3)/2).toFixed(0)}" y="${PY1-6}" font-size="8.5" fill="#991b1b" text-anchor="middle">แพงและไม่เด่น</text>
    <text x="${((PX0+PX1)/2).toFixed(0)}" y="${VH-8}" font-size="8.5" fill="#64748b" text-anchor="middle">Quality (ROIC−WACC spread) &#8594;</text>
    <text x="14" y="${MIDY}" font-size="8.5" fill="#64748b" transform="rotate(-90 14 ${MIDY})" text-anchor="middle">Cheap (margin of safety) &#8594;</text>
    ${dots}</svg>`;
}
function renderScreen(items){
  const el=document.getElementById('garpScreen'); if(!el)return;
  const dv=document.getElementById('garpDiv'); if(!items||!items.length){el.innerHTML=''; if(dv)dv.style.display='none'; return;} if(dv)dv.style.display='block';
  const cands=items.filter(x=>x.candidate).length;
  // P3-3: rows the screen cannot place (missing quality or cheap axis) are listed
  // greyed rather than dropped — "not shown" must never look like "not attractive"
  const nUn=items.filter(x=>x.unscreenable).length;
  const rows=items.map(it=>{
    const un=it.unscreenable;
    const cand=it.candidate?'<span class="tag" style="background:#0d9488;font-size:calc(9px*var(--fs))">candidate</span>'
      :(un?`<span class="muted" style="font-size:calc(9px*var(--fs));cursor:help" title="${esc('วัดไม่ได้: '+un+' — ไม่ได้แปลว่าไม่ดี แปลว่าข้อมูลไม่พอจะจัดอันดับ')}">วัดไม่ได้ &#9432;</span>`
          :'<span class="muted" style="font-size:calc(11px*var(--fs))">&mdash;</span>');
    return `<tr style="border-top:0.5px solid #e2e8f0${un?';background:#f8fafc;color:#94a3b8':''}">`
      +`<td style="padding:5px 8px;font-weight:700">${esc(it.ticker||'')}</td>`
      +`<td style="padding:5px 8px;color:#64748b">${esc(it.sector||'')}</td>`
      +`<td style="padding:5px 8px;text-align:center">${it.economics??'&mdash;'}</td>`
      +`<td style="padding:5px 8px;text-align:center">${it.price??'&mdash;'}</td>`
      +`<td style="padding:5px 8px;text-align:center;font-weight:700">${it.garp_score??'&mdash;'}</td>`
      +`<td style="padding:5px 8px;text-align:center">${cand}</td>`
      +`<td style="padding:5px 8px;color:#64748b">${esc(it.recommendation||'')}</td></tr>`;
  }).join('');
  el.innerHTML=`<div style="margin:6px 0 16px;border:1px solid #e2e8f0;border-radius:12px;padding:12px 16px;background:#fff">`
    +`<div style="font-size:calc(14px*var(--fs));font-weight:800">&#128269; GARP screen — cheap &times; quality <span class="muted" style="font-weight:600;font-size:calc(11px*var(--fs))">(Damodaran S13/S19) &middot; ${cands} candidate(s)</span></div>`
    +`<div class="muted" style="font-size:calc(11px*var(--fs));margin:5px 0 8px">จัดอันดับทุกตัวที่วิเคราะห์แล้ว = Economics (คุณภาพ/moat) × Price (margin of safety). candidate = ถูก×ดีทั้งคู่ (≥3 ทั้งสองด้าน)`
    +(nUn?` &middot; <b style="color:#b45309">${nUn} ตัววัดไม่ได้</b> (แสดงสีจางท้ายตาราง — ขาดข้อมูลด้านใดด้านหนึ่ง ไม่ได้แปลว่าแย่)`:'')+`</div>`
    +garpQuadrant(items)
    +`<table style="width:100%;border-collapse:collapse;font-size:calc(12px*var(--fs))"><thead><tr style="color:#64748b;text-align:left">`
    +`<th style="padding:5px 8px">Ticker</th><th style="padding:5px 8px">Sector</th>`
    +`<th style="padding:5px 8px;text-align:center" title="Economics subscore = ROIC−WACC spread (quality/moat)">Quality</th>`
    +`<th style="padding:5px 8px;text-align:center" title="Price subscore = margin of safety (cheapness)">Cheap</th>`
    +`<th style="padding:5px 8px;text-align:center">GARP /10</th><th style="padding:5px 8px;text-align:center">Candidate</th>`
    +`<th style="padding:5px 8px">DEEP reco</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
