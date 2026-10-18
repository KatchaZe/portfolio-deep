// Value formatters and small markup builders shared by every tab. A missing value
// renders as MUTED_DASH in HTML, or as a bare '—' where the text goes into a tooltip.
const MUTED_DASH='<span class="muted">&mdash;</span>';
// small grey upper-case caption above a value (card sections / tiles)
const CAPS_LABEL='font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase';
const TILE_LABEL='font-size:calc(9px*var(--fs));color:var(--grey);text-transform:uppercase;letter-spacing:.03em';
const RECO_COLOR={"BUY":"#16a34a","HOLD / Accumulate":"#0d9488","HOLD":"#64748b","SELL / AVOID":"#dc2626"};
const ACT_COLOR={"BUY":"#16a34a","ACCUMULATE":"#0d9488","WAIT":"#ca8a04","WATCH":"#d97706","HOLD":"#64748b","HOLD?":"#94a3b8","NO ADD":"#ea580c","REDUCE":"#dc2626"};
const MOMV2_COLOR={"Strong":"#15803d","Positive":"#16a34a","Neutral":"#94a3b8","Weak":"#d97706","Negative":"#dc2626"};
const RANK_COLOR={"Top":"#15803d","Mid":"#94a3b8","Bottom":"#dc2626"};

function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
// '+' in front of a non-negative number, as printed by the backend (no rounding)
function signed(v){return (v>=0?'+':'')+v;}

function fixed(x,d=2){return x==null?MUTED_DASH:Number(x).toFixed(d)}
function money(x){return x==null?MUTED_DASH:'$'+Number(x).toLocaleString(undefined,{maximumFractionDigits:0})}
function signedPct(v){return v==null?MUTED_DASH:signed(v)+'%';}
function pctFixed(v,d=1){return v==null?MUTED_DASH:Number(v).toFixed(d)+'%';}
// green / red signed percent, one decimal
function upDownPct(x){if(x==null)return MUTED_DASH;const s=x>=0?'up':'down';return `<span class="${s}">${x>=0?'+':''}${Number(x).toFixed(1)}%</span>`}
// a 0.123 ratio as '+12.3%', plain text for tooltips
function ratioPct(v){return v==null?'—':(v>=0?'+':'')+(v*100).toFixed(1)+'%'}
function billions(v){return v==null?'—':'$'+(v/1e9).toFixed(2)+'B';}
// -41000000 -> '-$41M' (prefix '$'); thousands get a 'k' only when asked
function compactNumber(v,prefix,kilo){
  const sign=v<0?'-':'', a=Math.abs(v);
  if(a>=1e9)return sign+prefix+(a/1e9).toFixed(1)+'B';
  if(a>=1e6)return sign+prefix+(a/1e6).toFixed(0)+'M';
  if(kilo&&a>=1e3)return sign+prefix+(a/1e3).toFixed(0)+'k';
  return sign+prefix+a.toFixed(0);
}
function plainNum(x,dp){dp=(dp==null)?2:dp; return (x==null||isNaN(x))?'—':(+x).toFixed(dp);}

function tag(t,c){return t?`<span class="tag" style="background:${c||'#64748b'}">${t}</span>`:MUTED_DASH}
function dot(tier){return `<span class="dot ${tier==='green'?'g':tier==='yellow'?'y':'r'}"></span>`}
