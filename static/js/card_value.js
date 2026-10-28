// Value blocks on a holding card: anchor fair value, upside, the price-vs-value scale, the
// pre-profit (S20) panel, the reverse-DCF "priced-in" line and the valuation cross-checks
// (scenarios, story Monte Carlo, relative valuation) drawn as charts.

function methodShort(m){return (m||'').replace('Terminal-Anchored Reverse DCF','RevDCF').replace('Young-Company DCF (failure-adjusted)','YoungDCF').replace('Future Value Projection','FVP').replace('Fundamental PEG','PEG').replace('Justified PEG','PEG').replace('Excess Return Model','ExRet').replace('FCFF DCF','DCF')}
// what the FCFF DCF assumed, for the hover on its value
function dcfTip(x){
  const k=x.key_metrics||{}, s=(x.dcf||{}).sources||{};
  const gp=k.dcf_growth_path_pct&&k.dcf_growth_path_pct.length?k.dcf_growth_path_pct:[k.dcf_growth_pct??'?'];
  const parts=['FCFF DCF (Damodaran): revenue growth '+gp.join('%, ')+'% (ปีที่ 1'+(gp.length>1?'–'+gp.length:'')+') แล้วลดเป็นเส้นตรงสู่ g ระยะยาวในปีที่ 10'+(s.growth?' ('+s.growth+')':''),
    'operating margin เป้าหมาย '+(k.dcf_margin_target_pct??'?')+'%',
    'sales-to-capital '+(k.dcf_sales_to_capital??'?')+(s.sales_to_capital?' ('+s.sales_to_capital+')':''),
    'terminal value = '+(k.dcf_terminal_share_pct??'?')+'% ของมูลค่า'];
  if(k.crp_pct!=null)parts.push('country risk +'+k.crp_pct+'% ('+(k.crp_basis||'')+')');
  if(k.company_type&&k.company_type!=='standard')parts.push('model: '+k.company_type);
  return pp2pct(parts.join(' · '));
}
// "Ambitious — needs 31%/y ..." -> "Ambitious"
function verdictHead(v){return (v||'').split('—')[0].trim();}

// forward-EPS blend: source count, plus the min–max range when there is more than one
function consensusBadge(x){
  const n=x.forward_eps_n||0; if(!n||x.forward_eps==null)return '';
  const src=x.forward_eps_sources?Object.keys(x.forward_eps_sources).join(', '):'';
  const rng=(x.forward_eps_low!=null&&x.forward_eps_high!=null&&n>1)
    ? ` range $${x.forward_eps_low}–$${x.forward_eps_high} (${x.forward_eps_spread_pct}% spread)` : '';
  const tip=`fwd EPS $${x.forward_eps} · ${n} source${n>1?'s':''}: ${src}${rng}`;
  const tok=n>=2?(x.forward_eps_spread_pct!=null&&x.forward_eps_spread_pct>25?'--bear':'--bull'):'--muted';
  return ` ${chip('EPS×'+n,tok,esc(tip))}`;
}
function anchorCell(x){
  const m=methodShort(x.anchor_method);
  const altLine=fairValueAlternatives(x,m);
  if(x.anchor_value!=null)return `${fixed(x.anchor_value)}${consensusBadge(x)}<div class="note">${m}</div>${altLine}`;
  if(x.rev_implied_cagr!=null)return `<b style="color:color-mix(in srgb,var(--violet) 80%,var(--ink))">RevDCF</b>${consensusBadge(x)}<div class="note">implies ${x.rev_implied_cagr}% CAGR</div>${altLine}`;
  return MUTED_DASH+altLine;
}
// Every computed method (DCF / ExRet, FVP, PEG); ★ marks the anchor behind Upside. When the two are
// far apart the low–high span is a method conflict, not a confidence band, so say so.
function fvMethods(x){
  const alts=[];
  if(x.fv_dcf!=null){const bad=(x.dcf||{}).discarded;
    alts.push([bad?'DCF✗':'DCF',x.fv_dcf,bad?'ตัดทิ้ง: '+bad+' — น่าจะมาจาก input ที่ผิดปกติ (เช่น EBIT ที่มีรายการพิเศษ) ไม่ได้ใช้คิด upside และไม่อยู่ในช่วง low–high · '+dcfTip(x):dcfTip(x)]);}
  if(x.fv_excess_return!=null)alts.push(['ExRet',x.fv_excess_return,'Excess-return model (ธนาคาร/ประกัน): book equity + PV ของ (ROE − Ke) × book equity']);
  if(x.fv_fvp!=null)alts.push(['FVP',x.fv_fvp,'Future Value Projection']);
  if(x.fv_peg!=null)alts.push(['PEG',x.fv_peg,'Justified PEG']);
  return alts;
}
function fvClash(x){
  const dx=(x.key_metrics||{}).fv_disagreement_x;
  return dx?` <span class="warnc" style="font-weight:700;cursor:help" title="${esc('สองวิธีให้คำตอบห่างกัน '+dx+' เท่า — ช่วง low–high นี้คือ "วิธีไม่ตรงกัน" ไม่ใช่ "ช่วงความมั่นใจ" ให้ยึดวิธีหลัก (★) และอ่าน flag ประกอบ')}">&#9888; ต่างกัน ${dx}&times;</span>`:'';
}
function fairValueAlternatives(x,anchor){
  const alts=fvMethods(x); if(!alts.length)return '';
  return `<div class="note" title="Fair Value จากทุกวิธีที่คำนวณได้ — ★ = วิธีหลักที่เลือกใช้คิด Upside (ตาม methodology verdict)">`
    + alts.map(a=>{const t=`${a[0]===anchor?'&#9733;':''}${a[0]} $${Number(a[1]).toFixed(2)}`;
        return a[2]?`<span title="${esc(a[2])}" style="cursor:help">${t}</span>`:t;}).join(' · ')+fvClash(x)+`</div>`;
}
// Margin of safety (V − P)/V, the one definition the score, gauge, advice and Risk Desk
// share; upside (V − P)/P is the return if price converges, shown underneath.
function mosOf(x){return x.mos_pct!=null?x.mos_pct:(x.upside_pct!=null&&x.upside_pct>-100?Math.round(x.upside_pct/(100+x.upside_pct)*1000)/10:null);}
function upsideCell(x){
  if(x.upside_pct!=null){
    const net=(x.net_upside_pct!=null)?` · net ${signed(x.net_upside_pct)}%`:'';
    return `<span title="Margin of safety = (Fair value − ราคา) / Fair value · บวก = ราคาต่ำกว่ามูลค่า">${upDownPct(mosOf(x))}</span>`
      +`<div class="note" title="upside = (FV − ราคา)/ราคา = ผลตอบแทนถ้าราคาวิ่งถึง FV · net = หลังค่าเทรด + ภาษี (Damodaran S6)">upside ${signed(x.upside_pct)}%${net}</div>`;
  }
  // "no upside" and "we cannot value this" are different answers: a pre-profit name
  // whose S20 DCF was not promoted to the anchor says why
  const yb=(x.young_dcf&&!x.young_dcf.promote)?x.young_dcf.blocked_reason:null;
  if(yb){
    return `<span title="${esc('Young-Company DCF (S20) ran but is informational only: '+yb)}" class="warnc" style="cursor:help">ตีมูลค่าไม่ได้ &#9432;</span>`
      +`<div class="note">${x.rev_verdict?esc(verdictHead(x.rev_verdict)):''}</div>`;
  }
  if(x.rev_verdict){
    // actual revenue growth next to the market-implied CAGR: performance vs expectation
    const act=(x.rev_actual_1y!=null)
      ? `<div class="note" title="Revenue growth ที่ทำได้จริงล่าสุด (1y) เทียบกับ implied CAGR ที่ตลาด price-in">actual ${signed(x.rev_actual_1y)}%/y${x.rev_implied_cagr!=null?` vs ${x.rev_implied_cagr}%`:''}</div>`:'';
    return `<span title="${esc(x.rev_verdict)}" class="muted">${verdictHead(x.rev_verdict)||'priced-in'}</span>${act}`;
  }
  return MUTED_DASH;
}

// S20 pre-profit valuation: going concern, minus failure risk, banded by Monte Carlo.
// Failure risk is its own probability, never folded into the discount rate.
function youngPanel(x){
  const y=x.young_dcf; if(!y)return '';
  const surv=Math.round((y.p_survival||0)*100);
  return `<div class="box">`+youngHeader(y,surv)+youngBand(y,y.monte_carlo||{},x.price)+youngChain(y,surv)+youngSurvivalRange(y)
    +((!y.promote||y.uncertain)?`<div class="note warnc">${y.promote?'ใช้เป็น FV หลักแต่ไม่แน่นอน (P ไม่เกินกลาง): ':''}${esc(y.blocked_reason||'')}</div>`:'')
    +pricedInLine(x)+`</div>`;
}
function youngHeader(y,surv){
  const inp=y.inputs||{};
  const tipInputs=['Inputs (derive จากงบที่ยื่นจริงทั้งหมด):',
    `• growth ปีแรก ${inp.g_high_pct}% — ${inp.g_high_src||''}`,
    `• margin ${inp.current_margin_pct}% → ${inp.target_margin_pct}% — ${inp.target_margin_src||''}`,
    `• sales-to-capital ${inp.sales_to_capital} — ${inp.sales_to_capital_src||''}`,
    `• survival ${surv}% — ${inp.p_survival_src||''}`,
    `• SBC dilution ${inp.annual_dilution_pct}%/ปี (อยู่ใน margin GAAP แล้ว ไม่หักซ้ำ) · WACC ${inp.wacc_pct}% · ROIC ปลายทาง ${inp.roic_stable_pct}%`,
    `• terminal = ${y.terminal_share_of_value_pct}% ของมูลค่า`].join('\n');
  return `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
    <h3>Pre-profit forward value (S20)</h3>
    ${chip(`survival ${surv}% &#9432;`,'--accent',esc(tipInputs))}
    ${y.promote?(y.uncertain?chip('ไม่แน่นอน &#9432;','--warn',esc('Fair Value หลักแต่ช่วงกว้าง/ติดลบ — คะแนน Price ไม่เกินกลาง: '+(y.blocked_reason||''))):''):chip('ข้อมูลเสริม &#9432;','--warn',esc('ยังไม่ใช้เป็น Fair Value หลัก: '+(y.blocked_reason||'')))}</div>`;
}
// distress (floor) -> p10 -> p50 -> p90 -> going concern (ceiling), with the price marked
function youngBand(y,mc,px){
  if(!(mc.p10!=null&&mc.p90!=null&&mc.p90>mc.p10))return '';
  const lo=Math.min(mc.p10,y.distress_per_share||0,px||mc.p10);
  const hi=Math.max(mc.p90,y.going_concern_per_share||0,px||mc.p90);
  const at=v=>Math.max(0,Math.min(100,((v-lo)/(hi-lo))*100));
  const l=at(mc.p10), r=at(mc.p90), m=at(mc.p50);
  const mark=(v,cls,lbl)=>v==null?'':`<div class="${cls}" title="${esc(lbl)}" style="left:${at(v).toFixed(1)}%"></div>`;
  return `<div class="scale" role="img" aria-label="${esc('ช่วงมูลค่า Monte Carlo p10 $'+mc.p10+' p50 $'+mc.p50+' p90 $'+mc.p90+' เทียบราคา $'+px)}" title="${esc('ช่วงมูลค่าจาก Monte Carlo 1000 รอบ (สุ่ม target margin, sales-to-capital, growth, survival)\np10 $'+mc.p10+' · p50 $'+mc.p50+' · p90 $'+mc.p90+'\nพื้นล่างสุด = distress $'+y.distress_per_share+' (เงินสดสุทธิ ถ้ากิจการมีค่าศูนย์)')}">
      <div class="tr"></div><div class="band" style="left:${l.toFixed(1)}%;width:${Math.max(1,r-l).toFixed(1)}%;background:var(--accent)"></div>
      <div class="tick" style="left:${m.toFixed(1)}%;background:var(--accent)"></div>
      ${mark(px,'pm','ราคาปัจจุบัน $'+px)}${mark(y.distress_per_share,'tick','distress floor $'+y.distress_per_share)}</div>
    <div class="scalelab"><span>p10 <b>$${mc.p10}</b></span><span>p50 <b>$${mc.p50}</b></span><span>p90 <b>$${mc.p90}</b></span>${px!=null?`<span><i class="lg px"></i>ราคา <b>$${px}</b></span>`:''}</div>`;
}
// p_survival is an assumption (a runway table, not his sector failure statistics), so
// the card shows what the value is if it is 20 points off either way.
const RUNWAY_TABLE='ตาราง runway → survival: ไม่เผาเงิน 90% · ≥5 ปี 85% · ≥3 ปี 75% · ≥2 ปี 65% · ≥1 ปี 50% · <1 ปี 30% · ไม่มีเงินสด 25% · รายได้ ≥$5B +5 จุด';
function youngSurvivalRange(y){
  const p=y.p_survival, gc=y.going_concern_per_share, d=y.distress_per_share||0;
  if(p==null||gc==null)return '';
  const at=q=>{q=Math.max(0,Math.min(1,q));return {q:Math.round(q*100),v:(q*gc+(1-q)*d).toFixed(2)}};
  const lo=at(p-0.2), hi=at(p+0.2);
  return `<div class="note warnc" title="${esc('survival เป็นสมมติฐาน ไม่ใช่ข้อมูลที่ยื่น — Damodaran ใช้สถิติการล้มของ sector ปรับด้วย runway; ที่นี่ใช้ runway อย่างเดียว\n'+RUNWAY_TABLE)}">
    &#9888; survival ${Math.round(p*100)}% = สมมติฐาน (จาก cash runway) · ถ้า ${lo.q}% → <b>$${lo.v}</b> · ถ้า ${hi.q}% → <b>$${hi.v}</b> &#9432;</div>`;
}
// value = p_survival × going concern + (1 − p_survival) × distress
function youngChain(y,surv){
  return `<div style="font-size:var(--t-sm)" title="${esc('Damodaran S20: failure risk ต้องเป็นความน่าจะเป็นแยกต่างหาก ห้ามยัดเข้า discount rate (เขาเรียกว่า blunt instrument)\nvalue = p_survival × going-concern + (1−p_survival) × distress')}">
    <span class="muted">ถ้ารอด</span> <b class="num">$${y.going_concern_per_share}</b>
    <span class="muted">&times; ${surv}%</span>
    <span class="muted">+ ถ้าไม่รอด</span> <b class="num">$${y.distress_per_share}</b>
    <span class="muted">&times; ${100-surv}% =</span> <b class="num">$${y.failure_adjusted_per_share}</b></div>`;
}

// Pricing (reverse DCF: the growth the price implies) beside the S20 valuation; they
// answer different questions, so the line stays even when the young DCF anchors.
// actual = FY-over-FY revenue growth, as the company reports it. For a loss-maker the
// implied CAGR rests on an assumed terminal margin (±5% points moves it ~7%), so it is
// never shown without its band. When no growth rate reaches the price, the verdict says so.
function pricedInLine(x){
  const imp=x.rev_implied_cagr;
  const head=`<div class="cap" title="Reverse DCF = เกม pricing (ตลาดคาดอะไร) ไม่ใช่ valuation — ใช้เป็นตัวเทียบกับมูลค่าข้างบน">ราคาปัจจุบัน price-in (10y)</div>`;
  if(imp==null){
    const vd=x.rev_verdict; if(!vd) return '';
    return `<div class="box" style="gap:2px">${head}<div class="warnc" style="font-size:var(--t-sm)">หา CAGR ที่รองรับราคานี้ไม่ได้ — ${esc(vd)}</div></div>`;
  }
  const act=x.rev_actual_1y, s=x.rev_sensitivity||{};
  const both=s.bull!=null&&s.bear!=null;
  const lo=both?Math.min(s.bull,s.bear):null, hi=both?Math.max(s.bull,s.bear):null;
  const bandTxt=(lo!=null&&hi!=null&&hi>lo)
    ? ` <span class="note">(${lo}–${hi}% ถ้า margin ±5%)</span>`:'';
  const warn=x.terminal_margin_anchored?'' : ` <span title="${esc('terminal margin ของหุ้นขาดทุนวัดจากงบไม่ได้ จึงเป็นสมมติฐาน — implied CAGR ตัวเดียวจึงเชื่อไม่ได้ ต้องอ่านคู่กับช่วง ±5%\n'+(x.terminal_margin_label||''))}" class="warnc" style="cursor:help;font-size:var(--t-xs)">margin สมมติ &#9432;</span>`;
  const gap=(act!=null)
    ? `<div class="note">ทำได้จริงล่าสุด <b>${signed(act)}%</b>/ปี &middot; ${imp<=act?'ตลาดคิดต่ำกว่าที่ทำได้':'ตลาดคิดสูงกว่าที่ทำได้'}</div>`:'';
  return `<div class="box" style="gap:2px">${head}<div style="font-size:var(--t-sm)">โต <b>${imp}%</b>/ปี${bandTxt} ${warn}</div>${gap}</div>`;
}

// Value first (S5/S12-13): the card's lead block. MoS against fair value with the
// Bear–Bull band and the price marked, or, with no fair value, the growth the market
// prices in against the growth actually delivered.
function valueGauge(x){
  if(x.upside_pct!=null&&x.anchor_value!=null)return priceVsValueGauge(x);
  if(x.rev_implied_cagr!=null)return impliedVsActualBars(x);
  return '';
}
function priceVsValueGauge(x){
  const c=((x.scenarios||{}).cases)||null, mos=mosOf(x), meth=methodShort(x.anchor_method);
  const bear=c?c[0].value:x.range_low, base=x.anchor_value, bull=c?c[c.length-1].value:x.range_high;
  const hi=Math.max(x.price||0,bull||0,base||0)*1.08||1, at=v=>Math.max(0,Math.min(100,(v||0)/hi*100));
  const lo=Math.min(bear??base,bull??base), up=Math.max(bear??base,bull??base);
  const ratio=x.price&&base?x.price/base:null;
  return `<div class="value">
      <div><div class="cap">Margin of safety</div><div class="mosbig ${upcls(mos)}" title="(Fair value − ราคา) / Fair value">${mos==null?'–':sgn(mos,1)}</div></div>
      <div class="fvbox"><div class="cap">Fair value · ${esc(meth)}</div><b>${usd(base)}</b>${consensusBadge(x)}
        <div class="note" title="upside = (FV − ราคา)/ราคา · net = หลังค่าเทรด ~0.2% + ภาษี capital gains ~15% — ตัดสินใจด้วย net เสมอ (Damodaran S6)">upside ${sgn(x.upside_pct)} · net ${sgn(x.net_upside_pct)}${mos!=null&&mos<-200&&ratio?` · ราคา ${nf(ratio,1)}× FV`:''}</div></div></div>
    <div class="scale" role="img" aria-label="${esc(`ราคา ${usd(x.price)} เทียบช่วงมูลค่า Bear ${usd(bear)} ถึง Bull ${usd(bull)} และ Fair value ${usd(base)}`)}">
      <div class="tr"></div><div class="band" style="left:${at(lo)}%;width:${Math.max(.8,at(up)-at(lo))}%"></div>
      <div class="tick" style="left:${at(base)}%" title="Fair value ${usd(base)}"></div>
      <div class="pm" style="left:${at(x.price)}%" title="ราคา ${usd(x.price)}"></div></div>
    <div class="scalelab"><span><i class="lg band"></i>${c?'Bear':'low'} <b>${usd(bear,0)}</b> – ${c?'Bull':'high'} <b>${usd(bull,0)}</b></span><span><i class="lg fv"></i>FV <b>${usd(base,0)}</b></span><span><i class="lg px"></i>ราคา <b>${usd(x.price,0)}</b></span></div>`;
}
const VERDICT_TOK={Plausible:'--bull',Ambitious:'--warn',Aggressive:'--bear',Exceptional:'--bear'};
function impliedVsActualBars(x){
  const imp=x.rev_implied_cagr, act=x.rev_actual_1y, mx=Math.max(imp||0,act||0,1);
  const bar=(v,tok)=>`<span class="t" style="flex:1;height:9px"><span class="f" style="width:${Math.max(3,Math.min(100,v/mx*100)).toFixed(0)}%;background:var(${tok})"></span></span>`;
  const verd=verdictHead(x.rev_verdict);
  return `<div class="box" style="gap:4px"><div class="cap" title="Reverse DCF: ราคาปัจจุบัน price-in การเติบโตรายได้กี่ %/ปี (10 ปี) เทียบที่ทำได้จริงล่าสุด">Market prices-in vs ทำได้จริง (10y CAGR)</div>
      <div style="display:flex;align-items:center;gap:8px;font-size:var(--t-sm)"><span style="width:7em;flex:none">implied <b class="num">${imp}%</b></span>${bar(imp,'--bear')}</div>
      ${act!=null?`<div style="display:flex;align-items:center;gap:8px;font-size:var(--t-sm)"><span style="width:7em;flex:none">actual <b class="num">${act}%</b>/y</span>${bar(act,'--bull')}</div>`:''}
      <div>${chip(esc(verd||'priced-in'),VERDICT_TOK[verd]||'--muted',esc(x.rev_verdict||''))} <span class="note">ช่องว่างระหว่างแท่ง = ศรัทธาที่ตลาดจ่ายล่วงหน้า</span></div></div>`;
}

// ---- detail tab "มูลค่า": the methods table and the cross-checks on the primary model
// (domain/engine/scenarios.py). None of them moves Anchor FV or the Price pillar.
const SCEN_TOK={Bear:'--bear',Base:'--base',Bull:'--bull'};
function pricedInWhy(note){
  return note==='above'?'เกินช่วงที่โมเดลหา (margin 80% / growth สูงสุด)':note==='below'?'ต่ำกว่าช่วงที่หา':note==='decreasing'?'ยิ่งโตยิ่งเสียมูลค่า (ผลตอบแทนเงินลงทุนใหม่ต่ำกว่าต้นทุนเงินทุน)':'คำนวณไม่ได้';
}
function vsPrice(v,px){if(v==null||!px)return '–';const d=(v-px)/px*100;return `<span class="${upcls(d)}">${sgn(d,0)}</span>`;}
function valueMethodsTable(x){
  const m=methodShort(x.anchor_method), r=x.relative||{}, ind=r.industry||{}, sc=x.scenarios||{};
  const rows=[];
  if(x.anchor_value!=null)rows.push(['Anchor · '+m,x.anchor_value,true,esc(x.anchor_method||'')]);
  fvMethods(x).forEach(a=>{if(a[0]!==m||x.anchor_value==null)rows.push([a[0],a[1],false,esc(a[2]||'')]);});
  if(r.value_regression!=null)rows.push(['Relative · peer regression',r.value_regression,false,'']);
  if(r.value_median!=null)rows.push(['Relative · peer median',r.value_median,false,'']);
  if(ind.value_avg!=null)rows.push(['อุตสาหกรรม '+(ind.industry||''),ind.value_avg,false,'']);
  if(sc.expected!=null)rows.push(['Scenario 25/50/25',sc.expected,false,'']);
  if(sc.story_mc&&sc.story_mc.p50!=null)rows.push(['Story Monte Carlo p50',sc.story_mc.p50,false,'']);
  if(!rows.length)return '';
  return `<div class="box"><div class="tw" tabindex="0" role="region" aria-label="ตารางมูลค่าต่อหุ้น"><table><caption>มูลค่าต่อหุ้นจากแต่ละวิธี ${fvClash(x)}</caption>
    <thead><tr><th scope="col" class="l">วิธี</th><th scope="col" class="n">มูลค่า</th><th scope="col" class="n">เทียบราคา</th></tr></thead><tbody>
    ${rows.map(([n,v,p,t])=>`<tr${p?' class="tot"':''}><th scope="row" class="l" style="color:var(--ink);font-size:var(--t-sm);font-weight:${p?700:400}"${t?` title="${t}"`:''}>${esc(n)}${p?' <span class="note">(ใช้ตัดสิน)</span>':''}</th><td class="n">${usd(v)}</td><td class="n">${vsPrice(v,x.price)}</td></tr>`).join('')}
    </tbody></table></div><p class="note" style="margin:0">ช่วง ${usd(x.range_low)} – ${usd(x.range_high)} · ทุกวิธีนอกจาก Anchor เป็น cross-check ไม่เปลี่ยน FV และคะแนน P</p></div>`;
}
function scenarioPanel(x){
  const sc=x.scenarios, pi=x.priced_in; if(!sc||!sc.cases)return '';
  const px=x.price, vals=sc.cases.map(c=>c.value), hi=Math.max(px||0,sc.expected||0,...vals)*1.1||1;
  const at=v=>Math.max(0,Math.min(100,v/hi*100));
  const rows=sc.cases.map(c=>`<div class="sbar" title="${esc(c.name+': margin '+c.margin_pct+'% · growth ปีแรก '+c.growth_y1_pct+'%')}">
      <span style="color:color-mix(in srgb,var(${SCEN_TOK[c.name]}) 80%,var(--ink));font-weight:700">${c.name}</span>
      <span class="t"><span class="f" style="width:${at(c.value).toFixed(1)}%;background:var(${SCEN_TOK[c.name]});opacity:.85"></span>${px?`<span class="m" style="left:${at(px).toFixed(1)}%"></span>`:''}</span>
      <span class="num" style="text-align:right">${usd(c.value)}</span></div>`).join('');
  const pb=sc.implied_bull_prob;
  const pbTxt=pb!=null?(pb===0?'Bear + Base ก็คุ้มราคาแล้ว':`ราคาเท่ากับโอกาส Bull <b>${Math.round(pb*100)}%</b> (ตั้งไว้ 25%)`):'ทุก scenario ต่ำกว่าราคา';
  const mos=sc.mos!=null?`${sc.mos>=0?'+':''}${Math.round(sc.mos*100)}%`:'–';
  let piLine='';
  if(pi){
    const m=pi.margin_pct!=null?`margin <b>${pi.margin_pct}%</b> <span class="note">(ตอนนี้ ${pi.margin_now_pct}% · โมเดล ${pi.margin_model_pct}%)</span>`:`margin: ${pricedInWhy(pi.margin_note)}`;
    const g=pi.growth_pct!=null?`หรือรายได้โต <b>${pi.growth_pct}%</b>/ปี 5 ปี → ปีที่ 10 = <b>${pi.rev10_x}×</b> ของวันนี้`:`growth: ${pricedInWhy(pi.growth_note)}`;
    piLine=`<div style="font-size:var(--t-sm)" title="${esc('ใช้โมเดลเดียวกับ Anchor FV แล้วแก้สมการหาตัวแปรเดียวที่ทำให้มูลค่าเท่าราคา ตัวแปรอื่นคงเดิม')}">ราคาต้องการ ${m} · ${g}</div>`;
  }
  const destroy=sc.growth_destroys_value?`<div class="note warnc">โตช้ากว่ากลับได้มูลค่ามากกว่า: ที่ margin นี้การโตใช้เงินลงทุนมากกว่าที่สร้าง</div>`:'';
  return `<div class="box"><h3>Scenario &amp; price-in <span class="note">คาดหวัง ${usd(sc.expected)} · MoS ${mos}</span></h3>${piLine}
    <div style="display:grid;gap:4px">${rows}</div>
    <div style="font-size:var(--t-sm)">25/50/25 → คาดหวัง <b class="num">${usd(sc.expected)}</b> · ${pbTxt}</div>${destroy}
    <p class="note" style="margin:0">ขีดดำ = ราคา · ${esc(pp2pct(sc.basis))} · cross-check เท่านั้น ไม่เปลี่ยน FV และคะแนน P</p></div>`;
}

// SVG helpers shared by the card charts and the Allocation / Correlation tabs
function niceStep(raw){const p=Math.pow(10,Math.floor(Math.log10(raw||1))),f=raw/p;return (f<1.5?1:f<3?2:f<7?5:10)*p}
function svgChart(w,h,label,body){return `<svg class="ch" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${body}</svg>`;}
// labels nudged up/down until they stop overlapping each other or the given boxes; a moved
// label gets a leader line back to its point
function placeLabels(list,obs){
  const placed=[...(obs||[])],cw=6.6,h=12;let out='';
  list.forEach(l=>{const w=String(l.t).length*cw;let y=l.y,tries=0;
    const box=yy=>{const x0=l.a==='end'?l.x-w:l.x;return [x0,yy-h+2,x0+w,yy+2]};
    const hit=b=>placed.some(p=>b[0]<p[2]&&b[2]>p[0]&&b[1]<p[3]&&b[3]>p[1]);
    while(hit(box(y))&&tries<8){tries++;y=l.y+(tries%2?1:-1)*Math.ceil(tries/2)*h}
    const bx=box(y);placed.push(bx);
    if(y!==l.y&&l.px!=null){const lx=l.a==='end'?bx[2]+1:bx[0]-1;out+=`<line x1="${l.px}" y1="${l.py}" x2="${lx}" y2="${y-4}" stroke="var(--muted)" stroke-width=".8"/>`;}
    out+=`<text x="${l.x}" y="${y}" text-anchor="${l.a}" class="${l.c||''}">${esc(l.t)}</text>`;});
  return out;
}

// Story Monte Carlo: the same story inputs sampled between Bear and Bull (1,000 draws,
// seeded). The bars are the value histogram; the dark line is the price.
function storyMcBlock(mc,px){
  if(!mc||!mc.bins||!mc.bins.length)return '';
  const W=460,H=190,L=10,R=10,B=158,T=14, w=mc.bin_width||1, span=w*mc.bins.length, hiX=Math.max(span,px||0)*1.04;
  const x=v=>L+(W-L-R)*v/hiX, top=Math.max(...mc.bins)||1;
  let s=mc.bins.map((c,i)=>{const h=(B-T)*c/top;return `<rect x="${(x(i*w)+.5).toFixed(1)}" y="${(B-h).toFixed(1)}" width="${Math.max(1,x((i+1)*w)-x(i*w)-1).toFixed(1)}" height="${h.toFixed(1)}" fill="var(--accent)" opacity="${(i+.5)*w>=(px||0)?.9:.45}"/>`}).join('');
  s+=`<line x1="${L}" x2="${W-R}" y1="${B}" y2="${B}" stroke="var(--line)"/>`;
  const st=niceStep(hiX/5); for(let t=0;t<=hiX;t+=st)s+=`<text x="${x(t)}" y="${B+14}" text-anchor="middle">${usd(t,0)}</text>`;
  if(mc.p10!=null&&mc.p90!=null)s+=`<rect x="${x(mc.p10)}" y="${B+2}" width="${Math.max(1,x(mc.p90)-x(mc.p10))}" height="3" fill="var(--bull)" opacity=".55"/>`;
  if(mc.p50!=null){const a=x(mc.p50)>W*0.6?'end':'start';s+=`<line x1="${x(mc.p50)}" x2="${x(mc.p50)}" y1="${T}" y2="${B}" stroke="var(--bull)" stroke-dasharray="4 3" stroke-width="1.5"/><text x="${x(mc.p50)+(a==='end'?-5:5)}" y="${T+44}" text-anchor="${a}" class="bull">p50 ${usd(mc.p50)}</text>`;}
  if(px){const a=x(px)>W*0.7?'end':'start';s+=`<line x1="${x(px)}" x2="${x(px)}" y1="${T-4}" y2="${B}" stroke="var(--mark)" stroke-width="2.5"/><text x="${x(px)+(a==='end'?-5:5)}" y="${T+6}" text-anchor="${a}" class="strong">ราคา ${usd(px)}</text>`;}
  const share=Math.round((mc.prob_above_price||0)*100);
  const story=mc.story||{}, mg=story.margin_pct||[];
  return `<div class="box"><h3>Story Monte Carlo <span class="note">${nf(mc.n||1000,0)} แบบ</span></h3>
    ${svgChart(W,H+4,`ฮิสโทแกรมมูลค่าต่อหุ้น p10 ${usd(mc.p10)} p50 ${usd(mc.p50)} p90 ${usd(mc.p90)} เทียบราคา ${usd(px)}`,s)}
    <div style="font-size:var(--t-sm)">p10 <b class="num">${usd(mc.p10)}</b> · p50 <b class="num">${usd(mc.p50)}</b> · p90 <b class="num">${usd(mc.p90)}</b> · story ที่คุ้มราคา <b class="num">${share}%</b></div>
    <p class="note" style="margin:0">แท่งเข้ม = มูลค่าสูงกว่าราคา · แถบเขียวใต้แกน = ช่วง p10–p90${mg.length?` · สุ่ม margin ${mg.join(' / ')}%`:''}${story.growth?' · growth '+esc(pp2pct(story.growth)):''}${story.sales_to_capital?' · S2C '+esc(story.sales_to_capital):''}${story.wacc?' · WACC '+esc(pp2pct(story.wacc)):''}${story.survival?' · survival '+esc(story.survival):''} · ไม่ใช่ช่วงความคลาดเคลื่อนของ FV หลัก</p></div>`;
}

// Relative valuation on the firm's own peers (weekly fit, target side every refresh):
// EV/Sales regressed on operating margin (+ growth when n >= 8), peers from
// Damodaran's company list (FMP fills in below 5), plus the industry multiple.
function relativeIndustry(x){
  const d=x.relative&&x.relative.industry; if(!d)return '';
  const px=x.price, pct=v=>v==null?'–':`${(v*100).toFixed(1)}%`;
  const vs=v=>v&&px?` <span class="note">(${v>=px?'+':''}${Math.round((v-px)/px*100)}%)</span>`:'';
  const fl=(d.flags||[]).map(f=>`<div class="note warnc">${esc(f)}</div>`).join('');
  const other=/app industry mapping/.test(d.basis||'')?' · ใช้อุตสาหกรรมของแอป (ไม่อยู่ในรายชื่อตลาด US ของ Damodaran)':'';
  return `<div style="font-size:var(--t-sm)" title="${esc('EV/Sales รวมของอุตสาหกรรม (Damodaran psdata) × รายได้ของหุ้นนี้ − หนี้สุทธิ · ปรับ margin เฉพาะเมื่อ margin ห่างจากอุตสาหกรรม 0.5–2 เท่า')}">
    อุตสาหกรรม <b>${esc(d.industry)}</b> <span class="note">(${d.n!=null?Math.round(d.n):'?'} บริษัท · Damodaran ${esc(d.as_of||'')}${other})</span><br>
    ${d.ev_sales}× (margin ${pct(d.margin)}) → <b class="num">${d.value_avg!=null?usd(d.value_avg):'–'}</b>${vs(d.value_avg)}${d.value_margin_adj!=null?` · ปรับ margin ×${d.margin_ratio} → <b class="num">${usd(d.value_margin_adj)}</b>${vs(d.value_margin_adj)}`:''}</div>${fl}`;
}
function relativeScatter(x){
  const r=x.relative||{}, m=r.model; if(!m||!(m.peers||[]).length||r.target_margin==null)return '';
  const pts=m.peers.filter(p=>p.margin!=null&&p.ev_sales!=null);
  const ms=[...pts.map(p=>p.margin),r.target_margin], vs=[...pts.map(p=>p.ev_sales),r.target_ev_sales||0,r.implied_ev_sales||0];
  const mn=Math.min(0,...ms),mx=Math.max(...ms),vmx=Math.max(...vs)*1.12||1;
  const W=460,H=230,L=40,R=14,T=10,B=196, X=v=>L+(v-mn)/((mx-mn)||1)*(W-L-R), Y=v=>B-v/vmx*(B-T);
  let s='';
  const ys=niceStep(vmx/4); for(let v=0;v<=vmx;v+=ys)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${L-6}" y="${Y(v)+3}" text-anchor="end">${nf(v,v<10?1:0)}×</text>`;
  const xs=niceStep((mx-mn)/4); for(let t=Math.ceil(mn/xs)*xs;t<=mx+1e-9;t+=xs)s+=`<text x="${X(t)}" y="${B+14}" text-anchor="middle">${Math.round(t*100)}%</text>`;
  s+=`<text x="${W-R}" y="${H-2}" text-anchor="end">operating margin (TTM)</text>`;
  const c=m.coef||[], hasG=(m.terms||[]).includes('growth');
  const line=v=>(c[0]||0)+(c[1]||0)*v+(hasG?(c[2]||0)*(r.target_growth||0):0), clipY=v=>Math.max(T,Math.min(B,Y(v)));
  if(c.length)s+=`<line x1="${X(mn)}" y1="${clipY(line(mn))}" x2="${X(mx)}" y2="${clipY(line(mx))}" stroke="var(--accent)" stroke-dasharray="5 4" stroke-width="1.5"/>`;
  const labs=[];
  pts.forEach(p=>{const e=X(p.margin)>W-70;s+=`<circle cx="${X(p.margin)}" cy="${Y(p.ev_sales)}" r="4" fill="var(--muted)"><title>${esc(p.symbol)} · margin ${(p.margin*100).toFixed(1)}% · EV/Sales ${p.ev_sales.toFixed(2)}×</title></circle>`;labs.push({x:X(p.margin)+(e?-6:6),y:Y(p.ev_sales)+4,px:X(p.margin),py:Y(p.ev_sales),a:e?'end':'start',t:p.symbol});});
  if(r.implied_ev_sales!=null&&r.target_ev_sales!=null)s+=`<line x1="${X(r.target_margin)}" x2="${X(r.target_margin)}" y1="${Y(r.implied_ev_sales)}" y2="${Y(r.target_ev_sales)}" stroke="var(--ink)" stroke-dasharray="2 2"/><circle cx="${X(r.target_margin)}" cy="${Y(r.implied_ev_sales)}" r="5" fill="none" stroke="var(--accent)" stroke-width="2"/>`;
  if(r.target_ev_sales!=null){const e=X(r.target_margin)>W-70;s+=`<circle cx="${X(r.target_margin)}" cy="${Y(r.target_ev_sales)}" r="6.5" fill="var(--accent)"/>`;labs.unshift({x:X(r.target_margin)+(e?-10:10),y:Y(r.target_ev_sales)+4,a:e?'end':'start',t:x.ticker,c:'strong'});}
  s+=placeLabels(labs);
  const gap=r.implied_ev_sales&&r.target_ev_sales?(r.target_ev_sales/r.implied_ev_sales-1)*100:null;
  return svgChart(W,H,`กราฟกระจาย EV/Sales กับ operating margin ของ peer ${pts.length} ตัว เทียบ ${x.ticker}`,s)
    +`<div style="font-size:var(--t-sm)">กลุ่มให้ EV/Sales <b class="num">${nf(r.implied_ev_sales,2)}×</b> ที่ margin <span class="num">${nf(r.target_margin*100,1)}%</span>${hasG?` และ growth <span class="num">${nf((r.target_growth||0)*100,1)}%</span>`:''} → ราคาเทียบกลุ่ม <b class="num">${usd(r.value_regression)}</b> · ${esc(x.ticker)} ซื้อขายที่ <b class="num">${nf(r.target_ev_sales,2)}×</b>${gap!=null?` <span class="${upcls(-gap)}">(${gap>=0?'แพงกว่า':'ถูกกว่า'}กลุ่ม ${nf(Math.abs(gap),0)}%)</span>`:''}</div>
    <p class="note" style="margin:0">จุดทึบ = ${esc(x.ticker)} ตอนนี้ · วงกลม = ระดับที่เส้นกลุ่มให้ · เส้นประ = ถดถอยของ peer${hasG?' ที่ growth ของ '+esc(x.ticker):''}</p>`;
}
function relativePanel(x){
  const r=x.relative; if(!r)return '';
  const pct=v=>v==null?'–':`${(v*100).toFixed(1)}%`;
  const exc=(r.excluded||[]).map(e=>`${esc(e.symbol)}: ${esc(e.reason)}`).join(' · ');
  const notes=(r.notes||[]).filter(Boolean).map(esc).join(' · ');
  const m=r.model, ind=relativeIndustry(x), d=r.industry;
  const headVal=m&&r.value_regression!=null?usd(r.value_regression):(d&&d.value_avg!=null?usd(d.value_avg)+' (อุตสาหกรรม)':'–');
  const foot=`${exc?'ตัดออก: '+exc+' · ':''}${notes?notes+' · ':''}cross-check เท่านั้น ไม่เปลี่ยน FV และคะแนน P`;
  if(!m) return `<div class="box"><h3>Relative (peers) <span class="note">${headVal}</span></h3>${ind}<p class="note" style="margin:0">peer regression: ไม่ได้คำนวณ · ${foot}</p></div>`;
  const px=x.price, vr=r.value_regression, vm=r.value_median;
  const vs=v=>v&&px?` <span class="note">(${v>=px?'+':''}${Math.round((v-px)/px*100)}% vs ราคา)</span>`:'';
  const eq=`EV/Sales = ${m.coef[0]} ${m.coef[1]>=0?'+':'−'} ${Math.abs(m.coef[1])}×margin${m.terms.includes('growth')?` ${m.coef[2]>=0?'+':'−'} ${Math.abs(m.coef[2])}×growth`:''}`;
  const weak=m.weak?` ${chip('fit อ่อน','--warn','R² ต่ำ หรือ peer น้อย — อ่านอย่างระวัง')}`:'';
  const rowsP=(m.peers||[]).map(p=>`<tr title="${esc((p.source==='fmp'?'จากรายชื่อ FMP · ':'จากรายชื่อ Damodaran · ')+(p.sic?'SIC '+p.sic[0]+' '+(p.sic[1]||''):'SIC ไม่ทราบ')+(p.addback?' · บวกคืนรายการพิเศษ '+(p.addback*100).toFixed(1)+'% ของรายได้':''))}"><td class="l">${esc(p.symbol)}${p.addback?'*':''}${p.source==='fmp'?'<sup>f</sup>':''}</td><td class="n">${p.ev_sales.toFixed(1)}×</td><td class="n">${pct(p.margin)}</td><td class="n">${pct(p.growth)}</td></tr>`).join('');
  const tgt=r.target_ev_sales!=null?`<tr class="tot"><td class="l">${esc(x.ticker)}</td><td class="n">${r.target_ev_sales.toFixed(1)}× <span class="note">→ ${r.implied_ev_sales}×</span></td><td class="n">${pct(r.target_margin)}</td><td class="n">${pct(r.target_growth)}</td></tr>`:'';
  const flags=(r.flags||[]).concat(r.target_excluded?[r.target_excluded]:[]).concat(m.industry_note?[m.industry_note]:[]).map(f=>`<div class="note warnc">${esc(f)}</div>`).join('');
  return `<div class="box"><h3>Relative · EV/Sales เทียบ margin ของกลุ่ม <span class="note">n=${m.n} · R² ${m.r2??'–'}</span>${weak}</h3>
    ${relativeScatter(x)}
    <div style="font-size:var(--t-sm)">regression <b class="num">${vr!=null?usd(vr):'–'}</b>${vs(vr)} · peer median ${m.peer_median_ev_sales}× → <b class="num">${vm!=null?usd(vm):'–'}</b>${vs(vm)}</div>
    ${ind}${flags}
    <details><summary class="note" style="cursor:pointer">สมการและ peer ทั้ง ${m.n} ตัว</summary>
      <div class="note" title="${esc('R² = ส่วนที่ margin/growth อธิบาย EV/Sales ของ peer ได้ · LOO = ทำนายทีละตัวจากตัวที่เหลือ แล้ววัดความคลาดเคลื่อน')}">${esc(eq)} · R² ${m.r2??'–'} · n ${m.n} · LOO คลาด ${m.loo_median_err!=null?Math.round(m.loo_median_err*100)+'%':'–'}</div>
      <div class="tw"><table style="margin-top:4px"><thead><tr><th class="l">Ticker</th><th class="n">EV/Sales</th><th class="n">margin</th><th class="n">โต FY</th></tr></thead><tbody>${tgt}${rowsP}</tbody></table></div>
      <div class="note">peer fit ${esc(m.as_of)}${r.peer_list_as_of?' · รายชื่อ FMP '+esc(r.peer_list_as_of):''} · <sup>f</sup> = เติมจาก FMP</div></details>
    <p class="note" style="margin:0">${foot}</p></div>`;
}
