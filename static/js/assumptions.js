// Monthly assumptions (ERP, S&P P/E): the form posts to /api/assumptions, which saves
// to the store and mirrors it to Drive.
async function loadAssumptions(){
  const el=document.getElementById('assumeMsg'); if(!el)return;
  try{
    const a=await (await fetch('/api/assumptions')).json();
    window._assume=a;   // renderAssumptions compares the Run Daily snapshot with these live values
    const src=(a.source||{});
    el.innerHTML=`ปัจจุบัน: ERP <b>${a.erp_pct}%</b> (${esc(a.erp_as_of||'—')}, ${src.erp==='manual'?'manual':src.erp==='damodaran-live'?'Damodaran web':'default'})`+
      ` · PE <b>${a.market_pe}</b> (${esc(a.market_pe_as_of||'—')}, ${src.market_pe==='manual'?'manual':'default'})`+
      (a.erp_stale?` <span style="color:#b45309;font-weight:700">⚑ ERP ${a.erp_months_old} เดือน — ควรอัพเดท</span>`:'');
    const e=document.getElementById('erpIn'), p=document.getElementById('peIn');
    if(e&&!e.value)e.placeholder=`ERP % (${a.erp_pct})`;
    if(p&&!p.value)p.placeholder=`S&P PE (${a.market_pe})`;
  }catch(err){el.textContent='โหลดค่า assumptions ไม่ได้';}
}
async function saveAssumptions(){
  const e=document.getElementById('erpIn').value, p=document.getElementById('peIn').value;
  const msg=document.getElementById('assumeMsg'), sp=document.getElementById('spAs');
  if(!e&&!p){msg.textContent='กรอกอย่างน้อย 1 ค่า (ERP % หรือ S&P PE)';return;}
  const q=[]; if(e)q.push('erp_pct='+encodeURIComponent(e)); if(p)q.push('market_pe='+encodeURIComponent(p));
  sp.style.display='inline-block';
  try{
    const r=await fetch('/api/assumptions?'+q.join('&'),{method:'POST'});
    const d=await r.json();
    if(d.error){msg.innerHTML='<span style="color:#dc2626">'+esc(d.error)+'</span>';}
    else{
      msg.innerHTML='<span style="color:#16a34a">✓ บันทึกแล้ว (sync Google Drive)</span> — '+esc(d.note||'');
      document.getElementById('erpIn').value='';document.getElementById('peIn').value='';
      await loadAssumptions(); load();
    }
  }catch(err){msg.innerHTML='<span style="color:#dc2626">บันทึกไม่สำเร็จ: '+esc(String(err))+'</span>';}
  sp.style.display='none';
}
// 'YYYY-MM...' -> whole calendar months before today, or null
function monthsSince(a){if(!a)return null;const m=String(a).match(/^(\d{4})-(\d{2})/);if(!m)return null;const n=new Date();return (n.getFullYear()-(+m[1]))*12+(n.getMonth()+1-(+m[2]));}
// S32/33: the whole market overlay rests on the manual ERP / P/E, so stale values get a banner
function renderAssumptions(mk){
  const el=document.getElementById('assumeBanner'); if(!el)return;
  const v=(mk||{}).valuation||{};
  const em=monthsSince(v.erp_as_of), pm=monthsSince(v.market_pe_as_of), lim=v.erp_stale_months||3;
  const bad=[];
  if(em!=null&&em>lim)bad.push(`ERP${v.implied_erp_pct!=null?' '+v.implied_erp_pct+'%':''} as-of ${v.erp_as_of} (<b>เก่า ${em} เดือน</b>)`);
  if(pm!=null&&pm>lim)bad.push(`Market P/E as-of ${v.market_pe_as_of} (<b>เก่า ${pm} เดือน</b>)`);
  if(!bad.length){el.style.display='none';return;}
  // The banner reads the last Run Daily snapshot. If the form has newer live values,
  // say that a Run Daily is all it takes.
  const cur=window._assume||{};
  const snapshotStale=cur.erp_as_of&&v.erp_as_of&&cur.erp_as_of>v.erp_as_of;
  el.style.cssText='display:block;margin:8px 0;padding:8px 12px;border-radius:8px;font-size:calc(12px*var(--fs));background:#fff7ed;color:#9a3412;border:1px solid #fdba74';
  el.innerHTML='&#9888; <b>สมมติฐานหมดอายุ:</b> '+bad.join(' · ')
    +(snapshotStale
      ?` — <b>ค่าปัจจุบันในระบบอัพเดตแล้ว (ERP ${cur.erp_pct}% · ${esc(cur.erp_as_of)})</b> — แผงนี้เป็น snapshot จาก Run Daily ครั้งก่อน &middot; กด <b>Run Daily</b> เพื่อคำนวณ market overlay ใหม่`
      :' — market overlay อาจเพี้ยนทั้งแผง &middot; อัปเดตที่ฟอร์ม <b>&#128200; Assumptions</b> ด้านบน (เอาค่าจากหน้า Damodaran &amp; multpl.com รายเดือน — ดูวิธีในแท็บ How to ข้อ 0) แล้วกด <b>Run Daily</b> + <b>Run Fundamental Refresh</b>');
}

// Revenue mix (country risk premium): auto from the latest 10-K on Refresh, or typed here.
async function saveRevenueMix(){
  const t=document.getElementById('mixT').value.trim(), m=document.getElementById('mixIn').value.trim();
  const msg=document.getElementById('mixMsg'), sp=document.getElementById('spMix');
  if(!t){msg.textContent='ใส่ ticker';return;}
  sp.style.display='inline-block';
  try{
    const r=await fetch('/api/revenue_mix?ticker='+encodeURIComponent(t)+'&mix='+encodeURIComponent(m),{method:'POST'});
    const d=await r.json();
    if(d.error){msg.innerHTML='<span style="color:#dc2626">'+esc(d.error)+'</span>';}
    else{
      msg.innerHTML='<span style="color:#16a34a">✓ '+(d.mix?'บันทึก':'ลบ')+' '+esc(d.ticker)+'</span> — '+esc(d.note||'');
      document.getElementById('mixIn').value='';
      if(document.getElementById('mixList').style.display!=='none')loadMixList();
    }
  }catch(err){msg.innerHTML='<span style="color:#dc2626">บันทึกไม่สำเร็จ: '+esc(String(err))+'</span>';}
  sp.style.display='none';
}
function mixText(m){return m?Object.entries(m).map(([k,v])=>k+' '+v).join(', '):'';}
async function loadMixList(){
  const el=document.getElementById('mixList');
  try{
    const d=await (await fetch('/api/revenue_mix')).json();
    el.innerHTML='<table style="border-collapse:collapse" cellpadding="3"><tr><th>Ticker</th><th>CRP</th><th>ที่มา (Refresh ล่าสุด)</th><th>กรอกเอง</th></tr>'+
      (d.rows||[]).map(r=>`<tr><td><a href="#" onclick="document.getElementById('mixT').value='${esc(r.ticker)}';document.getElementById('mixIn').value='${esc(mixText(r.manual||r.config))}';return false">${esc(r.ticker)}</a></td>`+
        `<td>${r.crp_pct==null?'—':r.crp_pct+'%'}</td><td>${esc(r.basis||'— (ยังไม่ Refresh)')}</td>`+
        `<td>${r.config?'config.py: '+esc(mixText(r.config)):esc(mixText(r.manual))||'—'}</td></tr>`).join('')+'</table>';
  }catch(err){el.textContent='โหลดไม่ได้';}
}
function toggleMixList(){const el=document.getElementById('mixList');const show=el.style.display==='none';el.style.display=show?'block':'none';if(show)loadMixList();}
