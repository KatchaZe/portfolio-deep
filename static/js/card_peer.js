// The card's "คู่แข่ง & Moat" tab: is the competitive position widening or narrowing?
// Reads row.peer_panel and row.moat_trend (domain/peer_panel.py, domain/moat_trend.py;
// docs/peer-panel/SPEC.md). Display only: nothing here feeds a score or a fair value.
// Functions only (no DOM work at load), so tests/test_peer_panel_render.py runs it in node.

const PP_MINUS='−';
const PP_LABEL={improving:['ดีขึ้น','↗','--bull'],stable:['ทรงตัว','→','--base'],eroding:['เสื่อม','↘','--bear']};
function ppLab(l){return PP_LABEL[l]||['ข้อมูลไม่พอ','·','--muted'];}
function ppNum(v){return typeof v==='number'&&isFinite(v);}
function ppSgn(v,d){return ppNum(v)?(v<0?PP_MINUS:v>0?'+':'')+Math.abs(v).toFixed(d==null?1:d):'—';}
function ppPct(f,d){return ppNum(f)?(f*100).toFixed(d==null?1:d)+'%':'—';}
function ppMoney(v){if(!ppNum(v))return '—';const a=Math.abs(v);return a>=1e9?'$'+(v/1e9).toFixed(a>=1e11?0:1)+'B':'$'+(v/1e6).toFixed(0)+'M';}
function ppMult(v){return ppNum(v)?v.toFixed(1)+'×':'—';}
function moatLabel(x){return ((x||{}).moat_trend||{}).label||null;}

// the arrow inside the card face's Moat chip; nothing when there is no label
function moatArrow(x){
  const l=moatLabel(x); if(!PP_LABEL[l])return '';
  const [w,a]=PP_LABEL[l];
  return ` <span class="pp-arrow" aria-hidden="true">${a}</span><span class="sr">แนวโน้ม ${w}</span>`;
}

// S1-S4: Thai name, band in display units, value formatter, value -> display units
const PP_SIG={
  spread_slope:{n:'ROIC − WACC เปลี่ยนต่อปี',s:'S1',th:1.0,u:'%/ปี',fmt:v=>ppSgn(v,2)+'%/ปี',k:v=>v},
  share_change:{n:'ส่วนแบ่งรายได้ในอุตสาหกรรม (เปลี่ยนเทียบส่วนแบ่งเดิม)',s:'S2',th:10,u:'%',fmt:v=>ppSgn(v*100,1)+'% ของเดิม',k:v=>v*100},
  gm_gap_change:{n:'Gross margin เทียบ median คู่แข่ง (เปลี่ยน)',s:'S3',th:2.0,u:'%',fmt:v=>ppSgn(v*100,1)+'%',k:v=>v*100},
  incremental_margin:{n:'Margin ส่วนเพิ่ม เทียบ margin เฉลี่ย',s:'S4',th:5.0,u:'%',fmt:v=>ppSgn(v*100,1)+'%',k:v=>v*100}};
const PP_DIR={'1':'ดีขึ้น','0':'ไม่ชัด','-1':'แย่ลง'};

// a diverging bar in band units, clamped to ±3; dashed lines mark the ±1 band
function ppSigBar(x,dir){
  const c=Math.max(-3,Math.min(3,x)),W=300,mid=150,u=W/6,w=Math.abs(c)*u;
  const fill=dir>0?'--bull':dir<0?'--bear':'--c6';
  return `<svg viewBox="0 0 ${W} 22" preserveAspectRatio="none" aria-hidden="true" focusable="false">
   <rect x="0" y="9" width="${W}" height="4" style="fill:var(--track)"/>
   <rect x="${(c<0?mid-w:mid).toFixed(1)}" y="5" width="${w.toFixed(1)}" height="12" rx="2" style="fill:var(${fill})"/>
   <line x1="${mid}" x2="${mid}" y1="1" y2="21" style="stroke:var(--ink-2);stroke-width:1.5"/>
   <line x1="${mid-u}" x2="${mid-u}" y1="2" y2="20" style="stroke:var(--ctl);stroke-dasharray:3 3"/>
   <line x1="${mid+u}" x2="${mid+u}" y1="2" y2="20" style="stroke:var(--ctl);stroke-dasharray:3 3"/></svg>`;
}

function ppSignals(m){
  const sigs=m.signals||[];
  const why=k=>(m.flags||[]).filter(f=>String(f).startsWith(PP_SIG[k].s+':')).map(f=>String(f).slice(4)).join('; ')||'ไม่มีข้อมูล';
  return Object.keys(PP_SIG).map(k=>{
    const s=sigs.find(z=>z.name===k),D=PP_SIG[k];
    if(!s||!ppNum(s.value))return `<div class="pp-sig"><span class="pp-name">${D.n}</span><span class="note">${esc(why(k))}</span><span class="pp-val note">—</span></div>`;
    const dir=String(s.direction);
    return `<div class="pp-sig" role="img" aria-label="${esc(D.n)}: ${esc(D.fmt(s.value))}, เกณฑ์ ±${D.th}${D.u}, ${PP_DIR[dir]||'ไม่ชัด'}, ช่วง ${esc(s.window||'')}"><span class="pp-name">${D.n}<br><span class="note">${esc(s.window||'')}</span></span>${ppSigBar(D.k(s.value)/D.th,s.direction)}<span class="pp-val num">${esc(D.fmt(s.value))}<br><span class="note">${PP_DIR[dir]||'ไม่ชัด'}</span></span></div>`;
  }).join('');
}

// width (the app's ROIC* - WACC class, card_drawer.js), trend, and the uncertainty rating
function ppHeadline(x){
  const sp=(x.key_metrics||{}).spread_pct, m=x.moat_trend||{}, u=x.uncertainty||{};
  const width=!ppNum(sp)?'—':sp>=10?'กว้าง':sp>=0?'แคบ':'ไม่มี';
  const [w,a]=ppLab(m.label);
  return `<div class="pp-head">
    <div><span class="cap">ความกว้าง moat</span><b>${width}</b><span class="note">วัดจากงบย้อนหลัง${ppNum(sp)?' · ROIC−WACC '+ppSgn(sp,1)+'%':''}</span></div>
    <div><span class="cap">แนวโน้ม</span><b><span aria-hidden="true">${a}</span> ${w}</b><span class="note">${m.label?(m.n_signals||0)+' สัญญาณ':'สัญญาณไม่ถึง 2 ตัว'}</span></div>
    <div><span class="cap">ความไม่แน่นอน</span><b>${esc(u.rating||'—')}</b><span class="note">ของมูลค่า (แท็บ มูลค่า)</span></div></div>`;
}

function ppRanks(p){
  const t=((p.table||{}).rows||[]).find(r=>r.is_target);
  if(!t||!t.rank3)return `<h3 class="pp-sub">ตำแหน่งในกลุ่มคู่แข่ง</h3><p class="note" style="margin:0">ไม่มีตารางคู่แข่ง จึงจัดอันดับไม่ได้</p>`;
  const W={1:'กลุ่มบน',2:'กลุ่มกลาง',3:'กลุ่มล่าง'}, n=p.table.rows.length, tv=p.table.target_vs_median||{};
  const cell=(k,l,extra)=>`<div><span class="cap">${l}</span><b>${W[t.rank3[k]]||'—'}</b><span class="note">${extra}</span></div>`;
  return `<h3 class="pp-sub">ตำแหน่งในกลุ่มคู่แข่ง (${n} บริษัท รวมหุ้นนี้)</h3>
   <div class="pp-ranks">${cell('scale','ขนาด','รายได้ TTM')}${cell('growth','การเติบโต',ppNum(tv.cagr3_fy)?'โต 3 ปี '+ppSgn(tv.cagr3_fy,1)+'% จาก median':'โต 3 ปี')}${cell('margin','Margin',ppNum(tv.op_margin_ttm)?'OPM '+ppSgn(tv.op_margin_ttm,1)+'% จาก median':'OPM TTM')}</div>
   <p class="note" style="margin:0">แบ่ง 3 กลุ่มเท่า ๆ กันตามลำดับ (บน = ดีที่สุดหนึ่งในสาม)</p>`;
}

function ppLead(x){
  const m=x.moat_trend||{}, p=x.peer_panel||{}, [w,a,tok]=ppLab(m.label), sigs=m.signals||[];
  const ups=sigs.filter(s=>s.direction>0).length, dns=sigs.filter(s=>s.direction<0).length;
  const sum=m.label?`สัญญาณดีขึ้น ${ups} · แย่ลง ${dns} · ไม่ชัด ${sigs.length-ups-dns} จาก ${sigs.length} ที่มีข้อมูล (ต้องรวมได้ +2 หรือ ${PP_MINUS}2 จึงจะไม่ใช่ “ทรงตัว”)`:`มีสัญญาณ ${sigs.length} ตัว ต้องมีอย่างน้อย 2 ตัวจึงจะสรุปได้`;
  const watch=(m.watch||[]).length?`<p class="note" style="margin:0"><b>ติดตามไตรมาสหน้า:</b> ${m.watch.map(esc).join(' · ')}</p>`:'';
  return `<div class="box pp-lead pp-s5"><span class="cap">แนวโน้ม moat</span>
     <div class="pp-big" style="color:color-mix(in srgb,var(${tok}) 75%,var(--ink))"><span class="pp-arrow" aria-hidden="true">${a}</span><span>${w}</span></div>
     <span class="pill" style="${tone('--warn')};justify-self:start">เกณฑ์ชั่วคราว · ยังไม่ backtest</span>
     ${ppHeadline(x)}
     <p class="note" style="margin:0">${sum}</p>${watch}
     ${ppRanks(p)}</div>
   <div class="box pp-s7"><h3>สัญญาณ 4 ตัว (เส้นประ = เกณฑ์ ± ที่นับว่าเปลี่ยนจริง)</h3>${ppSignals(m)}</div>`;
}

// R22-R23: the UI writes the Thai sentence from the hint's measures; a hint stored before
// measures existed falls back to its English evidence, translated where the shape is known
const PP_HINT={intangible:'ความได้เปรียบไม่มีตัวตน (แบรนด์/สิทธิบัตร)',scale:'ความได้เปรียบด้านขนาด',
  efficient_scale:'ขนาดตลาดที่พอดี (efficient scale)',cost_advantage:'ความได้เปรียบด้านต้นทุน',
  switching_costs:'ต้นทุนการเปลี่ยนเจ้า',network:'network effect'};
const PP_TREND={'1':['↗','แข็งขึ้น'],'0':['→','ทรงตัว'],'-1':['↘','อ่อนลง']};
function ppHintTh(h){
  const ms={}; (h.measures||[]).forEach(m=>{ms[m.metric]=m;});
  const v=k=>(ms[k]||{}).value, per=k=>esc((ms[k]||{}).period||'');
  if(h.type==='intangible'&&ppNum(v('gm_gap_pp')))return `gross margin สูงกว่า median คู่แข่ง ${v('gm_gap_pp').toFixed(0)}% (${per('gm_gap_pp')}) สอดคล้องกับอำนาจกำหนดราคาจากแบรนด์หรือทรัพย์สินทางปัญญา`;
  if(h.type==='scale'&&ppNum(v('industry_share')))return `${ppPct(v('industry_share'),0)} ของรายได้ทั้งอุตสาหกรรม (บริษัทที่ยื่น SEC, ${per('industry_share')}) สอดคล้องกับความได้เปรียบด้านขนาด`;
  if(h.type==='efficient_scale'&&ppNum(v('top5_share')))return `5 บริษัทใหญ่สุดมีรายได้รวม ${ppPct(v('top5_share'),0)} ของอุตสาหกรรม (${per('top5_share')}) ส่วนแบ่งของหุ้นนี้เปลี่ยน ${ppSgn(v('share_change_pp'),1)}% (${per('share_change_pp')}) ในกลุ่ม ${esc(v('n_members'))} บริษัท สอดคล้องกับตลาดที่รองรับผู้เล่นได้ไม่กี่ราย`;
  if(h.type==='cost_advantage'&&ppNum(v('opm_gap_pp')))return `operating margin สูงกว่า median คู่แข่ง ${v('opm_gap_pp').toFixed(0)}% (TTM) ขณะที่ gross margin ต่างจาก median ${ppSgn(v('gm_gap_pp'),0)}% (${per('gm_gap_pp')}) สอดคล้องกับความได้เปรียบด้านต้นทุน`;
  return esc(String(h.evidence||'')
    .replace(/^gross margin (\d+)pp above the peer median \(FY0\): consistent with pricing power from brand or IP$/,'gross margin สูงกว่า median คู่แข่ง $1% (ปีงบล่าสุด) สอดคล้องกับอำนาจกำหนดราคาจากแบรนด์หรือทรัพย์สินทางปัญญา')
    .replace(/^(\d+)% of industry revenue among US SEC filers \(CY(\d+)\): consistent with a scale advantage$/,'$1% ของรายได้ทั้งอุตสาหกรรม (บริษัทที่ยื่น SEC, CY$2) สอดคล้องกับความได้เปรียบด้านขนาด'));
}
// R24: no hint without excess returns; the server says so in a flag and the tab says it in Thai
const PP_NO_EXCESS='hints: no excess return';
function ppHints(m){
  const h=m.source_hints||[];
  if(!h.length)return (m.flags||[]).some(f=>String(f).startsWith(PP_NO_EXCESS))
    ?`<h3>แหล่ง moat ที่ตัวเลข “สอดคล้อง”</h3><p class="pp-empty">ยังไม่มีกำไรส่วนเกิน (ROIC ≤ WACC) จึงไม่นับเป็นแหล่ง moat</p>`:'';
  const tr=t=>{const x=PP_TREND[String(t)];return x?` · <span aria-hidden="true">${x[0]}</span> ${x[1]}`:(t===null?' · แนวโน้มยังไม่ทราบ':'');};
  return `<h3>แหล่ง moat ที่ตัวเลข “สอดคล้อง” (ไม่ใช่ข้อสรุป)</h3><ul class="pp-hints">${h.map(x=>`<li>${esc(PP_HINT[x.type]||x.type)} <span class="note">(${x.strength==='moderate'?'ปานกลาง':'อ่อน'}${tr(x.trend)})</span><br><span class="note">${ppHintTh(x)}</span></li>`).join('')}</ul>`;
}

function ppShare(p,wide){
  const sp=wide?'pp-s12':'pp-s7', s=p.industry_share;
  const has=s&&s.target_share&&Object.values(s.target_share).some(ppNum);
  if(!has){
    const cov=s?Object.values(s.coverage||{}).filter(ppNum):[];
    const why=!s?'หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran จึงคำนวณส่วนแบ่งไม่ได้'
      :cov.length&&Math.max(...cov)<0.7?`บริษัทในกลุ่ม (${esc(s.n_members)} ราย) มีข้อมูลใน SEC เพียง ${ppPct(Math.max(...cov),0)} ต่ำกว่าเกณฑ์ 70% จึงไม่คำนวณส่วนแบ่ง`
      :'ไม่พบรายได้ของหุ้นนี้ใน SEC frames (เช่น ยื่นงบแบบ 20-F/IFRS หรือใช้ tag รายได้ที่ไม่ได้ดึง) จึงไม่มีส่วนแบ่ง';
    return `<div class="box ${sp}"><h3>ส่วนแบ่งรายได้ในอุตสาหกรรม</h3><p class="pp-empty">${why} · ดูรายละเอียดในหมายเหตุข้อมูล</p></div>`;
  }
  const yrs=s.years||[], ts=yrs.map(y=>s.target_share[y]), t5=yrs.map(y=>(s.top5_share||{})[y]);
  const vals=[...ts,...t5].filter(ppNum);
  const W=520,H=190,L=44,Rm=30,T=12,B=28, ymax=Math.min(1,Math.ceil(Math.max(...vals)*10)/10+0.05);
  const X=i=>L+(yrs.length>1?i*(W-L-Rm)/(yrs.length-1):(W-L-Rm)/2), Y=v=>T+(1-v/ymax)*(H-T-B);
  const line=(arr,tok,dash)=>{let d='';arr.forEach((v,i)=>{if(!ppNum(v))return;d+=(d&&ppNum(arr[i-1])?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1);});
    return (d?`<path d="${d}" style="fill:none;stroke:var(${tok});stroke-width:2.5${dash?';stroke-dasharray:6 4':''}"/>`:'')+arr.map((v,i)=>ppNum(v)?`<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="3.5" style="fill:var(${tok})"/>`:'').join('');};
  const ticks=[0,ymax/2,ymax].map(v=>`<line x1="${L}" x2="${W-Rm}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" style="stroke:var(--line)"/><text x="${L-6}" y="${(Y(v)+4).toFixed(1)}" text-anchor="end" font-size="11" style="fill:var(--muted)">${Math.round(v*100)}%</text>`).join('');
  const xl=yrs.map((y,i)=>`<text x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="middle" font-size="11" style="fill:var(--muted)">CY${y}</text>`).join('');
  const last=yrs.length-1, hhi=(s.hhi||{})[yrs[last]];
  const lbl=`ส่วนแบ่ง CY${yrs[0]} ถึง CY${yrs[last]}: ${ts.map(v=>ppNum(v)?ppPct(v):'ไม่มี').join(', ')}; Top-5 รวม ${ppPct(t5[last])} ปีล่าสุด`;
  const cov=yrs.map(y=>(s.coverage||{})[y]).filter(ppNum);
  const tgt=(((p.table||{}).rows||[]).find(r=>r.is_target)||{}).symbol||'หุ้นนี้';
  return `<div class="box ${sp} pp-chart"><h3>ส่วนแบ่งรายได้ในอุตสาหกรรม (${esc(p.industry||'')})</h3>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(lbl)}">${ticks}${xl}${line(t5,'--c6',true)}${line(ts,'--accent')}</svg>
    <div class="pp-legend"><span><i style="background:var(--accent)"></i>${esc(tgt)}</span><span><i class="dash"></i>Top-5 รวม</span></div>
    <div class="pp-kfig">
      <div><span class="cap">ส่วนแบ่ง CY${yrs[last]}</span><b>${ppPct(ts[last])}</b></div>
      <div><span class="cap">เปลี่ยน 3 ปี</span><b>${ppNum(s.share_change_pp)?ppSgn(s.share_change_pp,2)+'%':'—'}</b><span class="note">กลุ่มเดียวกัน ${ppNum(s.matched_n)?s.matched_n:'—'} บริษัท</span></div>
      <div><span class="cap">Top-5 รวม</span><b>${ppPct(t5[last])}</b><span class="note">${ppNum(s.top5_change_pp)?ppSgn(s.top5_change_pp,1)+'% ใน 3 ปี':''}</span></div>
      <div><span class="cap">HHI</span><b>${ppNum(hhi)?hhi.toFixed(2):'—'}</b><span class="note">${!ppNum(hhi)?'':hhi>0.25?'กระจุกตัวสูง':hhi>0.15?'กระจุกตัวปานกลาง':'กระจายตัว'}</span></div></div>
    <p class="note" style="margin:0">สัดส่วนรายได้ในบริษัทที่ยื่น SEC ในอุตสาหกรรมเดียวกัน (${esc(s.n_members)} ราย, ครอบคลุม ${cov.length?ppPct(Math.min(...cov),0)+'–'+ppPct(Math.max(...cov),0):'—'}) ไม่ใช่ market share จริง</p></div>`;
}

const PP_COLS=[['symbol','บริษัท',''],['revenue_ttm','รายได้ TTM',''],['cagr3_fy','โต/ปี (3 ปี)',''],['gross_margin_fy','GM ปีงบ','pp-hsm'],['op_margin_ttm','OPM TTM',''],['ev_sales','EV/Sales',''],['ev_ebit','EV/EBIT','pp-hsm'],['pe','P/E','pp-hsm']];
const PP_FMT={revenue_ttm:ppMoney,cagr3_fy:v=>ppPct(v),gross_margin_fy:v=>ppPct(v),op_margin_ttm:v=>ppPct(v),ev_sales:ppMult,ev_ebit:ppMult,pe:ppMult};
const PP_SORT={};   // card id -> {k, dir}
function ppSorted(rows,k,dir){
  return [...rows].sort((a,b)=>{const x=a[k],y=b[k];
    if(k==='symbol')return dir*String(x).localeCompare(String(y));
    if(!ppNum(x))return ppNum(y)?1:0; if(!ppNum(y))return -1; return dir*(x-y);});
}
function ppTable(p,sort){
  const rows=(p.table||{}).rows||[];
  if(!rows.length){
    const fl=(p.flags||[]).join(' '), m=fl.match(/(\d+) usable peers \(need (\d+)\)/);
    const why=m?`มีคู่แข่งที่ใช้ได้ ${m[1]} ราย ต้องมีอย่างน้อย ${m[2]} ราย`:/no Damodaran industry/.test(fl)?'หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran':'ข้อมูลคู่แข่งยังไม่ถูกสร้างในรอบ refresh นี้';
    return `<div class="pp-s12 pp-tbl"><p class="pp-empty"><b>ยังไม่มีตารางคู่แข่ง:</b> ${esc(why)}${/no peer list from FMP/.test(fl)?' (รอบนี้ไม่ได้รายชื่อคู่แข่งจาก FMP)':''} · ดูรายละเอียดในหมายเหตุข้อมูล</p></div>`;
  }
  const st=p.table.stats||{}, k=(sort||{}).k||'revenue_ttm', dir=(sort||{}).dir||-1;
  const th=PP_COLS.map(([c,l,cls])=>`<th scope="col" class="${cls}"${k===c?` aria-sort="${dir>0?'ascending':'descending'}"`:''}><button type="button" data-psort="${c}">${l}<span aria-hidden="true">${k===c?(dir>0?' ▲':' ▼'):''}</span></button></th>`).join('');
  const out=r=>r.outlier||[];
  const tr=ppSorted(rows,k,dir).map(r=>`<tr class="${r.is_target?'pp-target':''}">${PP_COLS.map(([c,l,cls],i)=>i===0
      ?`<th scope="row">${esc(r.symbol)}${r.is_target?' <span class="note">(หุ้นนี้)</span>':''}</th>`
      :`<td class="num ${cls}${out(r).includes(c)?' pp-out':''}">${PP_FMT[c](r[c])}${out(r).includes(c)?'<span aria-hidden="true">*</span><span class="sr"> (นอกกรอบ)</span>':''}</td>`).join('')}</tr>`).join('');
  const med=PP_COLS.map(([c,l,cls],i)=>i===0?'<th scope="row">median คู่แข่ง</th>':`<td class="num ${cls}">${st[c]?PP_FMT[c](st[c].median):'—'}</td>`).join('');
  return `<div class="box pp-s12 pp-tbl"><div class="tw"><table class="pp-table"><caption>ตารางคู่แข่ง · กดหัวคอลัมน์เพื่อเรียง</caption><thead><tr>${th}</tr></thead><tbody>${tr}</tbody><tfoot><tr>${med}</tr></tfoot></table></div>
   <p class="note" style="margin:0">* นอกกรอบ IQR 1.5 เท่า · median ไม่รวมหุ้นนี้ · OPM ใช้ EBIT หลังบวกกลับรายการครั้งเดียว · บนมือถือซ่อน GM, EV/EBIT, P/E</p></div>`;
}

function ppNotes(p,m){
  const ev=(m.source_hints||[]).map(h=>h.evidence?`${h.type}: ${h.evidence}`:'').filter(Boolean);
  const u=[...new Set([...(p.flags||[]),...((p.industry_share||{}).flags||[]),...(m.flags||[]),...ev].map(f=>pp2pct(String(f))))];
  if(!u.length)return '';
  return `<details class="box pp-notes"><summary>หมายเหตุข้อมูล (${u.length})</summary><ul>${u.map(f=>`<li>${esc(f)}</li>`).join('')}</ul></details>`;
}

// the whole tab; `id` is the card id (keeps the table's sort between redraws)
function peerPane(x,id){
  x=x||{};
  const p=x.peer_panel||{}, m=x.moat_trend||{}, hb=ppHints(m);
  return `<div class="pp-grid" data-ppcard="${esc(id||'')}">${ppLead(x)}${ppShare(p,!hb)}${hb?`<div class="box pp-s5">${hb}</div>`:''}${ppTable(p,PP_SORT[id])}
    <div class="pp-s12">${ppNotes(p,m)}<p class="note pp-foot">ข้อมูลจากงบที่ยื่น SEC (10-K/10-Q) และรายชื่ออุตสาหกรรมของ Damodaran · peer panel ณ ${esc(p.as_of||'—')} · ใช้ประกอบการตัดสินใจ ไม่มีผลต่อคะแนน DEEP หรือ Fair Value</p></div></div>`;
}
// a sort button redraws only the table and keeps focus on the same column
function ppSortClick(btn){
  const grid=btn.closest('.pp-grid'), id=grid&&grid.dataset.ppcard, x=id&&CARD_ROWS[id]; if(!x)return;
  const k=btn.dataset.psort, cur=PP_SORT[id]||{k:'revenue_ttm',dir:-1};
  PP_SORT[id]=cur.k===k?{k,dir:-cur.dir}:{k,dir:k==='symbol'?1:-1};
  grid.querySelector('.pp-tbl').outerHTML=ppTable(x.peer_panel||{},PP_SORT[id]);
  const nb=grid.querySelector(`[data-psort="${k}"]`); if(nb)nb.focus();
}
if(typeof document!=='undefined')document.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('[data-psort]'); if(b)ppSortClick(b);});
