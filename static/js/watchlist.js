// The Watchlist tab: names are stored, their analysis is fetched on demand and not kept.
async function loadWatchlist(){try{const r=await fetch('/api/watchlist');const d=await r.json();renderWatchNames(d.names||[]);}catch(e){document.getElementById('watchNames').innerHTML='<span class="banner tone t-bear">&#9888; watchlist load failed: '+esc(String(e))+'</span>';}}
function renderWatchNames(names){
  document.getElementById('watchNames').innerHTML = names.length
    ? `<span class="cap">Watchlist ${names.length} ตัว</span> `+names.map(n=>`<span class="chip num">${esc(n)}<button type="button" class="rm" aria-label="ลบ ${esc(n)} ออกจาก watchlist" onclick="removeWatch('${n}')">&times;</button></span>`).join(' ')
    : '<span class="muted">Watchlist empty — add a ticker above.</span>';
}
async function addWatch(){const t=document.getElementById('wt').value.trim().toUpperCase();if(!t)return;await fetch('/api/watchlist/add?ticker='+encodeURIComponent(t),{method:'POST'});document.getElementById('wt').value='';loadWatchlist();}
async function removeWatch(t){await fetch('/api/watchlist/remove?ticker='+encodeURIComponent(t),{method:'POST'});loadWatchlist();}
async function runWatch(){
  const sp=document.getElementById('spW'),msg=document.getElementById('watchMsg');
  sp.style.display='inline-block';msg.textContent='Running watchlist (fetch on demand)…';
  try{const r=await fetch('/api/watchlist/run',{method:'POST'});const d=await r.json();
    renderWatchNames(d.names||[]);renderWatchTable(d.rows||[]);if(d.quota)renderQuota(d.quota);
    const rfW=d.rf_live===false?' ⚠ Rf fallback':'';
    msg.textContent=`Ran ${(d.rows||[]).length}, errors ${(d.errors||[]).length}${rfW}. ${(d.errors||[]).join('; ')}`;
  }catch(e){msg.textContent='Error: '+e}finally{sp.style.display='none'}
}
function renderWatchTable(rows){
  if(!rows.length){document.getElementById('watchTable').innerHTML='<span class="muted">No results — add tickers and run.</span>';return;}
  document.getElementById('watchTable').innerHTML=`<div class="cards">${rows.map(x=>pcard(x,true)).join('')}</div>`;
}
async function promote(t){
  const sh=prompt('Add '+t+' to My Portfolio.\n\nNumber of shares:');
  if(sh===null)return;
  if(!sh.trim()||isNaN(parseFloat(sh))||parseFloat(sh)<=0){alert('Please enter a valid number of shares.');return;}
  const ac=prompt('Average buy price ($) for '+t+':');
  if(ac===null)return;
  if(!ac.trim()||isNaN(parseFloat(ac))||parseFloat(ac)<=0){alert('Please enter a valid average cost.');return;}
  const u='/api/watchlist/promote?ticker='+encodeURIComponent(t)+'&shares='+encodeURIComponent(parseFloat(sh))+'&avg_cost='+encodeURIComponent(parseFloat(ac));
  await fetch(u,{method:'POST'});
  await load();loadWatchlist();showTab(1);
}
