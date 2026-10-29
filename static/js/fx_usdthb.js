// USD/THB over one year on the Correlation tab: the rate, its range and volatility, and what
// the rate did to a Thai investor's view of this USD portfolio.
//
// DATA CONTRACT (wire the backend to this; until then the panel draws MOCK data):
//   co.fx_usdthb = {
//     pair: 'USD/THB',
//     as_of: 'YYYY-MM-DD',          // last close used
//     source: 'Yahoo THB=X',        // shown in the panel tag
//     points: [{d:'YYYY-MM-DD', v:32.45}, ...]   // oldest -> newest, daily or weekly closes, ~1 year
//   }
// Everything else (change, high/low, vol, THB value, FX effect) is computed here from points.

// a seeded random walk shaped like USD/THB (about 32-36), weekdays only; never shown unlabelled
function fxMockSeries(asOf){
  let s=20261029; const rnd=()=>{s=(s*1103515245+12345)%2147483648;return s/2147483648;};
  const out=[], end=asOf?new Date(asOf+'T00:00:00'):new Date(); end.setHours(0,0,0,0);
  const d=new Date(end); d.setFullYear(d.getFullYear()-1);
  let v=33.6;
  for(;d<=end;d.setDate(d.getDate()+1)){
    if(d.getDay()===0||d.getDay()===6)continue;
    const drift=(32.6-v)*0.004, shock=(rnd()+rnd()+rnd()-1.5)*0.16;
    v=Math.max(31,Math.min(37,v+drift+shock));
    out.push({d:d.toISOString().slice(0,10),v:Math.round(v*1000)/1000});
  }
  return {pair:'USD/THB',as_of:out[out.length-1].d,source:'ข้อมูลตัวอย่าง',points:out,mock:true};
}

function fxStats(p){
  const vs=p.map(x=>x.v), first=p[0], last=p[p.length-1];
  let hi=p[0],lo=p[0]; p.forEach(x=>{if(x.v>hi.v)hi=x;if(x.v<lo.v)lo=x;});
  const rets=[]; for(let i=1;i<p.length;i++)rets.push(Math.log(p[i].v/p[i-1].v));
  const mean=rets.reduce((a,b)=>a+b,0)/(rets.length||1);
  const sd=Math.sqrt(rets.reduce((a,b)=>a+(b-mean)**2,0)/Math.max(1,rets.length-1));
  const days=(new Date(last.d)-new Date(first.d))/864e5, perYear=days>0?(p.length-1)/days*365:252;
  return {first,last,hi,lo,chg:(last.v/first.v-1)*100,vol:sd*Math.sqrt(perYear)*100,min:Math.min(...vs),max:Math.max(...vs)};
}

function fxChart(p,st){
  const W=920,H=230,L=46,R=70,T=16,B=196;
  const pad=(st.max-st.min)*0.12||0.5, mn=st.min-pad, mx=st.max+pad;
  const t0=new Date(p[0].d).getTime(), t1=new Date(p[p.length-1].d).getTime();
  const X=d=>L+(new Date(d).getTime()-t0)/((t1-t0)||1)*(W-L-R), Y=v=>B-(v-mn)/(mx-mn)*(B-T);
  let s='';
  const step=niceStep((mx-mn)/4);
  for(let v=Math.ceil(mn/step)*step;v<=mx;v+=step)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${L-6}" y="${Y(v)+3}" text-anchor="end">${nf(v,step<1?1:0)}</text>`;
  // one label per month, at the first point of each month
  let lastM='';
  p.forEach(x=>{const m=x.d.slice(0,7); if(m!==lastM){lastM=m; if(x===p[0]&&+x.d.slice(8)>10)return;const dt=new Date(x.d);s+=`<line x1="${X(x.d)}" x2="${X(x.d)}" y1="${B}" y2="${B+4}" stroke="var(--muted)"/><text x="${X(x.d)}" y="${B+16}" text-anchor="middle">${dt.toLocaleString('en-US',{month:'short'})}${dt.getMonth()===0?" '"+String(dt.getFullYear()).slice(2):''}</text>`;}});
  const pts=p.map(x=>`${X(x.d).toFixed(1)},${Y(x.v).toFixed(1)}`).join(' ');
  s+=`<polygon points="${X(p[0].d)},${B} ${pts} ${X(p[p.length-1].d)},${B}" fill="var(--accent)" opacity=".10"/>`;
  s+=`<line x1="${L}" x2="${W-R}" y1="${Y(st.first.v)}" y2="${Y(st.first.v)}" stroke="var(--muted)" stroke-dasharray="4 4"/><text x="${W-R-4}" y="${Y(st.first.v)-5}" text-anchor="end">1 ปีก่อน ${nf(st.first.v,2)}</text>`;
  s+=`<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts}"/>`;
  const dotLab=(x,lab,cls,dy)=>`<circle cx="${X(x.d)}" cy="${Y(x.v)}" r="4" fill="var(--surface)" stroke="var(--ink)" stroke-width="1.5"/><text x="${X(x.d)}" y="${Y(x.v)+dy}" text-anchor="middle" class="${cls}">${lab} ${nf(x.v,2)}</text>`;
  s+=dotLab(st.hi,'สูงสุด','',-9)+dotLab(st.lo,'ต่ำสุด','',16);
  s+=`<circle cx="${X(st.last.d)}" cy="${Y(st.last.v)}" r="5" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/><text x="${X(st.last.d)+9}" y="${Y(st.last.v)+4}" class="strong">${nf(st.last.v,2)}</text>`;
  return svgChart(W,H,`USD/THB ${p.length} จุด จาก ${nf(st.first.v,2)} (${st.first.d}) เป็น ${nf(st.last.v,2)} (${st.last.d}) สูงสุด ${nf(st.hi.v,2)} ต่ำสุด ${nf(st.lo.v,2)}`,s);
}

function corrFxHTML(co){
  const fx=(co&&co.fx_usdthb&&(co.fx_usdthb.points||[]).length>2)?co.fx_usdthb:fxMockSeries((((window.__last||{}).rows||[])[0]||{}).price_date);
  const p=fx.points, st=fxStats(p);
  const L=window.__last||{}, t=L.totals||{}, b=L.benchmark||{};
  const usdV=t.total_value??t.market_value;
  const thbNow=usdV!=null?usdV*st.last.v:null, thbThen=usdV!=null?usdV*st.first.v:null;
  const fxEff=thbNow!=null?thbNow-thbThen:null;
  const rUsd=b.portfolio_12m, rThb=rUsd!=null?((1+rUsd/100)*(1+st.chg/100)-1)*100:null;
  // baht weakening (rate up) helps a Thai holder of USD assets
  const fxTok=st.chg>0?'up':st.chg<0?'down':'';
  const thb=v=>v==null?'–':(v<0?'−':'')+'฿'+Math.abs(v).toLocaleString('en-US',{maximumFractionDigits:0});
  const mock=fx.mock?`<span class="pill" style="${tone('--warn')}" title="ยังไม่ได้ต่อข้อมูลจริง — เส้นนี้สร้างขึ้นเพื่อออกแบบเท่านั้น">MOCK · ข้อมูลตัวอย่าง</span>`:'';
  return `<section class="pnl s12" aria-labelledby="hFx"><div class="phead"><h2 id="hFx">USD/THB ย้อนหลัง 1 ปี <span class="note">ค่าเงินที่คนไทยถือหุ้นสหรัฐต้องรับ</span></h2>
      <div style="display:flex;gap:8px;align-items:center">${mock}<span class="tagsrc">${esc(fx.source||'')} · ${esc(fx.as_of||'')}</span></div></div>
    <div class="dgrid fxgrid"><div class="box">${fxChart(p,st)}
        <p class="note" style="margin:4px 0 0">เส้นประ = ระดับเมื่อ 1 ปีก่อน · เส้นขึ้น = บาทอ่อน (ดีกับคนถือ $) · เส้นลง = บาทแข็ง (กินผลตอบแทนเมื่อแลกกลับ)</p></div>
      <div class="box fxside">${kpisHTML([
      ['ล่าสุด',nf(st.last.v,2)+' ฿',null,esc(st.last.d)],
      ['เปลี่ยน 1 ปี',`${arrow(st.chg)}${sgn(st.chg,1)}`,fxTok,`จาก ${nf(st.first.v,2)} · ${st.chg>=0?'บาทอ่อน':'บาทแข็ง'}`],
      ['สูงสุด / ต่ำสุด',`${nf(st.hi.v,2)} / ${nf(st.lo.v,2)}`,null,`${esc(st.hi.d)} / ${esc(st.lo.d)}`],
      ['Vol ต่อปี',nf(st.vol,1)+'%',null,'จาก log return รายวัน'],
    ])}
        <h3 style="margin-top:14px">ผลต่อพอร์ตนี้ (มองเป็นบาท)</h3>
        <ul class="lines">
          <li><span>มูลค่าพอร์ตวันนี้</span><b>${usd(usdV)} ≈ ${thb(thbNow)}</b></li>
          <li><span>ผลจากค่าเงินอย่างเดียว (1 ปี)</span><b class="${upcls(fxEff)}">${fxEff==null?'–':(fxEff>=0?'+':'')+thb(fxEff)}</b></li>
          <li><span>ผลตอบแทน 12 เดือน หน่วย $</span><b class="${upcls(rUsd)}">${sgn(rUsd,1)}</b></li>
          <li><span>ผลตอบแทน 12 เดือน หน่วย ฿</span><b class="${upcls(rThb)}">${sgn(rThb,1)}</b></li>
        </ul>
        <p class="note" style="margin:6px 0 0">ผลจากค่าเงิน = มูลค่า $ วันนี้ × (อัตราวันนี้ − อัตรา 1 ปีก่อน) สมมติถือ $ เท่าเดิมทั้งปี · ผลตอบแทนบาท = (1 + r$) × (1 + ΔFX) − 1</p></div></div>
  </section>`;
}
