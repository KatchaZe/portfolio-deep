// The page shell: tabs, build stamp, persistence badge, keep-alive and boot. Loaded last.

// must equal config.BUILD; a browser still running cached JS shows the mismatch banner
const DASH_BUILD="2026-10-28a";

async function checkBuild(){
  const el=document.getElementById('buildStamp'); if(!el)return;
  try{
    const h=await (await fetch('/healthz')).json();
    if(h.build && h.build!==DASH_BUILD){
      el.className='banner tone t-bear'; el.style.display='block';
      el.innerHTML='&#9888; UI build <b>'+DASH_BUILD+'</b> &ne; server <b>'+h.build+'</b> — hard refresh (Ctrl+F5) to load the new dashboard.';
    }else{
      el.className='note'; el.style.display='block';
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
const TAB_ORDER=[1,2,3,6,4,5];
function showTab(n,focus){
  for(const i of TAB_ORDER){const t=document.getElementById('tab'+i),on=i===n;
    t.classList.toggle('active',on);t.setAttribute('aria-selected',on);t.tabIndex=on?0:-1;
    document.getElementById('p'+i).style.display=on?'block':'none';}
  if(focus)document.getElementById('tab'+n).focus();
  if(TAB_OPENERS[n])TAB_OPENERS[n]();
}
// arrow keys move between the top tabs (WAI-ARIA tabs pattern)
document.getElementById('tabs').addEventListener('keydown',e=>{
  const cur=TAB_ORDER.findIndex(i=>document.getElementById('tab'+i)===document.activeElement); if(cur<0)return;
  const k={ArrowRight:1,ArrowLeft:-1}[e.key], to=k?(cur+k+TAB_ORDER.length)%TAB_ORDER.length:e.key==='Home'?0:e.key==='End'?TAB_ORDER.length-1:null;
  if(to!=null){e.preventDefault();showTab(TAB_ORDER[to],true);}
});
// theme: follow the system, or force light / dark (remembered per browser)
const THEMES=['auto','light','dark'];
let __theme='auto'; try{__theme=localStorage.getItem('deep-theme')||'auto';}catch(e){}
function applyTheme(){
  if(__theme==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',__theme);
  const b=document.getElementById('themeBtn'); if(!b)return;
  const name={auto:'ตามระบบ',light:'สว่าง',dark:'มืด'}[__theme];
  b.setAttribute('aria-label','ธีม: '+name+' (กดเพื่อเปลี่ยน)'); b.title=b.getAttribute('aria-label');
  b.innerHTML=__theme==='light'?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    :__theme==='dark'?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
    :'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>';
}
function cycleTheme(){__theme=THEMES[(THEMES.indexOf(__theme)+1)%3];try{localStorage.setItem('deep-theme',__theme);}catch(e){}applyTheme();}
applyTheme();
// Drive backup badge: local only / healthy / failing (pushes blocked so an empty store
// cannot overwrite the backup)
async function loadPersist(){
  const el=document.getElementById('persist'); if(!el)return;
  try{
    const p=await (await fetch('/api/persist')).json();
    if(!p.configured){
      el.className='quota'; el.innerHTML='local only';
      el.title='ไม่ได้ตั้งค่า Google Drive backup — บน Render free tier ข้อมูลจะหายเมื่อ instance restart (ตั้ง GDRIVE_OAUTH_* ตาม GOOGLE_DRIVE_OAUTH_SETUP.md)';
      return;
    }
    const bad=(p.pull_state==='error')||p.push_blocked||(p.push_result==='error');
    if(bad){
      el.className='quota warn'; el.innerHTML='Drive &#10007; error';
      el.title='Google Drive backup มีปัญหา — '+(p.last_error||'pull/push ล้มเหลว')
        +'\nการ push ถูกบล็อกไว้เพื่อป้องกันไฟล์ backup ถูกทับด้วย store ว่าง จะ retry อัตโนมัติทุก 1 นาที'
        +'\nถ้าค้างนาน: refresh token อาจหมดอายุ (OAuth consent screen สถานะ Testing = token หมดใน 7 วัน — กด PUBLISH APP เป็น In production แล้วสร้าง token ใหม่)';
    }else{
      el.className='quota'; el.innerHTML='Drive &#10003;';
      el.title=`Google Drive backup ทำงานปกติ (${p.auth_mode})\nlast pull: ${p.last_pull||'—'} (${p.pull_state||p.pull_result||'—'})\nlast push: ${p.last_push||'—'} (${p.push_result||'—'})`;
    }
  }catch(e){el.innerHTML='Drive ?';}
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
// the tools panel opens on a first desktop visit and then remembers whether you closed it
(function(){const t=document.getElementById('tools'); if(!t)return;
  let pref=null; try{pref=localStorage.getItem('deep-tools');}catch(e){}
  // a phone starts with the tools closed so the portfolio is the first thing on screen
  if(pref==='0'||(pref==null&&window.innerWidth<720))t.open=false;
  t.addEventListener('toggle',()=>{try{localStorage.setItem('deep-tools',t.open?'1':'0');}catch(e){}});})();
