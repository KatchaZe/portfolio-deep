// Value formatters and small markup builders shared by every tab. A missing value
// renders as MUTED_DASH in HTML, or as a bare '—' where the text goes into a tooltip.
// Colours are theme tokens (dashboard.css): a map value is a token name such as '--bull'.
const MUTED_DASH='<span class="muted">&mdash;</span>';
const RECO_COLOR={"BUY":"--bull","HOLD / Accumulate":"--accent","HOLD":"--base","SELL / AVOID":"--bear"};
const RECO_LABEL={"BUY":"BUY","HOLD / Accumulate":"ACCUMULATE","HOLD":"HOLD","SELL / AVOID":"SELL / AVOID"};
const ACT_COLOR={"BUY":"--bull","ACCUMULATE":"--bull","WAIT":"--warn","WATCH":"--warn","HOLD":"--muted","HOLD?":"--muted","NO ADD":"--warn","REDUCE":"--bear","TRIM":"--bear","ADD":"--bull"};
const MOMV2_COLOR={"Strong":"--bull","Positive":"--bull","Neutral":"--muted","Weak":"--warn","Negative":"--bear"};
const RANK_COLOR={"Top":"--bull","Mid":"--muted","Bottom":"--bear"};

function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
// '+' in front of a non-negative number, as printed by the backend (no rounding)
function signed(v){return (v>=0?'+':'')+v;}
// a percentage-point figure is shown with the % sign, as every other percent on the page
function pp2pct(s){return String(s==null?'':s).replace(/(\d)\s?pp\b/g,'$1%');}

function fixed(x,d=2){return x==null?MUTED_DASH:Number(x).toFixed(d)}
function money(x){return x==null?MUTED_DASH:'$'+Number(x).toLocaleString('en-US',{maximumFractionDigits:0})}
// $1,234.56 / $1.2B; d decimals below a million, '–' when missing (plain text)
function usd(v,d=2){
  if(v==null||isNaN(v))return '–';
  const a=Math.abs(v),s=v<0?'−':'';
  if(a>=1e9)return s+'$'+(a/1e9).toFixed(1)+'B';
  if(a>=1e6)return s+'$'+(a/1e6).toFixed(1)+'M';
  return s+'$'+a.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
}
function nf(v,d=2){return v==null||isNaN(v)?'–':Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});}
// +1.2% / −3.4% with a true minus sign; unit defaults to %
function sgn(v,d=1,u='%'){return v==null||isNaN(v)?'–':(v>0?'+':v<0?'−':'')+nf(Math.abs(v),d)+u;}
function upcls(v){return v==null?'':v>0?'up':v<0?'down':'';}
function arrow(v){return v==null?'':v>0?'▲ ':v<0?'▼ ':'';}
function signedPct(v){return v==null?MUTED_DASH:signed(v)+'%';}
function pctFixed(v,d=1){return v==null?MUTED_DASH:Number(v).toFixed(d)+'%';}
// green / red signed percent, one decimal, with an arrow so colour is never the only cue
function upDownPct(x){if(x==null)return MUTED_DASH;return `<span class="${upcls(x)}">${arrow(x)}${x>=0?'+':''}${Number(x).toFixed(1)}%</span>`}
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

// tinted background + readable text for a token; `tok` is '--bull', '--bear', ...
function tone(tok){tok=tok||'--muted';return `background:color-mix(in srgb,var(${tok}) 14%,var(--surface));color:color-mix(in srgb,var(${tok}) 72%,var(--ink))`;}
function tag(t,c){return t?`<span class="tag" style="${tone(c)}">${t}</span>`:MUTED_DASH}
function dot(tier){return `<span class="dot ${tier==='green'?'g':tier==='yellow'?'y':'r'}" title="data confidence: ${esc(tier||'?')}"></span>`}
