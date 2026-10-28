// The stock card (My Portfolio and Watchlist): a summary that answers "below value? good
// business? what do I own? what now?", and a <details> that opens five detail tabs.
// The detail markup is built on first open, from the row kept in CARD_ROWS.

const CARD_ROWS={};   // card id -> display row
function cardId(x,watch){return (watch?'w-':'c-')+String(x.ticker).replace(/[^A-Za-z0-9_-]/g,'_');}
function recoPill(rec){const t=RECO_COLOR[rec]; return rec?`<span class="pill" style="${tone(t||'--muted')}">${esc(RECO_LABEL[rec]||rec)}</span>`:'';}
// one DEEP pillar as a 5-segment bar; a missing score shows a dash, never a zero
function pillarBar(lab,full,v){
  const w=v==null?0:Math.max(0,Math.min(5,v));
  const segs=[0,1,2,3,4].map(i=>`<i><span style="width:${Math.max(0,Math.min(1,w-i))*100}%"></span></i>`).join('');
  return `<div class="pil" title="${full} ${v==null?'ไม่มีข้อมูล':nf(v,2)+'/5'}"><div class="lab"><span>${lab}</span><b>${v==null?'—':(+Number(v).toFixed(2)).toString()}</b></div><div class="seg5" aria-hidden="true">${segs}</div></div>`;
}
// momCell / rankCell build table cells; a card keeps only what is inside the <td>
function cellContent(html){const m=String(html).match(/^<td(?:\s+title="[^"]*")?>([\s\S]*)<\/td>$/);return m?m[1]:html;}
function cellTitle(html){const m=String(html).match(/^<td title="([^"]*)"/);return m?m[1]:'';}

function cardHead(x,watch){
  const ac=ASSET_CLASS[x.ticker];
  const rmFn=watch?`removeWatch('${x.ticker}')`:`removeHolding('${x.ticker}')`;
  const chg=x.change==null?'':`<span class="${upcls(x.change)}">${arrow(x.change)}${sgn(x.change,2)}</span> วันล่าสุด`;
  return `<div class="chead">
    <div style="min-width:0"><h3>${dot(x.confidence_tier)}<span class="tk" id="h-${cardId(x,watch)}">${esc(x.ticker)}</span> ${ac?'':recoPill(x.recommendation)}</h3>
      <div class="co" title="${esc(x.company)} · ${esc(x.sector)}">${esc(x.company)} &middot; ${esc(x.sector)}</div>
      <div class="badges">${ac?pricingBadge(ac):''}${flagIcon(x)}${skippedBadge(x)}${moatChip(x)}${eqBadge(x)}${garpBadge(x)}</div></div>
    <div class="px"><div class="pxrow"><b title="${x.price_date?'ราคาปิด '+esc(x.price_date)+' (T-1)':'ราคาล่าสุด'}">${usd(x.price)}</b><button type="button" class="rm" title="ลบ ${esc(x.ticker)}" aria-label="ลบ ${esc(x.ticker)}" onclick="${rmFn}">&times;</button></div><div class="note">${chg}</div></div></div>`;
}
function cardDeep(x){
  const p=x.pillars||{};
  return `<div class="deep"><div class="compbox"><div class="cap">DEEP</div><div><span class="comp">${x.composite==null?'–':nf(x.composite,2)}</span><span class="note">/5</span></div></div>
    ${pillarBar('D','Demand',p.D)}${pillarBar('Ex','Execution',p.E_exec)}${pillarBar('Ec','Economics',p.E_econ)}${pillarBar('P','Price',p.P)}</div>`;
}
function cardPosition(x){
  if(x.shares==null&&x.market_value==null)return '';
  const pl=x.pl==null?'–':`${arrow(x.pl)}${sgn(x.pl_pct,1)}`;
  return `<div class="pos4">
    <div class="stat"><span class="cap">ถือ</span><b>${x.shares==null?'–':nf(x.shares,x.shares<10?3:2)}</b></div>
    <div class="stat"><span class="cap">มูลค่า</span><b>${usd(x.market_value)}</b></div>
    <div class="stat"><span class="cap">น้ำหนัก</span><b>${x._w==null?'–':nf(x._w,1)+'%'}</b></div>
    <div class="stat" title="${x.pl==null?'':'P/L '+usd(x.pl)+' จากต้นทุน '+usd(x.cost_basis)}"><span class="cap">กำไร/ขาดทุน</span><b class="${upcls(x.pl)}">${pl}</b></div></div>`;
}
function cardSignals(x){
  const mc=momCell(x), rc=rankCell(x);
  return `<div class="sig"><span>${dispAction(x)}</span>
    <span title="${cellTitle(mc)}">Momentum ${cellContent(mc).replace(/<div class="note">[\s\S]*?<\/div>/,'')}</span>
    ${x.momentum_v2&&x.momentum_v2.mom_bucket?`<span title="${cellTitle(rc)}">Rank ${cellContent(rc).replace(/<div class="note">[\s\S]*?<\/div>/,'')}</span>`:''}</div>`;
}
// S3 / S40-41: bonds, gold and crypto have no equity cash flows, so their card shows no
// FV / GARP / EQ (pricing, not valuation)
const ASSET_CLASS_NOTE={
  bond:'ตราสารหนี้ — ความเสี่ยงหลักคืออัตราดอกเบี้ย (duration) ดูผลใน Allocation &rarr; Bond rate risk',
  other:'ไม่แสดง FV / Upside / GARP — สินทรัพย์นี้เล่นเกม <b>pricing</b> ไม่ใช่ valuation (S40-41): กำไรมาจากคนถัดไปจ่ายแพงกว่าเท่านั้น จำกัดน้ำหนัก',
};

// one holding, or one watchlist name when watch is true
function pcard(x,watch){
  const id=cardId(x,watch); CARD_ROWS[id]=x;
  const ac=ASSET_CLASS[x.ticker];
  const own=watch?`<div><button type="button" class="btn sm" onclick="promote('${x.ticker}')">+ เพิ่มเข้าพอร์ต</button></div>`:cardPosition(x);
  const body=ac
    ? `<div class="note">${ASSET_CLASS_NOTE[ac==='bond'?'bond':'other']}</div>${own}${cardSignals(x)}${guardBadges(x)}${adviceLine(x)}`
    : `<div class="box" style="gap:6px">${valueGauge(x)||`<div class="note">ยังไม่มี Fair Value — ${upsideCell(x)}</div>`}</div>${cardDeep(x)}${own}${cardSignals(x)}${earnCircles(x)}${guardBadges(x)}${adviceAction(x)}`;
  return `<article class="card" id="${id}" data-t="${esc(x.ticker)}" aria-labelledby="h-${id}">
    <div class="csum">${cardHead(x,watch)}${body}</div>
    <details class="cdetail" data-card="${id}"><summary><span class="closed-txt">รายละเอียด</span><span class="open-txt">ซ่อนรายละเอียด</span><span class="sr"> ${esc(x.ticker)}</span></summary><div class="dbody"></div></details>
  </article>`;
}

// ---- detail tabs
const DTABS=[["val","มูลค่า"],["deep","DEEP & ทุน"],["trend","แนวโน้ม 5 ปี"],["earn","งบ & สัญญาณ"],["data","ข้อมูล"]];
const DPANES={
  val(x){
    if(ASSET_CLASS[x.ticker])return `<p class="note">${ASSET_CLASS_NOTE[ASSET_CLASS[x.ticker]==='bond'?'bond':'other']}</p>`;
    const left=valueMethodsTable(x)+(x.young_dcf?'':pricedInLine(x))+(x.anchor_value==null?impliedVsActualBars(x):'');
    const right=(scenarioPanel(x)||'')+youngPanel(x);
    const charts=[storyMcBlock((x.scenarios||{}).story_mc,x.price),relativePanel(x)].filter(Boolean);
    return `<div class="dgrid"><div class="box">${left||'<p class="note">ไม่มีวิธีประเมินมูลค่าที่ใช้ได้</p>'}</div><div class="box">${right||'<p class="note">ไม่มี scenario สำหรับหุ้นนี้</p>'}</div></div>`
      +(charts.length?`<div class="dgrid" style="margin-top:16px">${charts.join('')}</div>`:'');
  },
  deep(x){return deepDrawer(x);},
  trend(x){return trendStrip(x);},
  earn(x){
    const gw=g=>esc(g||'');
    const none=c=>`<tr><td colspan="${c}" class="l muted">${'ไม่มีข้อมูล'}</td></tr>`;
    const T=(cap,head,body,c,miss)=>`<div class="box"><div class="tw" tabindex="0" role="region" aria-label="${esc(cap)}"><table><caption>${cap}</caption><thead><tr>${head.map((h,i)=>`<th scope="col" class="${i?'n':'l'}">${h}</th>`).join('')}</tr></thead><tbody>${body||none(c)}</tbody></table></div>${body?'':miss}</div>`;
    const st=x.earn_status;
    const eps=T('EPS เทียบ consensus',['ไตรมาส','จริง','คาด','ต่าง','ผล'],(x.earnings_surprises||[]).map(e=>`<tr><td class="l">${esc(qlab(e.quarter))}</td><td class="n">${nf(e.eps_actual)}</td><td class="n">${nf(e.eps_estimate)}</td><td class="n ${upcls(e.surprise_pct)}">${sgn(e.surprise_pct)}</td><td class="n">${gw(e.grade)}</td></tr>`).join(''),5,earnMiss(st,'eps'));
    const rev=T('Revenue เทียบ consensus',['ไตรมาส','จริง','คาด','ต่าง','ผล'],(x.rev_surprises||[]).map(e=>`<tr><td class="l">${esc(qlab(e.quarter))}</td><td class="n">${usd(e.rev_actual)}</td><td class="n">${usd(e.rev_estimate)}</td><td class="n ${upcls(e.surprise_pct)}">${sgn(e.surprise_pct)}</td><td class="n">${gw(e.grade)}</td></tr>`).join(''),5,earnMiss(st,'rev'));
    const mgn=T('Operating margin เทียบปีก่อน',['ไตรมาส','Op margin','ปีก่อน','ต่าง','ทิศ'],(x.margin_trend||[]).map(e=>`<tr><td class="l">${esc(qlab(e.quarter))}</td><td class="n">${e.op_margin==null?'–':nf(e.op_margin*100,1)+'%'}</td><td class="n">${e.op_margin_yoy==null?'–':nf(e.op_margin_yoy*100,1)+'%'}</td><td class="n ${upcls(e.delta_pp)}">${sgn(e.delta_pp,1)}</td><td class="n">${gw(e.grade)}</td></tr>`).join(''),5,earnMiss(st,'mgn'));
    const m=x.momentum_v2||{}, mc=momCell(x), rc=rankCell(x);
    const adv=pp2pct(x.advice||'').split('|').map(s=>s.trim()).filter(Boolean);
    return `<div class="egrid">${eps}${rev}${mgn}</div>
      <div style="margin:8px 0 14px">${peadRow(x.pead||{})}<p class="note" style="margin:4px 0 0">วันประกาศงบครั้งถัดไป: ยังไม่มีแหล่งข้อมูลในแอป</p></div>
      <div class="dgrid"><div class="box"><h3>Momentum <span class="note">${esc(m.mom_label||'')} ${m.mom_score??''}${m.mom_n?'/'+m.mom_n:''}</span></h3>
        <div class="sig"><span title="${cellTitle(mc)}">${cellContent(mc)}</span><span title="${cellTitle(rc)}">${cellContent(rc)}</span></div>
        <ul class="lines">
          <li><span>6 เดือน</span><b class="${upcls(m.roc_6m)}">${ratioPct(m.roc_6m)}</b></li><li><span>12 เดือน</span><b class="${upcls(m.roc_12m)}">${ratioPct(m.roc_12m)}</b></li>
          <li><span>12-1</span><b class="${upcls(m.mom_12_1)}">${ratioPct(m.mom_12_1)}</b></li><li><span>เทียบ SPY (12-1)</span><b class="${upcls(m.excess_12_1)}">${ratioPct(m.excess_12_1)}</b></li>
          <li><span>เส้น 200 วัน</span><b>${m.above_sma200===true?'อยู่เหนือ':m.above_sma200===false?'อยู่ใต้':'–'}</b></li><li><span>ความผันผวนต่อปี</span><b>${m.vol_ann==null?'–':nf(m.vol_ann*100,0)+'%'}</b></li>
          <li><span>RSI / MACD / DBBMV</span><b>${fixed(x.rsi,0)} ${esc(x.rsi_signal||'')} / ${esc(x.macd_signal||'—')} / ${esc(x.dbbmv_signal||'—')}</b></li></ul>${guardBadges(x)}</div>
        <div class="box"><h3>คำแนะนำ (มูลค่า → คุณภาพ → จังหวะ)</h3>${adv.length?`<ul class="lines" style="gap:6px">${adv.map(a=>{const i=a.indexOf(':');return `<li style="grid-template-columns:1fr"><span>${i>0?`<b style="font-family:var(--f-body)">${esc(a.slice(0,i))}</b> ${esc(a.slice(i+1))}`:esc(a)}</span></li>`;}).join('')}</ul>`:'<p class="note" style="margin:0">ไม่มีคำแนะนำ</p>'}
          <div><span class="cap">Action</span> ${dispAction(x)}</div></div></div>`;
  },
  data(x){
    const fl=x.flags||[], es=x.earn_status||{};
    return `<div class="dgrid"><div class="box"><h3>ข้อสังเกตจากข้อมูล (${fl.length})</h3>${fl.length?`<ol class="flaglist">${fl.map(f=>`<li>${esc(pp2pct(f))}</li>`).join('')}</ol>`:'<p class="note" style="margin:0">ไม่มี</p>'}</div>
      <div class="box"><h3>แหล่งและความเชื่อมั่น</h3><ul class="lines">
        <li><span>Confidence</span><b>${x.confidence??'–'} · ${esc(x.confidence_tier||'–')}</b></li>
        <li><span>ประเภทบริษัท · โมเดล</span><b>${esc(x.company_type||'–')} · ${esc(methodShort(x.anchor_method)||'–')}</b></li>
        <li><span>Earnings quality</span><b>${esc(x.eq_verdict||'–')}</b></li>
        ${x.garp_score!=null?`<li><span>GARP</span><b>${x.garp_score}/10${x.garp_candidate?' · candidate':''}</b></li>`:''}
        <li><span>ราคาปิด</span><b>${esc(x.price_date||'–')}</b></li>
        ${x.forward_eps!=null?`<li><span>Forward EPS</span><b>$${x.forward_eps} · ${x.forward_eps_n||0} แหล่ง${x.forward_eps_spread_pct!=null?' · spread '+x.forward_eps_spread_pct+'%':''}</b></li>`:''}
        ${Object.entries(es).map(([k,s])=>`<li><span>แหล่ง ${esc(k.toUpperCase())}</span><b style="font-family:var(--f-body);white-space:normal;text-align:right;overflow-wrap:anywhere">${esc(String((s||{}).src||(s||{}).reason||'–').slice(0,80))}</b></li>`).join('')}</ul>
        ${(x.key_metrics||{}).crp_basis?`<p class="note" style="margin:0">CRP: ${esc(x.key_metrics.crp_basis)}</p>`:''}
        <p class="note" style="margin:0">${esc(pp2pct(x.verdict||''))}</p></div></div>`;
  },
};
function cardDetail(x,id){
  return `<div class="dtabs" role="tablist" aria-label="รายละเอียด ${esc(x.ticker)}">${DTABS.map(([k,l],i)=>`<button type="button" class="dtab" role="tab" id="dt-${id}-${k}" aria-controls="dp-${id}-${k}" aria-selected="${i===0}" tabindex="${i===0?0:-1}" data-k="${k}">${l}</button>`).join('')}</div>`
    +DTABS.map(([k],i)=>`<div role="tabpanel" id="dp-${id}-${k}" aria-labelledby="dt-${id}-${k}" tabindex="0"${i?' hidden':''}>${i===0?DPANES[k](x):''}</div>`).join('');
}
// a tablist: click or arrow keys select a tab, show its panel; onPick(tab) renders lazily
function wireTabs(root,sel,onPick){
  const tabs=[...root.querySelectorAll(sel)];
  const pick=(i,focus)=>{tabs.forEach((t,j)=>{t.setAttribute('aria-selected',j===i);t.tabIndex=j===i?0:-1;const p=document.getElementById(t.getAttribute('aria-controls'));if(p)p.hidden=j!==i;});
    if(onPick)onPick(tabs[i]); if(focus)tabs[i].focus();};
  tabs.forEach((t,i)=>{t.addEventListener('click',()=>pick(i));
    t.addEventListener('keydown',e=>{const k={ArrowRight:1,ArrowLeft:-1}[e.key];
      if(k){e.preventDefault();pick((i+k+tabs.length)%tabs.length,true);}
      else if(e.key==='Home'){e.preventDefault();pick(0,true);}else if(e.key==='End'){e.preventDefault();pick(tabs.length-1,true);}});});
}
// <details> toggle does not bubble, so listen in the capture phase
document.addEventListener('toggle',e=>{
  const d=e.target; if(!d.classList||!d.classList.contains('cdetail')||!d.open)return;
  const body=d.querySelector('.dbody'); if(body.dataset.ready)return;
  const id=d.dataset.card, x=CARD_ROWS[id]; if(!x)return;
  body.innerHTML=cardDetail(x,id); body.dataset.ready='1';
  wireTabs(body,'.dtab',t=>{const p=document.getElementById(t.getAttribute('aria-controls'));if(p&&!p.dataset.ready){p.innerHTML=DPANES[t.dataset.k](x);p.dataset.ready='1';}});
  body.querySelector('[role=tabpanel]').dataset.ready='1';
},true);

// ---- My Portfolio: summary band, filter / sort toolbar, the card grid
const VIEW={q:'',g:'all',sort:'mos'};
try{Object.assign(VIEW,JSON.parse(localStorage.getItem('deep-view')||'{}'));}catch(e){}
function saveView(){try{localStorage.setItem('deep-view',JSON.stringify(VIEW));}catch(e){}}
const RGROUP={"BUY":"buy","HOLD / Accumulate":"hold","HOLD":"hold","SELL / AVOID":"sell"};
const SORTS={mos:["MoS สูง → ต่ำ",(a,b)=>(mosOf(b)??-1e9)-(mosOf(a)??-1e9)],w:["น้ำหนักพอร์ต",(a,b)=>(b._w||0)-(a._w||0)],comp:["คะแนน DEEP",(a,b)=>(b.composite||0)-(a.composite||0)],pl:["กำไร/ขาดทุน %",(a,b)=>(b.pl_pct??-1e9)-(a.pl_pct??-1e9)],t:["Ticker A → Z",(a,b)=>String(a.ticker).localeCompare(String(b.ticker))]};
function summaryBand(rows,t,bench,phil){
  const mv=t.market_value||0, CL=100, X=v=>Math.max(-CL,Math.min(CL,v));
  const valued=rows.filter(r=>mosOf(r)!=null);
  const sorted=[...valued].sort(SORTS.mos[1]);
  const above=mv?valued.filter(r=>mosOf(r)<0).reduce((s,r)=>s+(r.market_value||0),0)/mv*100:null;
  const groups=[["BUY","--bull"],["HOLD / Accumulate","--accent"],["HOLD","--base"],["SELL / AVOID","--bear"]].map(([k,c])=>{const rs=rows.filter(r=>r.recommendation===k);return {l:RECO_LABEL[k],c,n:rs.length,w:mv?rs.reduce((s,r)=>s+(r.market_value||0),0)/mv*100:0};}).filter(g=>g.n);
  const pm=portMoS(), b=bench||{}, p=phil||{};
  const pl=t.pl==null?'':`<div style="margin-top:4px" class="${upcls(t.pl)}"><b class="num">${arrow(t.pl)}${usd(Math.abs(t.pl))} (${sgn(t.pl_pct)})</b> <span class="muted">จากต้นทุน ${usd(t.cost_basis)}</span></div>`;
  return `<div class="grid12">
   <section class="pnl s8" aria-label="สรุปพอร์ต"><div class="hero">
     <div><div class="cap">มูลค่าพอร์ต${t.cash?' (รวมเงินสด)':''}</div><div class="hero-num">${usd(t.total_value??mv)}</div>${pl}
       <div class="kv"><div><span class="cap">12 เดือน พอร์ต / S&amp;P</span><b>${sgn(b.portfolio_12m)} / ${sgn(b.market_12m)}</b></div>
         <div><span class="cap">ส่วนต่าง</span><b class="${upcls(b.excess_pp)}">${arrow(b.excess_pp)}${sgn(b.excess_pp,1)}</b></div>
         <div><span class="cap">เงินสด</span><b>${usd(t.cash||0)}${t.cash?` <span class="note">${t.cash_pct}%</span>`:''}</b></div></div></div>
     <div><div class="cap" style="display:flex;justify-content:space-between"><span>ราคาเทียบ Fair value รายตัว</span><span>MoS</span></div>
       ${sorted.length?`<ul class="mosbars clip" id="mosl" style="--rows:${Math.ceil(sorted.length/2)}">${sorted.map(r=>{const m=X(mosOf(r)),left=m<0?50+m/2:50,w=Math.abs(m)/2;
         return `<li><button class="mrow" type="button" data-go="${esc(r.ticker)}" aria-label="${esc(r.ticker)} MoS ${sgn(mosOf(r))} ไปที่การ์ด"><span class="num" style="font-weight:600">${esc(r.ticker)}</span><span class="mtrack"><span class="mfill" style="left:${left}%;width:${w}%;background:var(${m<0?'--bear':'--bull'})"></span></span><span class="num ${upcls(mosOf(r))}" style="text-align:right">${mosOf(r)<-CL?'< −100%':sgn(mosOf(r),0)}</span></button></li>`;}).join('')}</ul>
       <button class="btn sm ghost mosmore" type="button" aria-controls="mosl" aria-expanded="false" onclick="const o=document.getElementById('mosl').classList.toggle('clip');this.setAttribute('aria-expanded',!o);this.textContent=o?'แสดงทั้งหมด ${sorted.length} ตัว':'แสดงเฉพาะหัว/ท้าย 5 ตัว'">แสดงทั้งหมด ${sorted.length} ตัว</button>
       <p class="note" style="margin:4px 0 0">แกนตัดที่ ±100% · กดชื่อเพื่อไปที่การ์ด</p>`:'<p class="note">ยังไม่มี Fair Value — กด Run Fundamental Refresh</p>'}</div>
   </div></section>
   <section class="pnl s4" aria-label="น้ำหนักตาม Rating" style="display:grid;gap:14px;align-content:start">
     <div><h2>น้ำหนักตาม Rating</h2>
       <div class="stack" role="img" aria-label="${esc(groups.map(g=>g.l+' '+nf(g.w,0)+'%').join(', '))}">${groups.map(g=>`<span style="width:${g.w}%;background:var(${g.c})"></span>`).join('')}</div>
       <div class="legend">${groups.map(g=>`<div><span class="sw" style="background:var(${g.c})"></span><span>${esc(g.l)} <span class="note">${g.n} ตัว</span></span><b class="num">${nf(g.w,1)}%</b></div>`).join('')}</div></div>
     <div class="kv" style="margin:0;border-top:1px solid var(--line);padding-top:10px">
       <div title="margin of safety ระดับพอร์ต = (Σ fair value − Σ มูลค่าตลาด) / Σ fair value (S4)"><span class="cap">Portfolio MoS</span><b class="${pm?upcls(pm.gross):''}">${pm?sgn(pm.gross,1):'–'}</b>${pm?`<span class="note">${pm.n}/${pm.of} ตัวมี FV${pm.net!=null?' · net '+sgn(pm.net,1):''}</span>`:''}</div>
       <div><span class="cap">มูลค่าที่ราคาเกิน FV</span><b>${above==null?'–':nf(above,1)+'%'}</b></div>
       <div><span class="cap">DEEP เฉลี่ย</span><b>${p.avg_composite??'–'}</b></div>
       <div><span class="cap">GARP</span><b>${p.garp_candidates??'–'} ตัว</b></div></div>
   </section></div>`;
}
function cardToolbar(n){
  return `<div class="toolbar" role="toolbar" aria-label="กรองและเรียงการ์ด">
    <label class="sr" for="cardQ">ค้นหา ticker</label><input id="cardQ" class="search" type="search" placeholder="ค้นหา ticker / ชื่อ" value="${esc(VIEW.q)}" oninput="VIEW.q=this.value;saveView();drawCards()">
    <div class="seg" role="group" aria-label="กรองตาม rating">${[["all","ทั้งหมด"],["buy","BUY"],["hold","HOLD"],["sell","SELL"]].map(([k,l])=>`<button type="button" data-g="${k}" aria-pressed="${VIEW.g===k}" onclick="VIEW.g='${k}';saveView();drawCards()">${l}</button>`).join('')}</div>
    <label class="sr" for="cardSort">เรียงตาม</label><select id="cardSort" class="btn" onchange="VIEW.sort=this.value;saveView();drawCards()">${Object.entries(SORTS).map(([k,[l]])=>`<option value="${k}"${VIEW.sort===k?' selected':''}>เรียง: ${l}</option>`).join('')}</select>
    <span class="grow"></span><span class="count" id="cardCount" aria-live="polite"></span></div><div class="cards" id="cards"></div>`;
}
function drawCards(){
  const el=document.getElementById('cards'); if(!el)return;
  const rows=((window.__last||{}).rows)||[], q=VIEW.q.trim().toLowerCase();
  const list=rows.filter(r=>(VIEW.g==='all'||RGROUP[r.recommendation]===VIEW.g)&&(!q||String(r.ticker).toLowerCase().includes(q)||String(r.company||'').toLowerCase().includes(q))).sort((SORTS[VIEW.sort]||SORTS.mos)[1]);
  el.innerHTML=list.length?list.map(x=>pcard(x,false)).join('')
    :`<div class="empty" style="grid-column:1/-1"><b>ไม่พบหุ้นที่ตรงเงื่อนไข</b><span>ลองล้างคำค้นหรือเลือก "ทั้งหมด"</span><button class="btn" type="button" onclick="VIEW.q='';VIEW.g='all';saveView();document.getElementById('cardQ').value='';drawCards()">ล้างตัวกรอง</button></div>`;
  const c=document.getElementById('cardCount'); if(c)c.textContent=`แสดง ${list.length} จาก ${rows.length} ตัว`;
  document.querySelectorAll('[data-g]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.g===VIEW.g));
}
// a MoS bar (or an Allocation weight bar) jumps to its card on My Portfolio
document.addEventListener('click',e=>{
  const g=e.target.closest&&e.target.closest('[data-go]'); if(!g)return;
  if(document.getElementById('p1').style.display==='none')showTab(1);
  let el=document.getElementById('c-'+g.dataset.go.replace(/[^A-Za-z0-9_-]/g,'_'));
  if(!el){VIEW.q='';VIEW.g='all';const q=document.getElementById('cardQ');if(q)q.value='';drawCards();el=document.getElementById('c-'+g.dataset.go.replace(/[^A-Za-z0-9_-]/g,'_'));}
  if(el){el.scrollIntoView({block:'start'});const s=el.querySelector('summary');if(s)s.focus({preventScroll:true});}
});
