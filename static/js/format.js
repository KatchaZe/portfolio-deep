const RECO_COLOR={"BUY":"#16a34a","HOLD / Accumulate":"#0d9488","HOLD":"#64748b","SELL / AVOID":"#dc2626"};
const ACT_COLOR={"STRONG BUY":"#15803d","BUY":"#16a34a","ACCUMULATE":"#0d9488","HOLD":"#64748b","WAIT":"#ca8a04","WATCH":"#d97706","TRIM":"#ea580c","SELL":"#dc2626","STRONG SELL":"#991b1b"};
const MOM_COLOR={"Bullish":"#16a34a","Neutral":"#94a3b8","Bearish":"#dc2626"};
const MOMV2_COLOR={"Strong":"#15803d","Positive":"#16a34a","Neutral":"#94a3b8","Weak":"#d97706","Negative":"#dc2626"};
const RANK_COLOR={"Top":"#15803d","Mid":"#94a3b8","Bottom":"#dc2626"};
function f(x,d=2){return (x===null||x===undefined)?'<span class="muted">&mdash;</span>':Number(x).toFixed(d)}
function money(x){return (x===null||x===undefined)?'<span class="muted">&mdash;</span>':'$'+Number(x).toLocaleString(undefined,{maximumFractionDigits:0})}
function tag(t,c){return t?`<span class="tag" style="background:${c||'#64748b'}">${t}</span>`:'<span class="muted">&mdash;</span>'}
function momPct(v){return (v===null||v===undefined)?'—':(v>=0?'+':'')+(v*100).toFixed(1)+'%'}
function pct(x){if(x===null||x===undefined)return '<span class="muted">&mdash;</span>';const s=x>=0?'up':'down';return `<span class="${s}">${x>=0?'+':''}${Number(x).toFixed(1)}%</span>`}
function dot(tier){return `<span class="dot ${tier==='green'?'g':tier==='yellow'?'y':'r'}"></span>`}
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fmtB(v){return (v===null||v===undefined)?'—':'$'+(v/1e9).toFixed(2)+'B';}
function pctc(v){return v==null?'<span class="muted">&mdash;</span>':(v>=0?'+':'')+v+'%';}
function rp(v,d=1){return (v===null||v===undefined)?'<span class="muted">&mdash;</span>':Number(v).toFixed(d)+'%';}
function cnum(x,dp){dp=(dp==null)?2:dp; return (x==null||isNaN(x))?'—':(+x).toFixed(dp);}
