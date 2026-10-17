// The page shell: tabs, build stamp, persistence badge, keep-alive and boot. Loaded last.

// must equal config.BUILD; a browser still running cached JS shows the mismatch banner
const DASH_BUILD="2026-10-18a";

async function checkBuild(){
  const el=document.getElementById('buildStamp'); if(!el)return;
  try{
    const h=await (await fetch('/healthz')).json();
    if(h.build && h.build!==DASH_BUILD){
      el.style.cssText='display:block;margin:6px 0;padding:6px 10px;border-radius:8px;font-size:calc(11px*var(--fs));background:#fef2f2;color:#991b1b;border:1px solid #fecaca';
      el.innerHTML='&#9888; UI build <b>'+DASH_BUILD+'</b> &ne; server <b>'+h.build+'</b> — hard refresh (Ctrl+F5) to load the new dashboard.';
    }else{
      el.style.cssText='display:block;margin:6px 0;font-size:calc(10px*var(--fs));color:var(--grey)';
      el.textContent='build '+DASH_BUILD+(h.build?(' · server '+h.build):'');
    }
  }catch(e){}
}
// what a tab fetches each time it opens
const TAB_OPENERS={
  2:()=>loadWatchlist(),
  3:()=>{renderCashStance();loadAllocation();loadRisk();loadScreen();},
  6:()=>loadCorr(),
};
function showTab(n){
  for(const i of [1,2,3,4,5,6]){document.getElementById('tab'+i).classList.toggle('active',i===n);document.getElementById('p'+i).style.display=i===n?'block':'none';}
  if(TAB_OPENERS[n])TAB_OPENERS[n]();
}
// Drive backup badge: local only / healthy / failing (pushes blocked so an empty store
// cannot overwrite the backup)
async function loadPersist(){
  const el=document.getElementById('persist'); if(!el)return;
  try{
    const p=await (await fetch('/api/persist')).json();
    if(!p.configured){
      el.className='quota'; el.innerHTML='&#128190; local only';
      el.title='ไม่ได้ตั้งค่า Google Drive backup — บน Render free tier ข้อมูลจะหายเมื่อ instance restart (ตั้ง GDRIVE_OAUTH_* ตาม GOOGLE_DRIVE_OAUTH_SETUP.md)';
      return;
    }
    const bad=(p.pull_state==='error')||p.push_blocked||(p.push_result==='error');
    if(bad){
      el.className='quota warn'; el.innerHTML='&#128190; Drive &#10060;';
      el.title='Google Drive backup มีปัญหา — '+(p.last_error||'pull/push ล้มเหลว')
        +'\nการ push ถูกบล็อกไว้เพื่อป้องกันไฟล์ backup ถูกทับด้วย store ว่าง จะ retry อัตโนมัติทุก 1 นาที'
        +'\nถ้าค้างนาน: refresh token อาจหมดอายุ (OAuth consent screen สถานะ Testing = token หมดใน 7 วัน — กด PUBLISH APP เป็น In production แล้วสร้าง token ใหม่)';
    }else{
      el.className='quota'; el.innerHTML='&#128190; Drive &#10003;';
      el.title=`Google Drive backup ทำงานปกติ (${p.auth_mode})\nlast pull: ${p.last_pull||'—'} (${p.pull_state||p.pull_result||'—'})\nlast push: ${p.last_push||'—'} (${p.push_result||'—'})`;
    }
  }catch(e){el.innerHTML='&#128190; ?';}
}
// Render free tier spins the instance down after ~15 min idle (ephemeral disk).
// While the dashboard tab is open, ping /healthz every 4 min so the server stays
// warm mid-session, and re-check the persistence badge every 5 min.
setInterval(()=>{if(!document.hidden)fetch('/healthz').catch(()=>{});},4*60*1000);
setInterval(()=>{if(!document.hidden)loadPersist();},5*60*1000);
// Coming back to a long-idle tab: the server may have cold-started — reload data
// (drive_pull restores the store) instead of showing a stale/empty page.
document.addEventListener('visibilitychange',()=>{if(!document.hidden){load();loadPersist();}});

checkBuild();
load();
loadPersist();
loadAssumptions();
