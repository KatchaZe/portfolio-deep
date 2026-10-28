// The card's "DEEP & ทุน" tab (sub-score audit trail, moat: ROIC against WACC now, on new
// capital and at terminal, cost-of-capital chips) and the "แนวโน้ม 5 ปี" tab.

// Every metric chip carries a tooltip, and a missing value says why it is missing.
const KM_TIP={
  'Ke':'Cost of equity = Rf + beta x (ERP + country risk premium)',
  'WACC':'Ke x (E/V) + Kd-after-tax x (D/V) — the bar ROIC has to clear',
  'Kd a-t':'Cost of debt after tax = (Rf + default spread) x (1 - tax)',
  'ROE':'ธนาคาร/ประกัน: ROE = กำไรสุทธิ / equity ต้นปี (เงินฝากคือวัตถุดิบ ไม่ใช่ทุน)',
  'Ke (bar)':'ธนาคาร/ประกัน: ใช้ Ke เป็นต้นทุนเงินทุน (WACC ไม่มีความหมาย)',
  'ROIC*':'ROIC ที่ปรับแล้ว (normalized) — อาจต่างจาก "R&D-capitalized ROIC" บรรทัดแรกของ audit trail ซึ่งเป็นค่าก่อน normalize',
  'EVA':'Economic Value Added = (ROIC − WACC) x invested capital บนฐานเดียวกัน (R&D-capitalized ทั้งคู่ถ้ามี) — $ ต่อปี · ธนาคาร: (ROE − Ke) x equity',
  'Spread':'ROIC* − WACC (ธนาคาร: ROE − Ke). บวก = สร้างมูลค่า · ลบ = ทำลายมูลค่า',
  'Beta':'bottom-up beta ของอุตสาหกรรม (Damodaran) ที่ valuation ใช้',
  'CRP':'country risk premium ถ่วงตามรายได้รายประเทศ',
  'SBC % rev':'stock-based compensation ต่อรายได้ (นับครั้งเดียวเป็น dilution)',
  'Justified P/E':'P/E ที่สมเหตุผลจาก growth, ROIC และ Ke',
};
const KM_MISSING={
  'ROIC*':'วัดไม่ได้ — ขาดข้อมูล invested capital หรือ NOPAT',
  'Spread':'วัดไม่ได้ — ต้องมีทั้ง ROIC* และ WACC',
  'EVA':'วัดไม่ได้ — ต้องมีทั้ง spread และ invested capital',
};
function metricCard(label,val){
  const missing=String(val).indexOf('mdash')>=0;
  const tip=(KM_TIP[label]||label)+(missing&&KM_MISSING[label]?(' — '+KM_MISSING[label]):'');
  return `<div class="kchip" title="${tip.replace(/"/g,'&quot;')}"><span class="cap">${label}</span><b>${val}</b></div>`;}

const PILLAR={D:'Demand',E_exec:'Execution',E_econ:'Economics',P:'Price'};
// "D base(growth 39%)=5.0" -> pillar D, note "base(growth 39%)", adjustment 5.0
function subscoreBreakdown(notes){
  if(!notes.length)return '<p class="note" style="margin:0">No breakdown — run a fundamentals refresh on v8.2.</p>';
  const ADJ=/=\s*(-?\+?[0-9.]+)\s*$/;
  const g={D:[],E_exec:[],E_econ:[],P:[],other:[]};
  notes.forEach(n=>{const s=pp2pct(String(n)),m=s.match(/^(D|E_exec|E_econ|P) (.*)$/);(m?g[m[1]]:g.other).push(m?m[2]:s);});
  const item=s=>{const mm=s.match(ADJ),adj=mm?parseFloat(mm[1]):0;
    const tok=mm?(adj>0?'--bull':adj<0?'--bear':'--muted'):'--muted';
    return `<li><span>${esc(s.replace(ADJ,''))}</span><b style="color:color-mix(in srgb,var(${tok}) 75%,var(--ink))">${mm?esc(mm[1]):''}</b></li>`;};
  return `<div class="dgrid four">${['D','E_exec','E_econ','P'].map(p=>`<div class="box" data-pillar="${p}"><h3>${PILLAR[p]} <span class="num" data-score="${p}"></span></h3>${g[p].length?`<ul class="lines">${g[p].map(item).join('')}</ul>`:'<p class="note" style="margin:0">ไม่มีรายการ</p>'}</div>`).join('')}</div>`
    +(g.other.length?`<p class="note" style="margin:8px 0 0">${g.other.map(esc).join(' · ')}</p>`:'');
}

// ROIC (dot) against WACC (ring) for now / new capital / terminal: the gap is the moat
function moatDumbbell(rowsD){
  const vals=rowsD.flatMap(d=>[d.roic,d.wacc]).filter(v=>v!=null);
  if(!vals.length)return '';
  const mn=Math.min(0,...vals), mx=Math.max(...vals)*1.12||1;
  const W=460,L=104,R=58,rh=34,T=18,H=T+rowsD.length*rh+18, X=v=>L+(v-mn)/(mx-mn)*(W-L-R);
  let s='';
  const st=niceStep((mx-mn)/5); for(let v=Math.ceil(mn/st)*st;v<=mx;v+=st)s+=`<line x1="${X(v)}" x2="${X(v)}" y1="${T-6}" y2="${H-16}" stroke="var(--line)"/><text x="${X(v)}" y="${H-3}" text-anchor="middle">${nf(v,0)}%</text>`;
  rowsD.forEach((d,i)=>{const y=T+i*rh+rh/2;
    s+=`<text x="${L-10}" y="${y+4}" text-anchor="end" class="strong">${esc(d.label)}</text>`;
    if(d.roic==null||d.wacc==null){s+=`<text x="${L}" y="${y+4}">ไม่มีข้อมูล</text>`;return;}
    const good=d.roic>=d.wacc, col=good?'var(--bull)':'var(--bear)';
    s+=`<line x1="${X(d.wacc)}" x2="${X(d.roic)}" y1="${y}" y2="${y}" stroke="${col}" stroke-width="6" stroke-linecap="round" opacity=".55"/>`;
    s+=`<circle cx="${X(d.wacc)}" cy="${y}" r="6" fill="var(--surface)" stroke="var(--ink)" stroke-width="2"><title>WACC ${nf(d.wacc,1)}%</title></circle><circle cx="${X(d.roic)}" cy="${y}" r="6.5" fill="${col}"><title>ROIC ${nf(d.roic,1)}%</title></circle>`;
    s+=`<text x="${W-R+8}" y="${y+4}" class="${good?'bull':'bear'}">${sgn(d.roic-d.wacc,1)}</text>`;});
  return svgChart(W,H,'ROIC เทียบ WACC: '+rowsD.map(d=>`${d.label} ROIC ${nf(d.roic,1)}% WACC ${nf(d.wacc,1)}%`).join(', '),s);
}
// the filed 5-year ROIC against today's WACC: is the moat persistent?
function roicHistory(x){
  const t=((x.trend5y||{}).rows||[]).find(r=>r.key==='roic'||/ROIC/i.test(r.label||'')), k=x.key_metrics||{};
  if(!t||k.wacc_pct==null)return null;
  const p=(t.points||[]).filter(q=>q.v!=null); if(p.length<2)return null;
  const vs=[...p.map(q=>q.v),k.wacc_pct], mn=Math.min(0,...vs), mx=Math.max(...vs)*1.15;
  const W=460,H=170,L=34,R=64,T=12,B=146, X=i=>L+i/(p.length-1)*(W-L-R), Y=v=>B-(v-mn)/(mx-mn)*(B-T);
  let s='';
  const st=niceStep((mx-mn)/4); for(let v=Math.ceil(mn/st)*st;v<=mx;v+=st)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${L-5}" y="${Y(v)+3}" text-anchor="end">${nf(v,0)}%</text>`;
  const area=p.map((q,i)=>`${X(i)},${Y(Math.max(q.v,k.wacc_pct))}`).join(' ')+' '+p.map((q,i)=>`${X(i)},${Y(k.wacc_pct)}`).reverse().join(' ');
  s+=`<polygon points="${area}" fill="var(--bull)" opacity=".14"/>`;
  s+=`<line x1="${L}" x2="${W-R}" y1="${Y(k.wacc_pct)}" y2="${Y(k.wacc_pct)}" stroke="var(--ink)" stroke-dasharray="5 4" stroke-width="1.5"/><text x="${W-R+6}" y="${Y(k.wacc_pct)+4}" class="strong">WACC ${nf(k.wacc_pct,1)}%</text>`;
  s+=`<polyline fill="none" stroke="var(--accent)" stroke-width="2.5" points="${p.map((q,i)=>X(i)+','+Y(q.v)).join(' ')}"/>`;
  p.forEach((q,i)=>{s+=`<circle cx="${X(i)}" cy="${Y(q.v)}" r="4" fill="${q.v>=k.wacc_pct?'var(--bull)':'var(--bear)'}" stroke="var(--surface)" stroke-width="1.5"><title>FY${esc(q.fy)} ROIC ${nf(q.v,1)}%</title></circle><text x="${X(i)}" y="${B+14}" text-anchor="middle">${esc(q.fy)}</text>`;});
  const l=p[p.length-1]; s+=`<text x="${X(p.length-1)+8}" y="${Y(l.v)-6}" class="strong">ROIC ${nf(l.v,1)}%</text>`;
  return {svg:svgChart(W,H,`ROIC ${p.length} ปี เทียบ WACC ${nf(k.wacc_pct,1)}%: `+p.map(q=>'FY'+q.fy+' '+nf(q.v,1)+'%').join(', '),s),above:p.filter(q=>q.v>=k.wacc_pct).length,n:p.length};
}
function deepDrawer(x){
  const km=x.key_metrics||{}, sc=x.subscores||{};
  const fin=km.roic_basis==='roe_book_equity';
  const evaTxt=(x.eva==null)?'&mdash;':compactNumber(x.eva,'$',true);
  // kd_pct is PRE-tax; the after-tax figure is what the WACC uses
  const chips=(fin
    ?[['Ke',signedPct(km.ke_pct)],['ROE',signedPct(km.roic_pct)],['Spread',signedPct(km.spread_pct)],['EVA',evaTxt]]
    :[['Ke',signedPct(km.ke_pct)],['WACC',signedPct(km.wacc_pct)],['Kd a-t',signedPct(km.kd_after_pct)],
      ['ROIC*',signedPct(km.roic_adj_pct!=null?km.roic_adj_pct:km.roic_pct)],['Spread',signedPct(km.spread_pct)],['EVA',evaTxt],
      ['Beta',km.beta!=null?plainNum(km.beta,2):'&mdash;'],['CRP',km.crp_pct!=null?km.crp_pct+'%':'&mdash;'],
      ['SBC % rev',km.sbc_pct_of_rev!=null?km.sbc_pct_of_rev+'%':'&mdash;'],['Justified P/E',km.justified_pe!=null?km.justified_pe+'×':'&mdash;']])
    .map(c=>metricCard(c[0],c[1])).join('')+`<div class="kchip" title="Earnings quality (cash conversion / accruals / SBC)"><span class="cap">Earnings quality</span><b>${esc(x.eq_verdict||'—')}</b></div>`;
  const sp=km.spread_pct, roic=km.roic_adj_pct!=null?km.roic_adj_pct:km.roic_pct;
  const moat=sp==null?null:sp>=10?['--bull','Moat กว้าง','ROIC สูงกว่าต้นทุนเงินทุนมาก — มีความได้เปรียบชัด']:sp>=0?['--base','Moat แคบ','สร้างมูลค่าเหนือทุนได้ แต่ส่วนต่างไม่มาก']:['--bear','ไม่มี moat','ROIC ต่ำกว่า WACC — ยิ่งโตยิ่งทำลายมูลค่า'];
  const tW=km.terminal_wacc_pct??km.wacc_pct, tR=km.terminal_roic_pct, inc=km.incremental_roic_pct;
  const dd=moatDumbbell(fin?[{label:'ROE vs Ke',roic:km.roic_pct,wacc:km.ke_pct}]
    :[{label:'ตอนนี้',roic,wacc:km.wacc_pct},{label:'เงินลงทุนใหม่',roic:inc,wacc:km.wacc_pct},{label:'Terminal',roic:tR,wacc:tW}]);
  const incTxt=inc!=null&&km.wacc_pct!=null?`เงินลงทุนใหม่ได้ ROIC ${nf(inc,1)}% ${inc>=km.wacc_pct?'สูงกว่า':'ต่ำกว่า'}ทุน. `:'';
  const termTxt=tR!=null&&tW!=null?(tR-tW>0.5?`ระยะยาวโมเดลให้ ROIC ลดเหลือ ${nf(tR,1)}% ยังสูงกว่า WACC ${nf(tW,1)}% (moat คงอยู่บางส่วน).`:`ระยะยาวโมเดลให้ ROIC ${nf(tR,1)}% ≈ WACC ${nf(tW,1)}% (การโตหลังจากนั้นไม่เพิ่มมูลค่า).`):'';
  const hist=fin?null:roicHistory(x);
  const scores=x.pillars||{};
  const breakdown=subscoreBreakdown(sc.breakdown||[]);
  return `${breakdown.replace(/<span class="num" data-score="(\w+)"><\/span>/g,(m,p)=>scores[p]!=null?`<span class="num">${nf(scores[p],2)}</span><span class="note">/5</span>`:'')}
    <div class="moatwrap">
      <div class="box"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><h3>${fin?'ROE เทียบ Ke':'ROIC เทียบ WACC'}</h3>${moat?`<span class="pill" style="${tone(moat[0])}">${moat[1]} · ${sgn(sp,1)}</span>`:''}</div>
        ${dd||'<p class="note" style="margin:0">ไม่มี ROIC / WACC</p>'}
        <p class="explain">${moat?esc(moat[2])+'. ':''}${esc(incTxt)}${esc(termTxt)}</p>
        <p class="note" style="margin:0">จุดทึบ = ROIC · วงกลม = WACC · ตัวเลขขวา = ROIC − WACC · ROIC ${esc(km.roic_basis||'')}</p></div>
      <div class="box">${hist?`<h3>ROIC ${hist.n} ปี เทียบ WACC ปัจจุบัน <span class="note">ROIC จากงบรายปี (คนละฐานกับ ROIC* ด้านซ้าย)</span></h3>${hist.svg}<p class="explain">ROIC สูงกว่า WACC <b>${hist.above} จาก ${hist.n} ปี</b> — ${hist.above===hist.n?'สร้างมูลค่าต่อเนื่องตามงบ':hist.above===0?'ไม่เคยทำได้เกินต้นทุนทุน':'ทำได้บางปี ยังไม่สม่ำเสมอ'}${hist.above>0&&sp!=null&&sp<0?' · แต่ ROIC* ที่ปรับแล้ว (ฝั่งซ้าย) ต่ำกว่า WACC — ROIC ตามงบอาจดูดีเกินจริง ใช้ฝั่งซ้ายตัดสิน moat':''}</p>`:'<p class="note" style="margin:0">ไม่มี ROIC ย้อนหลังจากงบ</p>'}</div>
    </div>
    <div class="kchips">${chips}</div>
    <p class="note" style="margin:0">*ROIC = ปรับแล้ว (normalized) · FCFF&rarr;WACC, FCFE&rarr;Ke${km.damodaran_industry?` · อุตสาหกรรม Damodaran: ${esc(km.damodaran_industry)}`:''}${km.erp_pct!=null?` · ERP ${km.erp_pct}% (${esc(km.erp_as_of||'')})`:''}</p>`;
}

// 5-year trend (revenue / gross / op margin / FCF / ROIC from 10-K filings) answers the
// one question a single-year DEEP score cannot: is it improving steadily? Every row
// shares one fiscal-year axis, so the same year sits on the same vertical line.
const TREND_TOK={up:'--bull',down:'--bear',flat:'--muted'};
function trendValue(v,unit){
  if(v==null)return '&mdash;';
  if(unit!=='money')return v.toFixed(1)+'%';
  return compactNumber(v,'$',false);
}
// one row's line on the shared year axis (X maps a fiscal year to 0..100)
function spark(pts,unit,col,X){
  const p=(pts||[]).filter(q=>q.v!=null); if(!p.length)return '<span class="note">ไม่มีข้อมูล</span>';
  const vs=p.map(q=>q.v); let lo=Math.min(...vs), hi=Math.max(...vs);
  // FCF can go negative: keep zero inside the frame or a negative year just looks "low"
  if(lo>0&&unit==='money')lo=0;
  const Y=v=>hi===lo?50:92-(v-lo)/(hi-lo)*84;
  const zero=(lo<0&&hi>0)?`<line x1="0" x2="100" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}" stroke="var(--muted)" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`:'';
  return `<span class="sparkbox"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${X.grid}${zero}<polyline fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke" points="${p.map(q=>X(q.fy).toFixed(1)+','+Y(q.v).toFixed(1)).join(' ')}"/></svg>${p.map(q=>`<i style="left:${X(q.fy).toFixed(1)}%;top:${Y(q.v).toFixed(1)}%;background:${col}" title="FY${esc(q.fy)}: ${trendValue(q.v,unit)}"></i>`).join('')}</span>`;
}
// The strip is fiscal years while the valuation is TTM; they drift apart by
// (months since year end) x growth. The period comes from the payload, because a label
// the frontend derives stops being true when the frontend changes.
function trendPeriod(t){
  if(!t||!t.fy_to)return '';
  const span=t.fy_from&&t.fy_from!==t.fy_to?`FY${esc(t.fy_from)}–FY${esc(t.fy_to)}`:`FY${esc(t.fy_to)}`;
  // only worth the ink once TTM has moved away from the filed year
  const lag=(t.lag_months!=null&&t.lag_months>=3)
    ? ` <span class="note" title="${esc('ปีงบล่าสุดจบไปแล้ว '+t.lag_months+' เดือน — ตัวเลข TTM ของการ์ดจึงเดินหน้าไปกว่านี้')}">&middot; จบ ${esc(t.as_of||'')} (${t.lag_months} ด.ก่อน)</span>`
    : '';
  return `<span class="note">${span}</span>${lag}`;
}
function trendStrip(x){
  const t=x.trend5y; if(!t||!t.rows||!t.rows.length)return '<p class="note">ไม่มีข้อมูล 5 ปีจากงบ 10-K</p>';
  const yrs=[...new Set(t.rows.flatMap(r=>(r.points||[]).map(p=>String(p.fy))))].sort(), n=yrs.length;
  const X=fy=>n<2?50:yrs.indexOf(String(fy))/(n-1)*100;
  X.grid=yrs.map(y=>`<line x1="${X(y)}" x2="${X(y)}" y1="0" y2="100" stroke="var(--line)" vector-effect="non-scaling-stroke"/>`).join('');
  const axis=`<div class="spark axis" aria-hidden="true"><span></span><div class="yax">${yrs.map(y=>`<span style="left:${X(y)}%">${esc(y)}</span>`).join('')}</div><span></span></div>`;
  return `<div class="box" style="max-width:1000px"><h3 title="${esc('ตัวเลขจากงบ 10-K ที่ยื่น ก.ล.ต.สหรัฐ (GAAP) — ไม่ใช่ตัวเลข adjusted\nทุกค่าในแท็บนี้เป็นตัวเลข "ปีงบ" ส่วนมูลค่าและ ROIC* คิดจาก 12 เดือนล่าสุด (TTM)')}">แนวโน้ม 5 ปีจากงบ ${trendPeriod(t)}</h3>
    <p class="note" style="margin:0">ทุกแถวใช้แกนปีเดียวกัน · ปีที่ไม่มีข้อมูลจะไม่มีจุด · โตต่อเนื่องไหม ดูความชันของเส้น ไม่ใช่ค่าปีล่าสุด</p>
    ${t.rows.map(r=>trendRow(r,X)).join('')}${axis}</div>`;
}
function trendRow(r,X){
  const tok=TREND_TOK[r.direction]||'--muted', col=`var(${tok})`;
  // The backend states the summary's unit: the ROIC row's summary is a return on new
  // capital (%), not a change in points. The fallback only serves rows stored before that.
  const su0=r.summary_unit||(r.unit==='money'?'%':'pp'), su=su0==='pp'?'%':pp2pct(su0);
  const sum=r.summary==null?MUTED_DASH:`<b style="color:color-mix(in srgb,${col} 75%,var(--ink))">${signed(r.summary)}${su}</b>`;
  // fewer than 5 years must show, never read as a 5-year trend
  const nb=r.n<5?` <span class="note" title="${esc('SEC แท็กข้อมูลให้แค่ '+r.n+' ปีที่ต่อเนื่องกัน — ที่เหลือไม่ได้ยื่นหรือขาดขาใดขาหนึ่ง')}">${r.n}y</span>`:'';
  const pts=(r.points||[]).filter(p=>p.v!=null), first=pts[0], last=pts[pts.length-1];
  const tip=first?`${r.label} · FY${first.fy}→FY${last.fy} · ${trendValue(first.v,r.unit)} → ${trendValue(last.v,r.unit)}${r.note?'\n'+r.note:''}`:r.label;
  const vs=(r.vs_average_pp!=null)
    ? `<div class="note" title="${esc('ผลตอบแทนของทุนที่ใส่เพิ่มใน 5 ปีนี้ เทียบกับ ROIC ปีล่าสุด — ถ้าต่ำกว่า แปลว่าเงินก้อนใหม่ทำงานได้แย่กว่าของเก่า = moat เริ่มบาง (Damodaran)')}">ทุนใหม่ ${signed(r.vs_average_pp)}% vs ROIC ล่าสุด</div>`:'';
  return `<div class="spark" title="${esc(tip)}" role="img" aria-label="${esc(r.label+': '+pts.map(p=>'FY'+p.fy+' '+trendValue(p.v,r.unit).replace('&mdash;','—')).join(', '))}">
      <span>${esc(r.label)}${nb}</span>${spark(r.points,r.unit,col,X)}
      <span class="num" style="text-align:right">${last?trendValue(last.v,r.unit):'–'} <span class="note">${esc(r.summary_label||'')}</span> ${sum}${vs}</span></div>`;
}
