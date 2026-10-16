// The GARP screen on the Allocation tab: a quadrant map and a ranked table.
async function loadScreen(){
  const el=document.getElementById('garpScreen'); if(!el)return;
  try{const r=await fetch('/api/screen'); const d=await r.json(); renderScreen(d.items||[]);}catch(e){el.innerHTML='';}
}
// GARP quadrant (S13/S19): "cheap x good" is two-dimensional, so it is drawn as a map.
// The plot area is inset in a padded viewBox so a score of 5 on an edge is not clipped.
const QUAD={X0:44, X1:320, Y0:22, Y1:182, W:344, H:212};
const quadX=v=>QUAD.X0+Math.max(0,Math.min(5,v))/5*(QUAD.X1-QUAD.X0);
const quadY=v=>QUAD.Y1-Math.max(0,Math.min(5,v))/5*(QUAD.Y1-QUAD.Y0);
const clampQuadX=v=>Math.max(QUAD.X0,Math.min(QUAD.X1,v)), clampQuadY=v=>Math.max(QUAD.Y0,Math.min(QUAD.Y1,v));
function garpQuadrant(items){
  const pts=(items||[]).filter(i=>i.economics!=null&&i.price!=null);
  if(!pts.length)return '';
  // subscores are integers 0-5, so ties share a coordinate: group them, then fan the dots
  // around the true position and stack their labels
  const groups=new Map();
  pts.forEach(i=>{const k=i.economics+'|'+i.price; if(!groups.has(k))groups.set(k,[]); groups.get(k).push(i);});
  let dots='';
  groups.forEach(g=>{dots+=quadrantGroup(g);});
  return quadrantFrame(dots);
}
// a white halo behind each ticker keeps it legible over the captions and the green fill
function quadrantLabel(x,y,t,fs,an){
  return `<text x="${x}" y="${y}" font-size="${fs}" text-anchor="${an}" font-weight="700" fill="#fff" stroke="#fff" stroke-width="2.6" stroke-linejoin="round">${t}</text>`
    +`<text x="${x}" y="${y}" font-size="${fs}" text-anchor="${an}" font-weight="700" fill="#334155">${t}</text>`;
}
function quadrantGroup(g){
  const {X0,X1,Y0,Y1,H}=QUAD;
  const cx=quadX(g[0].economics), cy=quadY(g[0].price), n=g.length, r=n>1?Math.min(12,5+1.6*n):0;
  // on an edge the jitter fans inward, so no dot is clamped back onto its neighbour
  const ix=cx<=X0+2?1:cx>=X1-2?-1:0, iy=cy<=Y0+2?1:cy>=Y1-2?-1:0, onEdge=(ix||iy);
  const base=onEdge?Math.atan2(iy,ix):-Math.PI/2, span=onEdge?Math.PI/2:2*Math.PI;
  let out='';
  g.forEach((i,idx)=>{
    const a=n<2?0:(onEdge?base+span*(idx/(n-1)-0.5):base+span*idx/n);
    const dx=clampQuadX(cx+(n>1?r*Math.cos(a):0)), dy=clampQuadY(cy+(n>1?r*Math.sin(a):0));
    out+=`<circle cx="${dx.toFixed(1)}" cy="${dy.toFixed(1)}" r="5" fill="${i.candidate?'#0d9488':'#94a3b8'}"><title>${esc(i.ticker)} — Quality ${i.economics} · Cheap ${i.price}${i.candidate?' · GARP candidate':''}</title></circle>`;
  });
  // near a side the label grows inward instead of spilling out of the viewBox
  const lx=clampQuadX(cx).toFixed(1), an=cx>X1-22?'end':cx<X0+22?'start':'middle';
  if(n===1){
    const above=cy-9>=10;   // below the dot when above would clip the top edge
    return out+quadrantLabel(lx,(above?cy-9:cy+15).toFixed(1),esc(g[0].ticker),8.5,an);
  }
  const down=(cy+r+9*n+4)<=H-14;   // stack downward unless it would run off the bottom
  const y0=down?cy+r+11:cy-r-9-9*(n-1);
  g.forEach((i,idx)=>{out+=quadrantLabel(lx,(y0+9*idx).toFixed(1),esc(i.ticker),7.5,an);});
  return out;
}
function quadrantFrame(dots){
  const {X0:PX0,X1:PX1,Y0:PY0,Y1:PY1,W:VW,H:VH}=QUAD, MIDY=(PY0+PY1)/2;
  const x3=quadX(3), y3=quadY(3);
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
// Rows the screen cannot place (no quality or no cheapness score) are listed greyed, not
// dropped: "not shown" must never read as "not attractive".
function screenRow(it){
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
}
function renderScreen(items){
  const el=document.getElementById('garpScreen'); if(!el)return;
  const dv=document.getElementById('garpDiv'); if(!items||!items.length){el.innerHTML=''; if(dv)dv.style.display='none'; return;} if(dv)dv.style.display='block';
  const cands=items.filter(x=>x.candidate).length;
  const nUn=items.filter(x=>x.unscreenable).length;
  const rows=items.map(screenRow).join('');
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
