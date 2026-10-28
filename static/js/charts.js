// Theme-aware SVG/HTML charts shared by the Allocation and Correlation tabs. Every mark is
// coloured with a CSS token, so light and dark mode need no redraw.
const CAT=['--c1','--c2','--c3','--c4','--c5','--c6','--c7','--c8'];

// share-of-whole doughnut: the five largest slices, the rest folded into "อื่น ๆ"
function donutHTML(items,label,keep=5){
  const rows=(items||[]).filter(x=>x&&x.value>0);
  if(!rows.length)return '<p class="note" style="margin:0">ไม่มีข้อมูล</p>';
  const top=rows.slice(0,keep), rest=rows.slice(keep).reduce((s,x)=>s+x.value,0);
  const list=rest>0.05?[...top,{label:'อื่น ๆ',value:Math.round(rest*10)/10}]:top;
  const tot=list.reduce((s,x)=>s+x.value,0)||1, C=2*Math.PI*40; let off=0;
  const arcs=list.map((x,i)=>{const len=x.value/tot*C,a=`<circle cx="60" cy="60" r="40" fill="none" stroke="var(${CAT[i%CAT.length]})" stroke-width="18" stroke-dasharray="${Math.max(0,len-1.2).toFixed(2)} ${C.toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 60 60)"/>`;off+=len;return a;}).join('');
  const pct=v=>nf(v/tot*100,1)+'%';
  return `<div class="donut"><svg viewBox="0 0 120 120" role="img" aria-label="${esc(label+': '+list.map(x=>x.label+' '+pct(x.value)).join(', '))}">${arcs}</svg>
    <div class="legend">${list.map((x,i)=>`<div><span class="sw" style="background:var(${CAT[i%CAT.length]})"></span><span>${esc(x.label)}</span><b class="num">${pct(x.value)}</b></div>`).join('')}</div></div>`;
}
function makePie(id,data){const el=document.getElementById(id);if(el)el.innerHTML=donutHTML(data,id);}
// horizontal bars: {l: label, v: value, tok: '--bear', cls, title}; fmt prints the value
function hbarsHTML(list,{max,fmt=v=>nf(v,1)+'%',tok='--accent',label='',vw}={}){
  if(!list.length)return '<p class="note" style="margin:0">ไม่มีข้อมูล</p>';
  const mx=max||Math.max(...list.map(x=>Math.abs(x.v||0)))||1;
  return `<div class="hb" role="list" aria-label="${esc(label)}"${vw?` style="--vw:${vw}"`:''}>${list.map(x=>`<div class="hbr" role="listitem"${x.title?` title="${esc(x.title)}"`:''}><span class="hbl" title="${esc(x.l)}">${esc(x.l)}</span><span class="t"><span class="f" style="width:${Math.min(100,Math.abs(x.v||0)/mx*100).toFixed(1)}%;background:var(${x.tok||tok})"></span></span><span class="num hbv ${x.cls||''}">${fmt(x.v,x)}</span></div>`).join('')}</div>`;
}
// two bars per row (e.g. % capital vs % risk): a = grey reference, b = coloured
function pairBarsHTML(rows,label){
  if(!rows.length)return '<p class="note" style="margin:0">ไม่มีข้อมูล</p>';
  const mx=Math.max(...rows.flatMap(r=>[Math.abs(r.a||0),Math.abs(r.b||0)]))||1;
  return `<div class="cvr" role="list" aria-label="${esc(label)}">${rows.map(r=>`<div class="cvrr" role="listitem"${r.title?` title="${esc(r.title)}"`:''}><span class="num" style="font-weight:600">${esc(r.l)}</span>
    <span class="pair"><span class="t"><span class="f" style="width:${(Math.abs(r.a||0)/mx*100).toFixed(1)}%;background:var(--muted);opacity:.55"></span></span><span class="t"><span class="f" style="width:${(Math.abs(r.b||0)/mx*100).toFixed(1)}%;background:var(${r.tok||'--accent'})"></span></span></span>
    <span class="num cvrv">${r.av} → ${r.bv}</span><span class="num cvrd ${r.dcls||''}">${r.d}</span></div>`).join('')}</div>`;
}
function kpisHTML(list){return `<div class="kpis">${list.filter(Boolean).map(([l,v,c,sub,title])=>`<div class="kpi"${title?` title="${esc(title)}"`:''}><span class="cap">${l}</span><b class="${c||''}">${v}</b>${sub?`<span class="note">${sub}</span>`:''}</div>`).join('')}</div>`;}
// a numbered Risk Desk / Allocation panel
function secHTML(no,title,tag,body,span){return `<section class="pnl ${span||'s12'}"><div class="phead"><h2>${no!=null?`<span class="secno">${no}</span>`:''}${title}</h2>${tag?`<span class="tagsrc">${tag}</span>`:''}</div>${body}</section>`;}
