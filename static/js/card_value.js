// Value blocks on a holding card: anchor fair value, upside, the price-vs-value gauge,
// the pre-profit (S20) panel and the reverse-DCF "priced-in" line.

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
  return parts.join(' · ');
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
  const color=n>=2?(x.forward_eps_spread_pct!=null&&x.forward_eps_spread_pct>25?'#dc2626':'#16a34a'):'#94a3b8';
  return ` <span title="${esc(tip)}" style="cursor:help;font-size:calc(9px*var(--fs));font-weight:700;color:${color}">EPS×${n}</span>`;
}
function anchorCell(x){
  const m=methodShort(x.anchor_method);
  const altLine=fairValueAlternatives(x,m);
  if(x.anchor_value!=null)return `${fixed(x.anchor_value)}${consensusBadge(x)}<div class="muted" style="font-size:calc(9px*var(--fs))">${m}</div>${altLine}`;
  if(x.rev_implied_cagr!=null)return `<span style="font-size:calc(11px*var(--fs));color:#7c3aed;font-weight:700">RevDCF</span>${consensusBadge(x)}<div style="font-size:calc(9px*var(--fs));color:#7c3aed">implies ${x.rev_implied_cagr}% CAGR</div>${altLine}`;
  return MUTED_DASH+altLine;
}
// Every computed method (DCF / ExRet, FVP, PEG); ★ marks the anchor behind Upside. When the two are
// far apart the low–high span is a method conflict, not a confidence band, so say so.
function fairValueAlternatives(x,anchor){
  const alts=[];
  if(x.fv_dcf!=null){const bad=(x.dcf||{}).discarded;
    alts.push([bad?'DCF✗':'DCF',x.fv_dcf,bad?'ตัดทิ้ง: '+bad+' — น่าจะมาจาก input ที่ผิดปกติ (เช่น EBIT ที่มีรายการพิเศษ) ไม่ได้ใช้คิด upside และไม่อยู่ในช่วง low–high · '+dcfTip(x):dcfTip(x)]);}
  if(x.fv_excess_return!=null)alts.push(['ExRet',x.fv_excess_return,'Excess-return model (ธนาคาร/ประกัน): book equity + PV ของ (ROE − Ke) × book equity']);
  if(x.fv_fvp!=null)alts.push(['FVP',x.fv_fvp]);
  if(x.fv_peg!=null)alts.push(['PEG',x.fv_peg]);
  if(!alts.length)return '';
  const dx=(x.key_metrics||{}).fv_disagreement_x;
  const clash=dx?` <span style="color:#b45309;font-weight:700;cursor:help" title="${esc('สองวิธีให้คำตอบห่างกัน '+dx+' เท่า — ช่วง low–high นี้คือ "วิธีไม่ตรงกัน" ไม่ใช่ "ช่วงความมั่นใจ" ให้ยึดวิธีหลัก (★) และอ่าน flag ประกอบ')}">&#9888; ต่างกัน ${dx}&times;</span>`:'';
  return `<div class="muted" style="font-size:calc(9px*var(--fs));margin-top:1px" title="Fair Value จากทุกวิธีที่คำนวณได้ — ★ = วิธีหลักที่เลือกใช้คิด Upside (ตาม methodology verdict)">`
    + alts.map(a=>{const t=`${a[0]===anchor?'&#9733;':''}${a[0]} $${Number(a[1]).toFixed(2)}`;
        return a[2]?`<span title="${esc(a[2])}" style="cursor:help">${t}</span>`:t;}).join(' · ')+clash+`</div>`;
}
// Margin of safety (V − P)/V, the one definition the score, gauge, advice and Risk Desk
// share; upside (V − P)/P is the return if price converges, shown underneath.
function mosOf(x){return x.mos_pct!=null?x.mos_pct:(x.upside_pct!=null&&x.upside_pct>-100?Math.round(x.upside_pct/(100+x.upside_pct)*1000)/10:null);}
function upsideCell(x){
  if(x.upside_pct!=null){
    const net=(x.net_upside_pct!=null)?` · net ${signed(x.net_upside_pct)}%`:'';
    return `<span title="Margin of safety = (Fair value − ราคา) / Fair value · เขียว ≥ +15% · แดง ≤ −15%">${upDownPct(mosOf(x))}</span>`
      +`<div class="muted" style="font-size:calc(9px*var(--fs))" title="upside = (FV − ราคา)/ราคา = ผลตอบแทนถ้าราคาวิ่งถึง FV · net = หลังค่าเทรด + ภาษี (Damodaran S6)">upside ${signed(x.upside_pct)}%${net}</div>`;
  }
  // "no upside" and "we cannot value this" are different answers: a pre-profit name
  // whose S20 DCF was not promoted to the anchor says why
  const yb=(x.young_dcf&&!x.young_dcf.promote)?x.young_dcf.blocked_reason:null;
  if(yb){
    return `<span title="${esc('Young-Company DCF (S20) ran but is informational only: '+yb)}" style="font-size:calc(10px*var(--fs));color:#b45309;cursor:help">ตีมูลค่าไม่ได้ &#9432;</span>`
      +`<div class="muted" style="font-size:calc(9px*var(--fs))">${x.rev_verdict?esc(verdictHead(x.rev_verdict)):''}</div>`;
  }
  if(x.rev_verdict){
    // actual revenue growth next to the market-implied CAGR: performance vs expectation
    const act=(x.rev_actual_1y!=null)
      ? `<div style="font-size:calc(9px*var(--fs));color:#334155" title="Revenue growth ที่ทำได้จริงล่าสุด (1y) เทียบกับ implied CAGR ที่ตลาด price-in">actual ${signed(x.rev_actual_1y)}%/y${x.rev_implied_cagr!=null?` vs ${x.rev_implied_cagr}%`:''}</div>`:'';
    return `<span title="${esc(x.rev_verdict)}" style="font-size:calc(10px*var(--fs));color:#64748b">${verdictHead(x.rev_verdict)||'priced-in'}</span>${act}`;
  }
  return MUTED_DASH;
}

// S20 pre-profit valuation: going concern, minus failure risk, banded by Monte Carlo.
// Failure risk is its own probability, never folded into the discount rate.
function youngPanel(x){
  const y=x.young_dcf; if(!y)return '';
  const surv=Math.round((y.p_survival||0)*100);
  return youngHeader(y,surv)+youngBand(y,y.monte_carlo||{},x.price)+youngChain(y,surv)+youngSurvivalRange(y)
    +((!y.promote||y.uncertain)?`<div class="muted" style="font-size:calc(9px*var(--fs));margin-top:2px;color:#92400e">${y.promote?'ใช้เป็น FV หลักแต่ไม่แน่นอน (P ไม่เกินกลาง): ':''}${esc(y.blocked_reason||'')}</div>`:'')
    +pricedInLine(x);
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
  return `<div style="display:flex;align-items:center;gap:6px;margin:9px 0 1px">
    <span style="${CAPS_LABEL}">Pre-profit forward value (S20)</span>
    <span title="${esc(tipInputs)}" style="cursor:help;font-size:calc(9px*var(--fs));font-weight:700;background:#eef2ff;color:#3730a3;border-radius:4px;padding:1px 6px">survival ${surv}% &#9432;</span>
    ${y.promote?(y.uncertain?`<span style="font-size:calc(9px*var(--fs));font-weight:700;background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px" title="${esc('Fair Value หลักแต่ช่วงกว้าง/ติดลบ — คะแนน Price ไม่เกินกลาง: '+(y.blocked_reason||''))}">ไม่แน่นอน &#9432;</span>`:''):`<span style="font-size:calc(9px*var(--fs));font-weight:700;background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px" title="${esc('ยังไม่ใช้เป็น Fair Value หลัก: '+(y.blocked_reason||''))}">ข้อมูลเสริม &#9432;</span>`}</div>`;
}
// distress (floor) -> p10 -> p50 -> p90 -> going concern (ceiling), with the price marked
function youngBand(y,mc,px){
  if(!(mc.p10!=null&&mc.p90!=null&&mc.p90>mc.p10))return '';
  const lo=Math.min(mc.p10,y.distress_per_share||0,px||mc.p10);
  const hi=Math.max(mc.p90,y.going_concern_per_share||0,px||mc.p90);
  const at=v=>Math.max(0,Math.min(100,((v-lo)/(hi-lo))*100));
  const l=at(mc.p10), r=at(mc.p90), m=at(mc.p50);
  const mark=(v,c,lbl)=>v==null?'':`<div title="${esc(lbl)}" style="position:absolute;left:${at(v).toFixed(1)}%;top:-2px;width:2px;height:14px;background:${c}"></div>`;
  return `<div style="position:relative;height:10px;margin:5px 0 3px" title="${esc('ช่วงมูลค่าจาก Monte Carlo 1000 รอบ (สุ่ม target margin, sales-to-capital, growth, survival)\np10 $'+mc.p10+' · p50 $'+mc.p50+' · p90 $'+mc.p90+'\nพื้นล่างสุด = distress $'+y.distress_per_share+' (เงินสดสุทธิ ถ้ากิจการมีค่าศูนย์)')}">
      <div style="position:absolute;left:0;right:0;top:4px;height:2px;background:#e2e8f0;border-radius:1px"></div>
      <div style="position:absolute;left:${l.toFixed(1)}%;width:${Math.max(1,r-l).toFixed(1)}%;top:2px;height:6px;background:#c7d2fe;border-radius:3px"></div>
      <div style="position:absolute;left:${m.toFixed(1)}%;top:0;width:3px;height:10px;background:#4338ca;border-radius:2px"></div>
      ${mark(px,'#0f172a','ราคาปัจจุบัน $'+px)}
      ${mark(y.distress_per_share,'#94a3b8','distress floor $'+y.distress_per_share)}</div>
    <div style="display:flex;justify-content:space-between;font-size:calc(9px*var(--fs));color:var(--grey)">
      <span>p10 $${mc.p10}</span><span style="color:#4338ca;font-weight:700">p50 $${mc.p50}</span><span>p90 $${mc.p90}</span></div>`;
}
// p_survival is an assumption (a runway table, not his sector failure statistics), so
// the card shows what the value is if it is 20 points off either way.
const RUNWAY_TABLE='ตาราง runway → survival: ไม่เผาเงิน 90% · ≥5 ปี 85% · ≥3 ปี 75% · ≥2 ปี 65% · ≥1 ปี 50% · <1 ปี 30% · ไม่มีเงินสด 25% · รายได้ ≥$5B +5 จุด';
function youngSurvivalRange(y){
  const p=y.p_survival, gc=y.going_concern_per_share, d=y.distress_per_share||0;
  if(p==null||gc==null)return '';
  const at=q=>{q=Math.max(0,Math.min(1,q));return {q:Math.round(q*100),v:(q*gc+(1-q)*d).toFixed(2)}};
  const lo=at(p-0.2), hi=at(p+0.2);
  return `<div style="font-size:calc(9px*var(--fs));margin-top:2px;color:#92400e" title="${esc('survival เป็นสมมติฐาน ไม่ใช่ข้อมูลที่ยื่น — Damodaran ใช้สถิติการล้มของ sector ปรับด้วย runway; ที่นี่ใช้ runway อย่างเดียว\n'+RUNWAY_TABLE)}">
    &#9888; survival ${Math.round(p*100)}% = สมมติฐาน (จาก cash runway) · ถ้า ${lo.q}% → <b>$${lo.v}</b> · ถ้า ${hi.q}% → <b>$${hi.v}</b> &#9432;</div>`;
}
// value = p_survival × going concern + (1 − p_survival) × distress
function youngChain(y,surv){
  return `<div style="font-size:calc(10px*var(--fs));margin-top:4px" title="${esc('Damodaran S20: failure risk ต้องเป็นความน่าจะเป็นแยกต่างหาก ห้ามยัดเข้า discount rate (เขาเรียกว่า blunt instrument)\nvalue = p_survival × going-concern + (1−p_survival) × distress')}">
    <span class="muted">ถ้ารอด</span> <b>$${y.going_concern_per_share}</b>
    <span class="muted">&times; ${surv}%</span>
    <span class="muted">+ ถ้าไม่รอด</span> <b>$${y.distress_per_share}</b>
    <span class="muted">&times; ${100-surv}% =</span> <b style="color:#4338ca">$${y.failure_adjusted_per_share}</b></div>`;
}

// Pricing (reverse DCF: the growth the price implies) beside the S20 valuation; they
// answer different questions, so the line stays even when the young DCF anchors.
// actual = FY-over-FY revenue growth, as the company reports it. For a loss-maker the
// implied CAGR rests on an assumed terminal margin (±5pp moves it ~7pp), so it is never
// shown without its band. When no growth rate reaches the price, the verdict says so.
function pricedInLine(x){
  const imp=x.rev_implied_cagr;
  if(imp==null){
    const vd=x.rev_verdict; if(!vd) return '';
    return `<div style="margin-top:6px;padding-top:5px;border-top:0.5px dashed #e2e8f0">
    <span style="${CAPS_LABEL}" title="Reverse DCF = เกม pricing (ตลาดคาดอะไร) ไม่ใช่ valuation">ราคาปัจจุบัน price-in (10y)</span>
    <div style="font-size:calc(10px*var(--fs));margin-top:1px;color:#b45309">หา CAGR ที่รองรับราคานี้ไม่ได้ — ${esc(vd)}</div></div>`;
  }
  const act=x.rev_actual_1y, s=x.rev_sensitivity||{};
  const both=s.bull!=null&&s.bear!=null;
  const lo=both?Math.min(s.bull,s.bear):null, hi=both?Math.max(s.bull,s.bear):null;
  const bandTxt=(lo!=null&&hi!=null&&hi>lo)
    ? ` <span class="muted" style="font-size:calc(9px*var(--fs))">(${lo}–${hi}% ถ้า margin ±5pp)</span>`:'';
  const warn=x.terminal_margin_anchored?'' : ` <span title="${esc('terminal margin ของหุ้นขาดทุนวัดจากงบไม่ได้ จึงเป็นสมมติฐาน — implied CAGR ตัวเดียวจึงเชื่อไม่ได้ ต้องอ่านคู่กับช่วง ±5pp\n'+(x.terminal_margin_label||''))}" style="cursor:help;font-size:calc(9px*var(--fs));color:#b45309">margin สมมติ &#9432;</span>`;
  const gap=(act!=null)
    ? `<div class="muted" style="font-size:calc(9px*var(--fs));margin-top:1px">ทำได้จริงล่าสุด <b>${signed(act)}%</b>/ปี &middot; ${imp<=act?'ตลาดคิดต่ำกว่าที่ทำได้':'ตลาดคิดสูงกว่าที่ทำได้'}</div>`:'';
  return `<div style="margin-top:6px;padding-top:5px;border-top:0.5px dashed #e2e8f0">
    <span style="${CAPS_LABEL}" title="Reverse DCF = เกม pricing (ตลาดคาดอะไร) ไม่ใช่ valuation — ใช้เป็นตัวเทียบกับมูลค่าข้างบน">ราคาปัจจุบัน price-in (10y)</span>
    <div style="font-size:calc(10px*var(--fs));margin-top:1px">โต <b>${imp}%</b>/ปี${bandTxt} ${warn}</div>${gap}</div>`;
}

// Value first (S5/S12-13): where the price sits against fair value, or, with no fair
// value, the growth the market prices in against the growth actually delivered.
function valueGauge(x){
  if(x.upside_pct!=null&&x.anchor_value!=null)return priceVsValueGauge(x);
  if(x.rev_implied_cagr!=null)return impliedVsActualBars(x);
  return '';
}
function priceVsValueGauge(x){
  const ratio=1/(1+x.upside_pct/100);                     // price / FV
  const pos=Math.max(2,Math.min(98,(ratio-0.5)*100));     // scale: 0.5x -> 1.5x of FV
  const net=x.net_upside_pct, meth=methodShort(x.anchor_method);
  return `<div style="margin:9px 0 2px;${CAPS_LABEL}">Price vs Value &middot; FV ${fixed(x.anchor_value)} (${meth})</div>
      <div style="position:relative;height:12px;border-radius:6px;overflow:hidden;display:flex" title="เขียว = MoS ≥ +15% (ราคา ≤ 0.85×FV) · เทา = MoS ±15% · แดง = MoS ≤ −15% (ราคา ≥ 1.15×FV) — ขีดดำ = ราคาปัจจุบัน">
        <div style="flex:35;background:#bbf7d0"></div><div style="flex:30;background:#e2e8f0"></div><div style="flex:35;background:#fecaca"></div>
        <div style="position:absolute;left:${pos.toFixed(1)}%;top:0;width:3px;height:12px;background:#0f172a;border-radius:2px"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:calc(11px*var(--fs));margin-top:2px">
        <b class="${x.upside_pct>=0?'up':'down'}">${mosOf(x)!=null?`MoS ${signed(mosOf(x))}% · `:''}upside ${signed(x.upside_pct)}%${net!=null?` &rarr; net <span style="font-size:calc(12.5px*var(--fs))">${signed(net)}%</span>`:''}</b>
        <span class="muted" style="font-size:calc(9px*var(--fs))" title="net = หลังค่าเทรด ~0.2% + ภาษี capital gains ~15% — ตัดสินใจด้วย net เสมอ (Damodaran S6)">net หลัง cost/tax (S6)</span></div>`
      // every method with its hover (DCF: growth path, margin, S2C, TV%, CRP); the gauge
      // replaced the Anchor FV cell that used to carry it, so it had vanished from these cards
      + fairValueAlternatives(x,meth);
}
const VERDICT_COLORS={Plausible:['#dcfce7','#166534'],Ambitious:['#ffedd5','#9a3412'],Aggressive:['#fee2e2','#991b1b'],Exceptional:['#fee2e2','#991b1b']};
function impliedVsActualBars(x){
  const imp=x.rev_implied_cagr, act=x.rev_actual_1y, mx=Math.max(imp||0,act||0,1);
  const bar=(v,c)=>`<div style="flex:1;height:9px;background:#f1f5f9;border-radius:5px"><div style="height:9px;width:${Math.max(3,Math.min(100,v/mx*100)).toFixed(0)}%;background:${c};border-radius:5px"></div></div>`;
  const verd=verdictHead(x.rev_verdict);
  const vc=VERDICT_COLORS[verd]||['#e2e8f0','#475569'];
  return `<div style="margin:9px 0 2px;${CAPS_LABEL}" title="Reverse DCF: ราคาปัจจุบัน price-in การเติบโตรายได้กี่ %/ปี (10 ปี) เทียบที่ทำได้จริงล่าสุด">Market prices-in vs ทำได้จริง (10y CAGR)</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:calc(10px*var(--fs))"><span style="width:76px;flex:none">implied <b>${imp}%</b></span>${bar(imp,'#fca5a5')}</div>
      ${act!=null?`<div style="display:flex;align-items:center;gap:6px;font-size:calc(10px*var(--fs));margin-top:2px"><span style="width:76px;flex:none">actual <b>${act}%</b>/y</span>${bar(act,'#86efac')}</div>`:''}
      <div style="margin-top:3px">${chip(esc(verd||'priced-in'),vc[0],vc[1],esc(x.rev_verdict||''))} <span class="muted" style="font-size:calc(9px*var(--fs))">ช่องว่างระหว่างแท่ง = ศรัทธาที่ตลาดจ่ายล่วงหน้า</span></div>`;
}
