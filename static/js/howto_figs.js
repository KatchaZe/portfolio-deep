// How to: each section shows the real component, copied from the live page, with numbered
// markers on the parts its table explains. The copy is inert and aria-hidden (the table is
// the text alternative); ids become data-fid so nothing clashes with the live page.

function htCopy(el){
  if(!el)return null;
  const c=el.cloneNode(true);
  [c,...c.querySelectorAll('[id]')].forEach(e=>{if(e.id){e.dataset.fid=e.id;e.removeAttribute('id');}});
  c.querySelectorAll('[onclick],[oninput],[onchange],[onkeydown]').forEach(e=>['onclick','oninput','onchange','onkeydown'].forEach(a=>e.removeAttribute(a)));
  return c;
}
const htShown=el=>el&&el.innerHTML.trim()&&getComputedStyle(el).display!=='none'&&!el.hidden;
const fid=(root,id)=>root.querySelector(`[data-fid="${id}"]`);

// each figure: build(stage) fills the stage and returns false when its source is not on
// the page yet; marks are [label, find(stage) -> element]
// every copy is laid out at one desktop width, then scaled to fit the page
const HT_W=1480;
const HT_FIGS={
  htfig1:{max:0.9,
    need:'เปิดแท็บ My Portfolio (กด Run Daily ถ้ายังไม่มีการ์ด)',
    build(st){
      const src=document.querySelector('#cards .card'); const x=src&&CARD_ROWS[src.id]; if(!x)return false;
      const c=htCopy(src), d=c.querySelector('details.cdetail');
      d.open=true; const b=d.querySelector('.dbody'); b.innerHTML=cardDetail(x,'htf'); b.dataset.ready='1';
      st.appendChild(c); return true;},
    marks:[['1',s=>s.querySelector('.chead h3')],['2',s=>s.querySelector('.value')],['3',s=>s.querySelector('.csum .scale')],
      ['4',s=>s.querySelector('.deep')],['5',s=>s.querySelector('.pos4')],['6',s=>s.querySelector('.csum .sig')],
      ['7',s=>s.querySelector('.earn3')],['8',s=>s.querySelector('.csum .guard')||s.querySelector('.csum .advice')],
      ...['A','B','C','D','E'].map((l,i)=>[l,s=>s.querySelectorAll('.dtab')[i]])]},
  htfig2:{max:0.9,
    need:'กด Run Daily ก่อน (แถบ command strip ยังไม่มีข้อมูล)',
    build(st){
      const strip=document.getElementById('cmdStrip'); if(!strip||strip.hidden)return false;
      const top=document.createElement('div'); top.className='top'; top.style.position='static';
      top.appendChild(htCopy(document.querySelector('.top-in'))); st.appendChild(top);
      const tools=htCopy(document.getElementById('tools')); tools.open=false; tools.style.margin='10px 16px'; st.appendChild(tools);
      ['momBanner','assumeBanner'].forEach(id=>{const e=document.getElementById(id); if(htShown(e))st.appendChild(htCopy(e));});
      const box=document.createElement('div'); box.style.padding='0 16px 12px';
      box.appendChild(htCopy(strip)); const syn=document.getElementById('stripSynth'); if(htShown(syn))box.appendChild(htCopy(syn));
      st.appendChild(box); return true;},
    marks:[['1',s=>fid(s,'persist')],['2',s=>fid(s,'quota')],['–',s=>s.querySelector('details.tools summary')],['3',s=>fid(s,'btnDaily')],
      ['4',s=>fid(s,'momBanner')],['5',s=>fid(s,'assumeBanner')],
      ...['6','7','8','9'].map((l,i)=>[l,s=>s.querySelectorAll('.stile')[i]]),['10',s=>fid(s,'stripSynth')]]},
  htfig3:{max:0.75,
    need:'เปิดแท็บ Allocation แล้วกด Run risk analysis',
    build(st){
      const story=document.querySelector('#riskBody .finding'); if(!story)return false;
      ['cashStance','garpScreen'].forEach(id=>{const e=document.getElementById(id); if(htShown(e)){const c=htCopy(e);c.style.marginBottom='14px';st.appendChild(c);}});
      st.appendChild(htCopy(document.querySelector('#p3 .deskhead'))); st.appendChild(htCopy(story)); return true;},
    marks:[['1',s=>fid(s,'cashStance')],['2',s=>s.querySelector('[data-fid=garpScreen] svg')],['3',s=>s.querySelector('[data-fid=garpScreen] table')],
      ['4',s=>s.querySelector('.deskctl')],['5',s=>s.querySelector('.finding > p')],['6',s=>s.querySelector('.finding h2')]]},
  htfig4:{max:0.6,
    need:'เปิดแท็บ Allocation แล้วกด Run risk analysis',
    build(st){
      const body=document.getElementById('riskBody'); if(!body||!body.querySelector('.secno'))return false;
      const c=htCopy(body); c.querySelectorAll('.finding').forEach(e=>e.remove()); st.appendChild(c);
      const cost=document.querySelector('#p3 > .grid12:last-of-type'); if(cost){const k=htCopy(cost);k.style.marginTop='14px';st.appendChild(k);}
      return true;},
    marks:[...[['1','1'],['2','2'],['3','3'],['4','4'],['5','5'],['6','5b'],['7','6'],['8','7'],['10','C'],['11','W']]
      .map(([l,no])=>[l,s=>[...s.querySelectorAll('.secno')].find(e=>e.textContent.trim()===no)]),
      ['9',s=>[...s.querySelectorAll('.pnl h2')].find(e=>/Bottom line/.test(e.textContent))]]},
  htfig5:{max:0.6,
    need:'เปิดแท็บ Correlation แล้วกด Run correlation',
    build(st){
      const body=document.getElementById('corrBody'); if(!body||!body.querySelector('[aria-labelledby=hHeat]'))return false;
      st.appendChild(htCopy(document.querySelector('#p6 > .phead'))); st.appendChild(htCopy(body)); return true;},
    marks:[['A','hPhil'],['B','hHeat'],['C','hSec'],['D','hPairs'],['E','hBench'],['F','hFx'],['A7','hRoll']]
      .map(([l,h])=>[l,s=>s.querySelector(`[aria-labelledby=${h}]`)]).concat([['Crisis',s=>s.querySelector('.seg')]])},
};

// the copy is laid out at full width / scale, then shrunk with a transform (getBoundingClientRect
// reports transformed boxes exactly; CSS zoom does not, inside a zoomed subtree)
function htPlace(fig){
  const st=fig.querySelector('.htstage'); if(!st)return;
  fig.querySelectorAll('.htmark').forEach(m=>m.remove());
  const inner=fig.parentElement.clientWidth-30; if(inner<=0)return;
  const k=Math.min(HT_FIGS[fig.id].max||1,inner/HT_W);
  st.style.width=HT_W+'px'; st.style.transform=`scale(${k})`;
  fig.style.height=(st.offsetHeight*k+28)+'px'; fig.style.width=(HT_W*k+30)+'px'; fig.style.maxWidth='100%';
  const F=fig.getBoundingClientRect();
  (HT_FIGS[fig.id].marks||[]).forEach(([lab,find])=>{
    const el=find(st); if(!el)return; const r=el.getBoundingClientRect(); if(!r.width&&!r.height)return;
    const m=document.createElement('span'); m.className='htmark'+(lab.length>2?' wide':''); m.textContent=lab;
    m.style.left=Math.max(0,r.left-F.left-8)+'px'; m.style.top=Math.max(0,r.top-F.top-8)+'px'; fig.appendChild(m);});
}
const htObs=typeof ResizeObserver!=='undefined'?new ResizeObserver(es=>es.forEach(e=>e.target.querySelectorAll('.htfig').forEach(htPlace))):null;
function renderHowtoFigs(){
  Object.entries(HT_FIGS).forEach(([id,f])=>{
    const fig=document.getElementById(id); if(!fig)return;
    fig.innerHTML=''; fig.setAttribute('aria-hidden','true');
    const st=document.createElement('div'); st.className='htstage'; st.inert=true;
    fig.appendChild(st);
    let ok=false; try{ok=f.build(st)!==false;}catch(e){ok=false;}
    if(!ok){fig.innerHTML=`<div class="htph">ภาพตัวอย่างจะแสดงข้อมูลจริงของพอร์ตคุณ — ${esc(f.need)} แล้วกลับมาที่หน้านี้</div>`;return;}
    htPlace(fig); requestAnimationFrame(()=>htPlace(fig)); setTimeout(()=>htPlace(fig),500);
    if(document.fonts)document.fonts.ready.then(()=>htPlace(fig));
    if(htObs)htObs.observe(fig.parentElement);
  });
}
