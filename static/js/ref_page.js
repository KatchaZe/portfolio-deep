// The Ref tab's search, expand / collapse and links, on the static markup tools/build_ref.py
// writes. How to links (#ref-<id>) open the Ref tab at that entry.

// every word of the query must appear in the entry's search text (case ignored)
function refWords(q){return (q||'').toLowerCase().trim().split(/\s+/).filter(Boolean);}
function refMatches(words,text){return words.every(w=>text.includes(w));}

const REF={saved:null};
function refApply(q){
  const words=refWords(q), secs=[...document.querySelectorAll('#p5 .refsec')];
  if(words.length&&REF.saved==null)REF.saved=secs.map(s=>s.open);
  let total=0;
  secs.forEach((s,i)=>{
    let n=0;
    s.querySelectorAll('.refent').forEach(e=>{const hit=!words.length||refMatches(words,e.dataset.q||'');e.hidden=!hit;if(hit)n++;});
    total+=n;
    s.hidden=words.length>0&&n===0;
    if(words.length)s.open=n>0;
    else if(REF.saved)s.open=REF.saved[i];
  });
  if(!words.length)REF.saved=null;
  const all=document.querySelectorAll('#p5 .refent').length;
  document.getElementById('refcount').textContent=words.length?`พบ ${total} จาก ${all} รายการ`:`ทั้งหมด ${all} รายการ`;
  document.getElementById('refempty').hidden=!(words.length&&total===0);
  return total;
}
function refSetAll(open){document.querySelectorAll('#p5 .refsec').forEach(s=>{if(!s.hidden)s.open=open;});}
// open the Ref tab at a section (#refsec-x) or an entry (#ref-x) and move focus there
function refGo(hash){
  const el=document.getElementById(hash.replace(/^#/,'')); if(!el)return false;
  if(typeof showTab==='function'&&document.getElementById('p5').style.display==='none')showTab(5);
  const sec=el.matches('.refsec')?el:el.closest('.refsec');
  if(el.hidden||(sec&&sec.hidden)){document.getElementById('refq').value='';refApply('');}
  if(sec)sec.open=true;
  const target=el.matches('.refsec')?el.querySelector('summary'):el;
  target.scrollIntoView({block:'start'}); target.focus({preventScroll:true});
  return true;
}
function refInit(){
  const q=document.getElementById('refq'); if(!q)return;
  q.addEventListener('input',()=>refApply(q.value));
  document.getElementById('refOpenAll').addEventListener('click',()=>refSetAll(true));
  document.getElementById('refCloseAll').addEventListener('click',()=>refSetAll(false));
  document.getElementById('refClear').addEventListener('click',()=>{q.value='';refApply('');q.focus();});
  document.addEventListener('click',e=>{
    const a=e.target.closest&&e.target.closest('a[href^="#ref"]'); if(!a)return;
    if(refGo(a.getAttribute('href')))e.preventDefault();
  });
}
// a shared link to an entry: wait for app.js (showTab) before opening the tab
window.addEventListener('load',()=>{if(/^#ref/.test(location.hash))refGo(location.hash);});
refInit();
