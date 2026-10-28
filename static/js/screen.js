// The GARP screen on the Allocation tab: a quadrant map and a ranked table.
async function loadScreen(){
  const el=document.getElementById('garpScreen'); if(!el)return;
  try{const r=await fetch('/api/screen'); const d=await r.json(); renderScreen(d.items||[]);}catch(e){el.innerHTML='';}
}
// GARP quadrant (S13/S19): "cheap x good" is two-dimensional, so it is drawn as a map.
// Subscores share coordinates, so names on one point are listed together, and labels are
// nudged apart (with a leader line) instead of printed over each other.
function garpQuadrant(items){
  const pts=(items||[]).filter(i=>i.economics!=null&&i.price!=null);
  if(!pts.length)return '';
  const W=420,H=320,L=40,R=12,T=12,B=284, X=v=>L+Math.max(0,Math.min(5,v))/5*(W-L-R), Y=v=>B-Math.max(0,Math.min(5,v))/5*(B-T);
  let s=`<rect x="${X(3)}" y="${T}" width="${X(5)-X(3)}" height="${Y(3)-T}" fill="var(--bull)" opacity=".12"/>`;
  [0,1,2,3,4,5].forEach(v=>{s+=`<line x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${B}" stroke="var(--line)"/><line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${X(v)}" y="${B+14}" text-anchor="middle">${v}</text><text x="${L-6}" y="${Y(v)+3}" text-anchor="end">${v}</text>`;});
  s+=`<line x1="${X(3)}" x2="${X(3)}" y1="${T}" y2="${B}" stroke="var(--muted)" stroke-dasharray="4 3"/><line x1="${L}" x2="${W-R}" y1="${Y(3)}" y2="${Y(3)}" stroke="var(--muted)" stroke-dasharray="4 3"/>`;
  s+=`<text x="${W-R-4}" y="${T+12}" text-anchor="end" class="bull strong">ถูก × ดี</text><text x="${L+4}" y="${T+12}" class="bear">ถูกแต่คุณภาพต่ำ (trap?)</text><text x="${W-R-4}" y="${B-6}" text-anchor="end">ดีแต่แพง — รอ pullback</text><text x="${L+4}" y="${B-6}" class="bear">แพงและไม่เด่น</text>`;
  s+=`<text x="${(L+W-R)/2}" y="${H-4}" text-anchor="middle">Quality (ROIC−WACC spread) →</text><text x="12" y="${(T+B)/2}" text-anchor="middle" transform="rotate(-90 12 ${(T+B)/2})">Cheap (margin of safety) →</text>`;
  const grp=new Map(); pts.forEach(i=>{const k=i.economics+'|'+i.price; if(!grp.has(k))grp.set(k,[]); grp.get(k).push(i);});
  const labs=[], obs=[[W-R-70,T,W-R,T+16],[L,T,L+150,T+16],[W-R-130,B-18,W-R,B],[L,B-18,L+100,B]];
  grp.forEach(g=>{const x=g[0],cx=X(x.economics),cy=Y(x.price),c=g.some(y=>y.candidate),right=cx>W-90;
    s+=`<circle cx="${cx}" cy="${cy}" r="${c?6.5:5}" fill="${c?'var(--bull)':'var(--surface)'}" stroke="${c?'var(--bull)':'var(--muted)'}" stroke-width="1.8"><title>${esc(g.map(y=>y.ticker).join(', '))} — Quality ${x.economics} · Cheap ${x.price}${c?' · GARP candidate':''}</title></circle>`;
    obs.push([cx-7,cy-7,cx+7,cy+7]);
    labs.push({x:cx+(right?-9:9),y:cy+4,px:cx,py:cy,a:right?'end':'start',t:g.map(y=>y.ticker).join(' · '),c:c?'strong':''});});
  s+=placeLabels(labs.sort((a,b)=>(b.c?1:0)-(a.c?1:0)),obs);
  const cand=pts.filter(i=>i.candidate).map(i=>i.ticker);
  return svgChart(W,H,`GARP quadrant: แกนนอน Quality แกนตั้ง Cheap · ${cand.length} candidate${cand.length?': '+cand.join(', '):''}`,s);
}
// Rows the screen cannot place (no quality or no cheapness score) are listed greyed, not
// dropped: "not shown" must never read as "not attractive".
function screenRow(it){
  const un=it.unscreenable;
  const cand=it.candidate?chip('candidate','--bull','GARP candidate: ≥3 ทั้ง Quality และ Cheap')
    :(un?`<span class="note" style="cursor:help" title="${esc('วัดไม่ได้: '+un+' — ไม่ได้แปลว่าไม่ดี แปลว่าข้อมูลไม่พอจะจัดอันดับ')}">วัดไม่ได้ &#9432;</span>`:MUTED_DASH);
  return `<tr${un?' class="muted" style="background:var(--sunken)"':''}>`
    +`<th scope="row" class="l num" style="color:var(--ink)">${esc(it.ticker||'')}</th>`
    +`<td class="l muted">${esc(it.sector||'')}</td>`
    +`<td class="n">${it.economics??'&mdash;'}</td><td class="n">${it.price??'&mdash;'}</td>`
    +`<td class="n"><b>${it.garp_score??'&mdash;'}</b></td><td>${cand}</td>`
    +`<td class="l">${recoPill(it.recommendation)}</td></tr>`;
}
function renderScreen(items){
  const el=document.getElementById('garpScreen'); if(!el)return;
  const dv=document.getElementById('garpDiv'); if(!items||!items.length){el.innerHTML=''; if(dv)dv.style.display='none'; return;} if(dv)dv.style.display='block';
  const cands=items.filter(x=>x.candidate).length;
  const nUn=items.filter(x=>x.unscreenable).length;
  const rows=items.map(screenRow).join('');
  el.innerHTML=secHTML('G','GARP screen — cheap &times; quality',`Damodaran S13/S19 · ${cands} candidate(s)`,
    `<p class="note" style="margin:0 0 6px">จัดอันดับทุกตัวที่วิเคราะห์แล้ว = Economics (คุณภาพ/moat) × Price (margin of safety). candidate = ถูก×ดีทั้งคู่ (≥3 ทั้งสองด้าน)`
    +(nUn?` &middot; <b class="warnc">${nUn} ตัววัดไม่ได้</b> (แสดงสีจางท้ายตาราง — ขาดข้อมูลด้านใดด้านหนึ่ง ไม่ได้แปลว่าแย่)`:'')+`</p>`
    +`<div class="dgrid"><div style="max-width:560px">${garpQuadrant(items)}<p class="note" style="margin:4px 0 0">จุดทึบ = candidate (≥3 ทั้งสองแกน) · ชื่อหลายตัวบนจุดเดียว = คะแนนเท่ากัน</p></div><div class="tw" tabindex="0" role="region" aria-label="ตาราง GARP"><table><caption class="sr">GARP screen ทุกตัว</caption><thead><tr>`
    +`<th scope="col" class="l">Ticker</th><th scope="col" class="l">Sector</th>`
    +`<th scope="col" class="n" title="Economics subscore = ROIC−WACC spread (quality/moat)">Quality</th>`
    +`<th scope="col" class="n" title="Price subscore = margin of safety (cheapness)">Cheap</th>`
    +`<th scope="col" class="n">GARP /10</th><th scope="col" class="l">Candidate</th>`
    +`<th scope="col" class="l">DEEP reco</th></tr></thead><tbody>${rows}</tbody></table></div></div>`);
}
