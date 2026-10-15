// 2026-08-16: a bare "&mdash;" told the reader nothing about WHY a metric is absent,
// unlike the "x n/a" chips in the earnings row which carry the source failure. Every
// card now gets a tooltip, and the dash gets the reason when one is known.
const KM_TIP={
  'Ke':'Cost of equity = Rf + beta x ERP',
  'WACC':'Ke x (E/V) + Kd-after-tax x (D/V) — the bar ROIC has to clear',
  'Kd a-t':'Cost of debt after tax = (Rf + default spread) x (1 - tax)',
  'ROIC*':'ROIC ที่ปรับแล้ว (normalized) — อาจต่างจาก "R&D-capitalized ROIC" บรรทัดแรกของ audit trail ด้านล่าง ซึ่งเป็นค่าก่อน normalize',
  'Spread':'ROIC* - WACC. บวก = สร้างมูลค่า · ลบ = ทำลายมูลค่า',
  'EVA':'Economic Value Added = Spread x Invested Capital ($ ต่อปี)',
};
const KM_MISSING={
  'ROIC*':'วัดไม่ได้ — ขาดข้อมูล invested capital หรือ NOPAT',
  'Spread':'วัดไม่ได้ — ต้องมีทั้ง ROIC* และ WACC',
  'EVA':'วัดไม่ได้ — ต้องมีทั้ง spread และ invested capital',
};
function kmCard(label,val){
  const missing=String(val).indexOf('mdash')>=0;
  const tip=(KM_TIP[label]||label)+(missing&&KM_MISSING[label]?(' — '+KM_MISSING[label]):'');
  return `<div title="${tip.replace(/"/g,'&quot;')}" style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:6px 9px"><div style="font-size:calc(9px*var(--fs));color:var(--grey)">${label}</div><div style="font-size:calc(14px*var(--fs));font-weight:700">${val}</div></div>`;}
function adjColor(s){const n=parseFloat(s);return n>0?'#16a34a':n<0?'#dc2626':'#94a3b8';}
function deepDrawer(x){
  const km=x.key_metrics||{};
  // 2026-08-16: $XB with one decimal swallowed every small-cap EVA — TMDX $3M and
  // CELH $41M both printed "$0.0B" — and put the minus inside the currency ("$-0.3B").
  const evaTxt=(x.eva==null)?'&mdash;':(function(v){
    const sign=v<0?'-':'', a=Math.abs(v);
    if(a>=1e9) return sign+'$'+(a/1e9).toFixed(1)+'B';
    if(a>=1e6) return sign+'$'+(a/1e6).toFixed(0)+'M';
    if(a>=1e3) return sign+'$'+(a/1e3).toFixed(0)+'k';
    return sign+'$'+a.toFixed(0);
  })(x.eva);
  const cards=[['Ke',pctc(km.ke_pct)],['WACC',pctc(km.wacc_pct)],['Kd a-t',pctc(km.kd_pct)],
    ['ROIC*',pctc(km.roic_adj_pct!=null?km.roic_adj_pct:km.roic_pct)],['Spread',pctc(km.spread_pct)],['EVA',evaTxt]]
    .map(c=>kmCard(c[0],c[1])).join('');
  const notes=((x.subscores||{}).breakdown)||[];
  let bd;
  if(notes.length){
    bd=notes.map(n=>{
      const mm=String(n).match(/=(-?\+?[0-9.]+)\s*$/);
      const col=mm?adjColor(mm[1]):'#64748b';
      const head=String(n).replace(/=(-?\+?[0-9.]+)\s*$/,'');
      return `<span style="font-size:calc(11px*var(--fs));color:#64748b">${esc(head)}<b style="color:${col};font-weight:700">${mm?'= '+mm[1]:''}</b></span>`;
    }).join('<span style="color:#cbd5e1"> &middot; </span>');
  }else bd='<span class="muted" style="font-size:calc(11px*var(--fs))">No breakdown — run a fundamentals refresh on v8.2.</span>';
  return `<div style="padding:10px 12px;background:#f8fafc">
    <div style="display:flex;flex-wrap:wrap;gap:8px">${cards}
      <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:6px 9px"><div style="font-size:calc(9px*var(--fs));color:var(--grey)">Earnings quality</div><div style="font-size:calc(13px*var(--fs));font-weight:700">${x.eq_verdict||'&mdash;'}</div></div></div>
    <div style="margin-top:9px;font-size:calc(9px*var(--fs));color:var(--grey);font-weight:700">SUB-SCORE BREAKDOWN (audit trail) &middot; *ROIC = ROIC ที่ปรับแล้ว (normalized) &mdash; ค่า &quot;R&amp;D-capitalized ROIC&quot; ก่อน normalize อยู่บรรทัดแรกด้านล่าง และอาจต่างกันได้ถึง ~10pp &middot; FCFF&rarr;WACC, FCFE&rarr;Ke</div>
    <div style="margin-top:4px;line-height:1.9">${bd}</div>
  </div>`;
}
// ---- T5: แถบเทรนด์ 5 ปี — รายได้ / gross / op margin / FCF / ROIC (SEC 10-K) ----
// ตอบคำถามเดียว: "ดีขึ้นต่อเนื่องไหม" ซึ่งคะแนน DEEP ทุกตัวเป็น snapshot ปีเดียวจึงตอบไม่ได้
const TREND_COL={up:'#0d9488',down:'#b91c1c',flat:'#94a3b8'};
function tfmt(v,unit){
  if(v==null)return '&mdash;';
  if(unit!=='money')return v.toFixed(1)+'%';
  const a=Math.abs(v);
  return (v<0?'-':'')+(a>=1e9?(a/1e9).toFixed(1)+'B':a>=1e6?(a/1e6).toFixed(0)+'M':a.toFixed(0));
}
function spark(pts,unit,col){
  if(!pts||pts.length<2)return '';
  const W=104,H=22,P=2, vs=pts.map(p=>p.v);
  let lo=Math.min(...vs), hi=Math.max(...vs);
  // FCF ติดลบได้ ต้องให้เส้นศูนย์อยู่ในกรอบเสมอ ไม่งั้นแท่งติดลบจะดูเหมือนแค่ "ต่ำ"
  if(lo>0&&unit==='money')lo=0;
  if(hi===lo)hi=lo+1;
  const X=i=>P+i*(W-2*P)/(pts.length-1), Y=v=>H-P-(v-lo)/(hi-lo)*(H-2*P);
  const d=pts.map((p,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
  const zero=(lo<0&&hi>0)?`<line x1="${P}" y1="${Y(0).toFixed(1)}" x2="${W-P}" y2="${Y(0).toFixed(1)}" stroke="#cbd5e1" stroke-dasharray="2 2" stroke-width="0.7"/>`:'';
  const dots=pts.map((p,i)=>`<circle cx="${X(i).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="${i===pts.length-1?2.2:1.3}" fill="${col}"><title>FY${esc(p.fy)}: ${tfmt(p.v,unit)}</title></circle>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:${W}px;height:${H}px;flex:none">${zero}<path d="${d}" fill="none" stroke="${col}" stroke-width="1.4" stroke-linejoin="round"/>${dots}</svg>`;
}
// D: the strip is FISCAL YEARS; the valuation above it is TTM. The gap between them is
// (months since the year ended) x (growth), so it is 0% for MSFT (year ended six weeks
// ago) and +18% for AVGO (nine months). Both are correct and the card used to say which
// was which nowhere. The period comes from the payload — a label the front end derives
// stops being true the moment the front end changes.
function trendPeriod(t){
  if(!t||!t.fy_to)return '';
  const span=t.fy_from&&t.fy_from!==t.fy_to?`FY${esc(t.fy_from)}–FY${esc(t.fy_to)}`:`FY${esc(t.fy_to)}`;
  // only worth the ink once TTM has actually moved away from the filed year
  const lag=(t.lag_months!=null&&t.lag_months>=3)
    ? ` <span class="muted" style="text-transform:none;letter-spacing:0" title="${esc('ปีงบล่าสุดจบไปแล้ว '+t.lag_months+' เดือน — ตัวเลข TTM ด้านบนของการ์ดจึงเดินหน้าไปกว่านี้')}">&middot; จบ ${esc(t.as_of||'')} (${t.lag_months} ด.ก่อน)</span>`
    : '';
  return `<span class="muted" style="text-transform:none;letter-spacing:0">&middot; ${span}</span>${lag}`;
}
function trendStrip(x){
  const t=x.trend5y; if(!t||!t.rows||!t.rows.length)return '';
  const rows=t.rows.map(r=>{
    const col=TREND_COL[r.direction]||'#64748b';
    // B: the suffix belongs to the SUMMARY, not to the row. Guessing it from r.unit
    // printed the ROIC row's incremental RETURN as a delta — PFE read "ทุนใหม่ -33.0pp"
    // ("ROIC fell 33 points") when it meant "capital added over 5y earned -33%".
    // The backend now states it; the old rule stays only for rows stored before that.
    const su=r.summary_unit||(r.unit==='money'?'%':'pp');
    const sum=r.summary==null?'<span class="muted">&mdash;</span>'
      :`<b style="color:${col}">${r.summary>=0?'+':''}${r.summary}${su}</b>`;
    // n<5 ต้องบอกให้เห็น ไม่ใช่ปล่อยให้อ่านเป็น 5 ปี (ผู้ใช้เลือก "แสดงเท่าที่มี + บอกจำนวนปี")
    const nb=r.n<5?` <span class="muted" style="font-size:calc(8px*var(--fs))" title="${esc('SEC แท็กข้อมูลให้แค่ '+r.n+' ปีที่ต่อเนื่องกัน — ที่เหลือไม่ได้ยื่นหรือขาดขาใดขาหนึ่ง')}">${r.n}y</span>`:'';
    const first=r.points[0], last=r.points[r.points.length-1];
    const tip=`${r.label} · FY${first.fy}→FY${last.fy} · ${tfmt(first.v,r.unit)} → ${tfmt(last.v,r.unit)}${r.note?'\n'+r.note:''}`;
    const vs=(r.vs_average_pp!=null)
      ? `<div class="muted" style="font-size:calc(8.5px*var(--fs))" title="${esc('ผลตอบแทนของทุนที่ใส่เพิ่มใน 5 ปีนี้ เทียบกับ ROIC ปีล่าสุด — ถ้าต่ำกว่า แปลว่าเงินก้อนใหม่ทำงานได้แย่กว่าของเก่า = moat เริ่มบาง (Damodaran)')}">ทุนใหม่ ${r.vs_average_pp>=0?'+':''}${r.vs_average_pp}pp vs ROIC ล่าสุด</div>`:'';
    return `<div style="display:flex;align-items:center;gap:7px;padding:2px 0" title="${esc(tip)}">
      <span style="width:74px;flex:none;font-size:calc(9.5px*var(--fs));color:#475569">${esc(r.label)}${nb}</span>
      ${spark(r.points,r.unit,col)}
      <span style="font-size:calc(10px*var(--fs));white-space:nowrap">${tfmt(last.v,r.unit)} <span class="muted" style="font-size:calc(8.5px*var(--fs))">${esc(r.summary_label)}</span> ${sum}</span>
    </div>${vs?`<div style="margin:-2px 0 3px 81px">${vs}</div>`:''}`;
  }).join('');
  return `<div style="margin-top:10px;padding-top:8px;border-top:0.5px dashed #e2e8f0">
    <div style="font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase" title="${esc('ตัวเลขจากงบ 10-K ที่ยื่น ก.ล.ต.สหรัฐ (GAAP) — ไม่ใช่ตัวเลข adjusted ที่บริษัทนำเสนอใน earnings call\nโตต่อเนื่องไหม ดูความชันของเส้น ไม่ใช่ค่าปีล่าสุด\n\nทุกค่าในแถบนี้เป็นตัวเลข "ปีงบ" ส่วนมูลค่าและ ROIC ด้านบนของการ์ดคิดจาก 12 เดือนล่าสุด (TTM) — ยิ่งปีงบจบมานาน สองค่ายิ่งห่างกัน')}">โตต่อเนื่องไหม &middot; ${t.years} ปีล่าสุด ${trendPeriod(t)} <span class="muted" style="text-transform:none;letter-spacing:0">(${esc(t.source)})</span></div>
    <div style="margin-top:3px">${rows}</div></div>`;
}
function toggleDrawer(tr){
  const dr=tr.nextElementSibling; if(!dr||!dr.classList.contains('pdrawer'))return;
  const open=dr.style.display!=='none'; dr.style.display=open?'none':'';
  const ch=tr.querySelector('.chev'); if(ch)ch.style.transform=open?'':'rotate(180deg)';
}
