// The Correlation tab: is the book balanced or bunched? It reads the same /api/risk payload
// as the Risk Desk (fetchRiskPayload), in Normal or measured-Crisis mode.
let __corr=null, corrMode='normal';
async function loadCorr(force){
  const body=document.getElementById('corrBody'); if(!body)return;
  const sp=document.getElementById('spCorr'); if(sp)sp.style.display='inline-block';
  const en=(document.getElementById('corrEnrich')||{}).value||'auto';
  body.innerHTML='<div class="empty">Computing correlation… (price history + covariance)</div>';
  try{
    const d=await fetchRiskPayload(en,!!force);   // shared with Risk Desk — no second /api/risk build
    __corr=d; if(d.quota)renderQuota(d.quota); renderCorr(d);
  }catch(e){body.innerHTML='<div class="banner tone t-bear">Error: '+esc(String(e))+'</div>';}
  finally{if(sp)sp.style.display='none';}
}
function setCorrMode(m){corrMode=m;
  for(const [id,k] of [['cmNormal','normal'],['cmCrisis','crisis']]){const b=document.getElementById(id);if(b)b.setAttribute('aria-pressed',k===m);}
  if(__corr)renderCorr(__corr);
}
// diverging tint: red = moves together, blue = offsets; text stays --ink (AA at ≤50% tint)
function corrBg(v){if(v==null)return 'background:var(--track)';const p=Math.round(Math.min(1,Math.abs(v))*50);return `background:color-mix(in srgb,var(${v>=0?'--bear':'--accent'}) ${p}%,var(--surface))`;}
function corrNum(v){if(v==null)return '—';if(Math.abs(v)<0.005)return '.00';return nf(v,2).replace(/^0\./,'.').replace(/^-0\./,'−.');}
function corrCell(v,extra){return `<td style="${corrBg(v)}" title="${plainNum(v,2)} (${corrMode})"${extra||''}>${corrNum(v)}</td>`;}
const modeTag=()=>chip(corrMode.toUpperCase(),corrMode==='crisis'?'--bear':'--info');

function renderCorr(d){
  const body=document.getElementById('corrBody'); if(!body)return;
  if(!d||d.status==='insufficient'){body.innerHTML='<div class="empty">'+esc((d&&d.message)||'ต้องมี ≥2 holding ที่มีราคา — เพิ่ม holding หรือกด Run Daily ก่อน')+'</div>';return;}
  const co=d.correlation;
  if(!co||co.status==='error'){body.innerHTML='<div class="banner tone t-bear">Correlation unavailable: '+esc((co&&co.message)||'—')+'</div>';return;}
  body.innerHTML=corrProxyWarnHTML(co)+`<div class="grid12">${corrPhilHTML(co,d)}${corrHeatHTML(co)}
    <div class="s4" style="display:grid;gap:14px;align-content:start">${corrPairsHTML(co)}${corrSectorHTML(co,d)}</div>
    ${corrBenchHTML(co)}${corrFxHTML(co)}${corrLegendHTML(co)}</div>`;
}

// where the Crisis numbers come from: a measured market episode, or the assumed floor
function crisisSourceText(co){
  const c=(((co||{}).meta||{}).crisis)||{};
  if(c.method==='measured'||c.method==='mixed')
    return `Crisis = วัดจริงช่วง ${esc(c.first_day||'')} → ${esc(c.last_day||'')} (S&amp;P ${c.market_drawdown_pct}% , ${c.window_days} วันทำการ)${c.assumed_pairs?` · ${c.assumed_pairs} คู่ข้อมูลไม่พอ: ใช้ค่าสมมติ (equity↔equity ≥ 0.9, คู่อื่นใช้ค่าปกติ)`:''}`;
  return 'Crisis = ค่าสมมติ: คู่ equity↔equity ≥ 0.9 (ไม่มีราคาย้อนหลังช่วงวิกฤตให้วัด)';
}
function corrProxyBadge(co){
  const cm=((co||{}).meta||{}).cov_mode;
  if(!cm||cm==='realized')return '';
  return chip(cm==='proxy'?'PROXY':'MIXED','--warn','history ไม่พอ — ใช้ค่าสมมติ equity↔equity 0.60 / ข้าม class 0.20')+' ';
}
function corrProxyWarnHTML(co){
  const m=(co||{}).meta||{}; const cm=m.cov_mode;
  if(!cm||cm==='realized')return '';
  const miss=(m.proxy_tickers||[]).map(esc).join(', ');
  const what=cm==='proxy'?'<b>ทั้งตาราง</b>เป็นค่าสมมติ (ดึงราคาย้อนหลังไม่ได้เลย)':'บางคู่เป็นค่าสมมติ (history ไม่ครบ)';
  return `<div class="banner tone t-warn" style="margin-bottom:12px">&#9888; <b>${cm==='proxy'?'PROXY':'MIXED'} — ประมาณการ ไม่ใช่ correlation ที่คำนวณจริง</b> · ${what} · คู่สมมติ: equity↔equity 0.60 / ข้าม class 0.20${miss?' · ขาด history: '+miss:''} · กด Run correlation ใหม่ได้ — ระบบจะ retry เฉพาะตัวที่ขาด</div>`;
}

function corrPhilHTML(co,d){
  const ph=co.philosophy||{}, g=ph.gauge||{}, mode=corrMode;
  const enb=(mode==='crisis'?g.enb_crisis:g.enb), ratio=(g.eff_n&&enb)?(enb/g.eff_n):null;
  const rc=ratio==null?['','—']:ratio>=0.7?['up','ดี']:ratio>=0.5?['warnc','เฝ้าระวัง']:['down','กระจุก'];
  const ap=(co.avg_pairwise||{})[mode], dwn=((co.benchmark||{}).downside||{}).downside;
  const story=mode==='crisis'?ph.story_crisis:ph.story_normal, frag=g.fragility_pct;
  const dv=(d&&d.diversification)||{};
  const pills=(ph.pillars||[]).map(p=>`<div class="kpi"><span class="cap">${esc(p.name)} · ${esc(p.s)}</span><b style="font-size:var(--t-sm);font-family:var(--f-body)">${esc(p.value)}</b><span class="note">${esc(p.note)}</span></div>`).join('');
  const mg=(co.marginal||[]);
  const a1=mg.length?`<details style="margin-top:12px"><summary class="note" style="cursor:pointer">A1 · %Capital vs %Risk (S2/S4 marginal) — ${mg.length} ตัว</summary>
    <div style="margin-top:8px">${pairBarsHTML(mg.map(r=>({l:r.ticker,a:r.cap_pct,b:r.risk_pct,tok:(r.risk_pct||0)>(r.cap_pct||0)?'--bear':'--bull',av:pctFixed(r.cap_pct,0),bv:pctFixed(r.risk_pct,0),d:sgn((r.risk_pct||0)-(r.cap_pct||0),1),dcls:(r.risk_pct||0)>(r.cap_pct||0)?'down':'up'})),'%Capital เทียบ %Risk')}</div></details>`:'';
  return `<section class="pnl s12" aria-labelledby="hPhil"><div class="phead"><h2 id="hPhil">Diversification Philosophy <span class="note">ราย "พอร์ต" ตามเสา Damodaran — คู่กับ VALUE→QUALITY→TIMING ราย "หุ้น"</span></h2>${modeTag()}</div>
    <p class="tagsrc" style="margin:0 0 4px">${crisisSourceText(co)}</p>
    ${kpisHTML([[`ENB ÷ Eff N (${mode})`,`<span class="${rc[0]}">${plainNum(ratio,2)} ${rc[1]}</span>`,'',`ENB ${plainNum(enb,1)} / Eff N ${plainNum(g.eff_n,1)}`],
      ['Fragility ΔENB · N→Crisis',`${plainNum(g.enb,1)}→${plainNum(g.enb_crisis,1)}`,'down',esc(g.fragility_text||(frag!=null?('การกระจายหาย ~'+frag+'% ตอนวิกฤต'):'corr วิ่งเข้า 1'))],
      [`avg corr (${mode})`,plainNum(ap,2),'',`downside corr ${plainNum(dwn,2)} · S4`],
      ['Diversification ratio',plainNum(mode==='crisis'?dv.dr_crisis:dv.dr_normal,2)],
      g.r2_nasdaq!=null?['R² เทียบ Nasdaq',plainNum(g.r2_nasdaq,2),'',g.r2_nasdaq>=0.7?'แทบเป็น index':'']:null])}
    <p class="explain" style="margin-top:10px">${esc(story||'')}</p>
    <div class="pillars">${pills}</div>${a1}</section>`;
}

function corrHeatHTML(co){
  const order=co.order||[], mode=corrMode, mat=(mode==='crisis'?co.crisis_matrix:co.matrix)||[], secs=co.sectors||{};
  const sepC=j=>(j>0&&secs[order[j]]!==secs[order[j-1]])?' secL':'';
  let h=`<thead><tr><th scope="col"><span class="sr">ticker</span></th>${order.map((c,j)=>`<th scope="col" class="vh${sepC(j)}"><span>${esc(c)}</span></th>`).join('')}</tr></thead><tbody>`;
  order.forEach((r,i)=>{
    const sepR=(i>0&&secs[r]!==secs[order[i-1]])?' secT':'';
    h+=`<tr><th scope="row" class="num${sepR}" title="${esc(secs[r]||'')}">${esc(r)}</th>`;
    order.forEach((c,j)=>{const v=(mat[i]||[])[j];const cl=(sepC(j)+sepR).trim();
      h+=i===j?`<td class="diag ${cl}" title="${esc(r)}">1</td>`:`<td class="${cl}" style="${corrBg(v)}" title="${esc(r)} × ${esc(c)} = ${plainNum(v,2)} (${mode})">${corrNum(v)}</td>`;});
    h+='</tr>';
  });
  const groups=[];let prev=null;order.forEach(t=>{if(secs[t]!==prev){groups.push(secs[t]);prev=secs[t];}});
  return `<section class="pnl s8" aria-labelledby="hHeat"><div class="phead"><h2 id="hHeat">Correlation Heatmap — รายตัว <span class="note">จัดกลุ่มตาม sector</span></h2><span>${corrProxyBadge(co)}${modeTag()}</span></div>
    <div class="tw" tabindex="0" role="region" aria-label="ตาราง correlation ${order.length}×${order.length}"><table class="heat"><caption class="sr">correlation รายคู่ โหมด ${mode}</caption>${h}</tbody></table></div>
    <div class="hmleg"><span>−1</span><span class="hmbar" aria-hidden="true"></span><span>+1</span><span class="note">แดง = วิ่งไปด้วยกัน · น้ำเงิน = ถ่วงกัน (diversifier) · เส้นแบ่ง = sector: ${groups.map(esc).join(' → ')}</span></div></section>`;
}

function corrSectorHTML(co,d){
  const mode=corrMode, sa=((co.sector_avg||{})[mode])||{}, bysec=((d.allocation||{}).by_sector)||[];
  const rows=bysec.map(s=>{const a=(sa[s.label]||{}).avg, n=(sa[s.label]||{}).n;
    return {l:s.label,v:s.value,tok:s.value>=45?'--bear':s.value>=30?'--warn':'--accent',title:`corr เฉลี่ยในกลุ่ม ${a==null?'—':plainNum(a,2)}${n?' ('+n+' คู่)':''}`,a};});
  return `<section class="pnl" aria-labelledby="hSec"><h2 id="hSec">Sector Summary</h2><p class="note" style="margin:2px 0 8px">แถบ = %น้ำหนักพอร์ต · ขวา = avg corr ในกลุ่ม</p>
    ${hbarsHTML(rows,{fmt:(v,x)=>`${nf(v,1)}% · <span class="${x.a==null?'muted':x.a>=0.7?'down':x.a>=0.5?'warnc':'up'}">${x.a==null?'—':plainNum(x.a,2)}</span>`,label:'น้ำหนักและ correlation ภายใน sector',vw:'8.5em'})}</section>`;
}

function corrPairsHTML(co){
  const mode=corrMode, p=((co.pairs||{})[mode])||{high:[],low:[]};
  const bars=(list,tok,lab)=>hbarsHTML((list||[]).map(x=>({l:x[0]+' × '+x[1],v:x[2],tok,cls:''})),{max:1,fmt:v=>plainNum(v,2),label:lab});
  return `<section class="pnl" aria-labelledby="hPairs"><h2 id="hPairs">คู่สัมพันธ์ สูงสุด / ต่ำสุด</h2><p class="note" style="margin:2px 0 8px">คู่ต่ำ = ตัวช่วยกระจายจริง</p>
    <div class="box"><h3 class="down">วิ่งไปด้วยกันมากสุด</h3>${bars(p.high,'--bear','คู่ correlation สูงสุด')}</div>
    <div class="box" style="margin-top:12px"><h3 class="up">ถ่วงกันดีสุด</h3>${bars(p.low,'--accent','คู่ correlation ต่ำสุด')}</div></section>`;
}

function corrBenchHTML(co){
  const b=co.benchmark; if(!b)return '';
  const mode=corrMode, refs=b.refs||[], order=co.order||[];
  const pv=(o)=>o?(mode==='crisis'?o.crisis:o.normal):null;
  const star=(o)=>(mode==='crisis'&&o&&o.crisis_measured===false)?'*':'';
  const chips=hbarsHTML(refs.map(r=>{const o=(b.portfolio||{})[r.sym];return {l:`${r.name} · ${r.sym}`,v:pv(o),tok:'--c1',star:star(o)};}),{max:1,fmt:(v,x)=>plainNum(v,2)+x.star,label:'correlation พอร์ตกับ benchmark'});
  // benchmarks down the side, holdings across (same order as the heatmap) so the table runs wide
  let hb=`<thead><tr><th scope="col"><span class="sr">benchmark</span></th>${order.map(t=>`<th scope="col" class="vh"><span>${esc(t)}</span></th>`).join('')}</tr></thead><tbody>`;
  refs.forEach(r=>{hb+=`<tr><th scope="row" class="num" title="${esc(r.name)}">${esc(r.sym)}</th>${order.map(t=>corrCell(pv(((b.holdings||{})[t]||{})[r.sym]))).join('')}</tr>`;});
  hb+='</tbody>';
  let rr=`<thead><tr><th scope="col"><span class="sr">asset</span></th>${refs.map(r=>`<th scope="col" class="vh"><span>${esc(r.sym)}</span></th>`).join('')}</tr></thead><tbody>`;
  refs.forEach(a=>{rr+=`<tr><th scope="row" class="num">${esc(a.sym)}</th>`+refs.map(bb=>{if(a.sym===bb.sym)return '<td class="diag">1.00</td>';const key=(b.refref||{})[a.sym+'-'+bb.sym]||(b.refref||{})[bb.sym+'-'+a.sym];return corrCell(pv(key));}).join('')+'</tr>';});
  rr+='</tbody>';
  return `<section class="pnl s12" aria-labelledby="hBench"><div class="phead"><h2 id="hBench">Benchmark &amp; Asset-Class Correlation <span class="note">S&amp;P 500 · Nasdaq-100 · Gold · Bitcoin (IBIT)</span></h2>${modeTag()}</div>
    <div class="dgrid"><div class="box"><h3>A · พอร์ตทั้งก้อน vs benchmark</h3>${chips}</div>
      <div class="box"><h3>C · สินทรัพย์ vs สินทรัพย์</h3><div class="tw" tabindex="0" role="region" aria-label="correlation ระหว่างสินทรัพย์อ้างอิง"><table class="heat">${rr}</table></div></div></div>
    <div class="box" style="margin-top:14px"><h3>B · แต่ละหุ้น vs benchmark <span class="note">แถว = benchmark · คอลัมน์ = หุ้น (เรียงตาม sector เหมือน heatmap)</span></h3><div class="tw" tabindex="0" role="region" aria-label="correlation แต่ละหุ้นกับ benchmark"><table class="heat heatw">${hb}</table></div></div>
    <p class="note" style="margin:10px 0 0"><b>อ่านการ์ดนี้</b> — ${crisisSourceText(co)} · ค่าที่มี * = ข้อมูลช่วงวิกฤตไม่พอ จึงใช้ค่าสมมติ · ตัวที่ crisis corr ต่ำกว่า 0.30 = ตัวถ่วงจริง (S38–41) · IBIT = spot-BTC ETF (แทน BTC-USD, ปฏิทินตรงวันหุ้น)</p></section>`;
}

function corrLegendHTML(co){
  const rollAll=((co.rolling||{}).port_vs_spy)||[], roll=rollAll.filter(v=>v!=null);
  let chart='<p class="note">ไม่มีข้อมูล rolling</p>';
  if(roll.length>2){
    const W=460,H=150,L=30,R=12,T=10,B=126, mn=Math.min(0,...roll), mx=1, X=i=>L+i/(rollAll.length-1)*(W-L-R), Y=v=>B-(v-mn)/(mx-mn)*(B-T);
    let s=[0,.5,1].map(v=>`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${L-5}" y="${Y(v)+3}" text-anchor="end">${v}</text>`).join('');
    const pts=rollAll.map((v,i)=>v==null?null:`${X(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean).join(' ');
    s+=`<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts}"/><text x="${L}" y="${H-4}">เก่า</text><text x="${W-R}" y="${H-4}" text-anchor="end">ล่าสุด ${plainNum(roll[roll.length-1],2)}</text>`;
    chart=svgChart(W,H,`rolling 60 วัน correlation พอร์ตกับ S&P จาก ${plainNum(roll[0],2)} เป็น ${plainNum(roll[roll.length-1],2)}`,s)
      +`<p class="note" style="margin:4px 0 0">${plainNum(roll[0],2)} → ${plainNum(roll[roll.length-1],2)} · ${roll[roll.length-1]>roll[0]?'&#9650; กำลังไต่ขึ้น (เตือน)':'&#9660; / ทรงตัว'} · ใกล้ 1 = พอร์ตเคลื่อนตามตลาดแทบทั้งหมด</p>`;
  }
  return `<section class="pnl s12" aria-labelledby="hRoll"><div class="phead"><h2 id="hRoll">A7 · Rolling 60d correlation พอร์ต vs S&amp;P</h2></div>
    <div class="dgrid"><div>${chart}</div>
    <p class="explain"><b>Damodaran S2–4 · S41:</b> heatmap = marginal risk เชิงภาพ (ยิ่งแดง = วิ่งตามกัน) · ปุ่ม <b>Crisis</b> = correlation ที่วัดในช่วงตลาดพังจริง (ไม่ใช่ค่าเฉลี่ยปกติ) · ${crisisSourceText(co)} · ENB ต่ำกว่า Eff N เพราะ correlation กินการกระจาย</p></div></section>`;
}
