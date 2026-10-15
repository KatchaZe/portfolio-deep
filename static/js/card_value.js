function methodShort(m){return (m||'').replace('Terminal-Anchored Reverse DCF','RevDCF').replace('Young-Company DCF (failure-adjusted)','YoungDCF').replace('Future Value Projection','FVP').replace('Justified PEG','PEG')}
function consensusBadge(x){
  // forward-EPS blend: show source count + min–max range (Phase 2 multi-source consensus)
  const n=x.forward_eps_n||0; if(!n||x.forward_eps==null)return '';
  const src=x.forward_eps_sources?Object.keys(x.forward_eps_sources).join(', '):'';
  const rng=(x.forward_eps_low!=null&&x.forward_eps_high!=null&&n>1)
    ? ` range $${x.forward_eps_low}–$${x.forward_eps_high} (${x.forward_eps_spread_pct}% spread)` : '';
  const tip=`fwd EPS $${x.forward_eps} · ${n} source${n>1?'s':''}: ${src}${rng}`;
  const color=n>=2?(x.forward_eps_spread_pct!=null&&x.forward_eps_spread_pct>25?'#dc2626':'#16a34a'):'#94a3b8';
  return ` <span title="${esc(tip)}" style="cursor:help;font-size:calc(9px*var(--fs));font-weight:700;color:${color}">EPS×${n}</span>`;
}
function anchorCell(x){
  // show EVERY computed method (FVP + PEG); ★ marks the anchor used for Upside
  const m=methodShort(x.anchor_method);
  const alts=[];
  if(x.fv_fvp!=null)alts.push(['FVP',x.fv_fvp]);
  if(x.fv_peg!=null)alts.push(['PEG',x.fv_peg]);
  // D4: when the two methods are far apart, [low,high] is a method conflict — not a
  // confidence band. Say so on the card instead of letting it read as precision.
  const dx=(x.key_metrics||{}).fv_disagreement_x;
  const clash=dx?` <span style="color:#b45309;font-weight:700;cursor:help" title="${esc('สองวิธีให้คำตอบห่างกัน '+dx+' เท่า — ช่วง low–high นี้คือ "วิธีไม่ตรงกัน" ไม่ใช่ "ช่วงความมั่นใจ" ให้ยึดวิธีหลัก (★) และอ่าน flag ประกอบ')}">&#9888; ต่างกัน ${dx}&times;</span>`:'';
  const altLine=alts.length
    ? `<div class="muted" style="font-size:calc(9px*var(--fs));margin-top:1px" title="Fair Value จากทุกวิธีที่คำนวณได้ — ★ = วิธีหลักที่เลือกใช้คิด Upside (ตาม methodology verdict)">`
      + alts.map(a=>`${a[0]===m?'&#9733;':''}${a[0]} $${Number(a[1]).toFixed(2)}`).join(' · ')+clash+`</div>`
    : '';
  if(x.anchor_value!=null)return `${f(x.anchor_value)}${consensusBadge(x)}<div class="muted" style="font-size:calc(9px*var(--fs))">${m}</div>${altLine}`;
  if(x.rev_implied_cagr!=null)return `<span style="font-size:calc(11px*var(--fs));color:#7c3aed;font-weight:700">RevDCF</span>${consensusBadge(x)}<div style="font-size:calc(9px*var(--fs));color:#7c3aed">implies ${x.rev_implied_cagr}% CAGR</div>${altLine}`;
  return '<span class="muted">&mdash;</span>'+altLine;
}
function upsideCell(x){
  if(x.upside_pct!=null){
    const net=(x.net_upside_pct!=null)?`<div class="muted" style="font-size:calc(9px*var(--fs))" title="after ~trading cost + capital-gains tax (Damodaran S6)">net ${(x.net_upside_pct>=0?'+':'')+x.net_upside_pct}%</div>`:'';
    return pct(x.upside_pct)+net;
  }
  // REV-16 (S20): a pre-profit name whose forward DCF did NOT earn the right to
  // anchor still says WHY — "no upside" and "we cannot value this" are different
  // answers and the card used to show the same dash for both.
  const yb=(x.young_dcf&&!x.young_dcf.promote)?x.young_dcf.blocked_reason:null;
  if(yb&&x.upside_pct==null){
    return `<span title="${esc('Young-Company DCF (S20) ran but is informational only: '+yb)}" style="font-size:calc(10px*var(--fs));color:#b45309;cursor:help">ตีมูลค่าไม่ได้ &#9432;</span>`
      +`<div class="muted" style="font-size:calc(9px*var(--fs))">${x.rev_verdict?esc((x.rev_verdict||'').split('—')[0].trim()):''}</div>`;
  }
  if(x.rev_verdict){
    // pre-profit: show ACTUAL revenue CAGR next to the market-implied one so the
    // gap (performance vs expectation) is visible at a glance
    const act=(x.rev_actual_1y!=null)
      ? `<div style="font-size:calc(9px*var(--fs));color:#334155" title="Revenue growth ที่ทำได้จริงล่าสุด (1y) เทียบกับ implied CAGR ที่ตลาด price-in">actual ${(x.rev_actual_1y>=0?'+':'')+x.rev_actual_1y}%/y${x.rev_implied_cagr!=null?` vs ${x.rev_implied_cagr}%`:''}</div>`:'';
    return `<span title="${esc(x.rev_verdict)}" style="font-size:calc(10px*var(--fs));color:#64748b">${(x.rev_verdict||'').split('—')[0].trim()||'priced-in'}</span>${act}`;
  }
  return '<span class="muted">&mdash;</span>';
}
// ---- REV-16 / Damodaran S20: pre-profit forward valuation panel ----
// หุ้น pre-profit เคยมีแค่ reverse DCF ซึ่งเป็น "ราคาที่ตลาดคิด" (pricing) ไม่ใช่ "มูลค่า"
// panel นี้แสดง valuation จริง: going-concern → หัก failure risk → band จาก Monte Carlo
// โดย failure risk เป็น "ความน่าจะเป็น" แยกออกมา ไม่ใช่การบวกเข้า discount rate (S20)
function youngPanel(x){
  const y=x.young_dcf; if(!y)return '';
  const mc=y.monte_carlo||{}, px=x.price;
  const surv=Math.round((y.p_survival||0)*100);
  const inp=y.inputs||{};
  const tipInputs=['Inputs (derive จากงบที่ยื่นจริงทั้งหมด):',
    `• growth ปีแรก ${inp.g_high_pct}% — ${inp.g_high_src||''}`,
    `• margin ${inp.current_margin_pct}% → ${inp.target_margin_pct}% — ${inp.target_margin_src||''}`,
    `• sales-to-capital ${inp.sales_to_capital} — ${inp.sales_to_capital_src||''}`,
    `• survival ${surv}% — ${inp.p_survival_src||''}`,
    `• SBC dilution ${inp.annual_dilution_pct}%/ปี · WACC ${inp.wacc_pct}% · ROIC ปลายทาง ${inp.roic_stable_pct}%`,
    `• terminal = ${y.terminal_share_of_value_pct}% ของมูลค่า`].join('\n');

  // แถบ band: distress (พื้น) → p10 → p50 → p90 → going concern (เพดาน)
  let band='';
  if(mc.p10!=null&&mc.p90!=null&&mc.p90>mc.p10){
    const lo=Math.min(mc.p10,y.distress_per_share||0,px||mc.p10);
    const hi=Math.max(mc.p90,y.going_concern_per_share||0,px||mc.p90);
    const at=v=>Math.max(0,Math.min(100,((v-lo)/(hi-lo))*100));
    const l=at(mc.p10), r=at(mc.p90), m=at(mc.p50);
    const mark=(v,c,lbl)=>v==null?'':`<div title="${esc(lbl)}" style="position:absolute;left:${at(v).toFixed(1)}%;top:-2px;width:2px;height:14px;background:${c}"></div>`;
    band=`<div style="position:relative;height:10px;margin:5px 0 3px" title="${esc('ช่วงมูลค่าจาก Monte Carlo 1000 รอบ (สุ่ม target margin, sales-to-capital, growth, survival)\np10 $'+mc.p10+' · p50 $'+mc.p50+' · p90 $'+mc.p90+'\nพื้นล่างสุด = distress $'+y.distress_per_share+' (เงินสดสุทธิ ถ้ากิจการมีค่าศูนย์)')}">
      <div style="position:absolute;left:0;right:0;top:4px;height:2px;background:#e2e8f0;border-radius:1px"></div>
      <div style="position:absolute;left:${l.toFixed(1)}%;width:${Math.max(1,r-l).toFixed(1)}%;top:2px;height:6px;background:#c7d2fe;border-radius:3px"></div>
      <div style="position:absolute;left:${m.toFixed(1)}%;top:0;width:3px;height:10px;background:#4338ca;border-radius:2px"></div>
      ${mark(px,'#0f172a','ราคาปัจจุบัน $'+px)}
      ${mark(y.distress_per_share,'#94a3b8','distress floor $'+y.distress_per_share)}</div>
    <div style="display:flex;justify-content:space-between;font-size:calc(9px*var(--fs));color:var(--grey)">
      <span>p10 $${mc.p10}</span><span style="color:#4338ca;font-weight:700">p50 $${mc.p50}</span><span>p90 $${mc.p90}</span></div>`;
  }

  // โซ่ S20: going concern × survival + distress × (1-survival)
  const chain=`<div style="font-size:calc(10px*var(--fs));margin-top:4px" title="${esc('Damodaran S20: failure risk ต้องเป็นความน่าจะเป็นแยกต่างหาก ห้ามยัดเข้า discount rate (เขาเรียกว่า blunt instrument)\nvalue = p_survival × going-concern + (1−p_survival) × distress')}">
    <span class="muted">ถ้ารอด</span> <b>$${y.going_concern_per_share}</b>
    <span class="muted">&times; ${surv}%</span>
    <span class="muted">+ ถ้าไม่รอด</span> <b>$${y.distress_per_share}</b>
    <span class="muted">&times; ${100-surv}% =</span> <b style="color:#4338ca">$${y.failure_adjusted_per_share}</b></div>`;

  const head=`<div style="display:flex;align-items:center;gap:6px;margin:9px 0 1px">
    <span style="font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase">Pre-profit forward value (S20)</span>
    <span title="${esc(tipInputs)}" style="cursor:help;font-size:calc(9px*var(--fs));font-weight:700;background:#eef2ff;color:#3730a3;border-radius:4px;padding:1px 6px">survival ${surv}% &#9432;</span>
    ${y.promote?'':`<span style="font-size:calc(9px*var(--fs));font-weight:700;background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px" title="${esc('ยังไม่ใช้เป็น Fair Value หลัก: '+(y.blocked_reason||''))}">ข้อมูลเสริม &#9432;</span>`}</div>`;

  const why=y.promote?''
    : `<div class="muted" style="font-size:calc(9px*var(--fs));margin-top:2px;color:#92400e">${esc(y.blocked_reason||'')}</div>`;
  return head+band+chain+why+pricedInLine(x);
}
// ---- REV-21: valuation (S20, ข้างบน) เทียบกับ pricing (reverse DCF) ----
// เดิมแถบ implied-vs-actual จะหายไปทันทีที่ young DCF ได้เป็น anchor เพราะ valueGauge
// สลับไปใช้ gauge ปกติ — แต่สองอย่างนี้ตอบคนละคำถามและควรอยู่ด้วยกัน:
//   young DCF = "มันควรมีค่าเท่าไหร่"  ·  reverse DCF = "ตลาดคิดราคาไว้ที่การโตเท่าไหร่"
// ตรวจที่มาแล้ว (2026-08-08, P3-1): actual = revenue_annuals[0]/revenue_annuals[1]-1
// คือ growth ปีงบต่อปีงบ ตรงกับตัวเลขที่บริษัทประกาศใน earnings call
// (ก่อนหน้านี้บันทึกไว้ว่า "มาจาก revenue_annuals ตรง ๆ" ซึ่งไม่จริง — ตัวตั้งเป็น TTM
//  ตัวหารเป็นปีงบก่อนหน้าอีกปี ทำให้ MSFT ขึ้น +29.8% ทั้งที่ประกาศจริง +15%)
// ส่วน implied ใช้ค่าจริงทั้งหมดยกเว้น terminal margin ซึ่งหุ้นขาดทุนวัดไม่ได้ →
// เดา ±5pp ขยับคำตอบ ~7pp จึงต้องแสดงคู่กับ band เสมอ ห้ามโชว์ตัวเลขเดี่ยว
function pricedInLine(x){
  const imp=x.rev_implied_cagr;
  // FIX (2026-08-16): when the solver cannot reach EV at ANY growth rate the engine
  // reports WHY (REV-5: "a price the solver cannot reach is reported, not silently
  // Unknown") — but this line used to `return ''` and drop that verdict, so the
  // reader got a blank space on exactly the names that need the most explaining.
  // Show the verdict instead of nothing.
  if(imp==null){
    const vd=x.rev_verdict; if(!vd) return '';
    return `<div style="margin-top:6px;padding-top:5px;border-top:0.5px dashed #e2e8f0">
    <span style="font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase" title="Reverse DCF = เกม pricing (ตลาดคาดอะไร) ไม่ใช่ valuation">ราคาปัจจุบัน price-in (10y)</span>
    <div style="font-size:calc(10px*var(--fs));margin-top:1px;color:#b45309">หา CAGR ที่รองรับราคานี้ไม่ได้ — ${esc(vd)}</div></div>`;
  }
  const act=x.rev_actual_1y, s=x.rev_sensitivity||{};
  const lo=(s.bull!=null&&s.bear!=null)?Math.min(s.bull,s.bear):null;
  const hi=(s.bull!=null&&s.bear!=null)?Math.max(s.bull,s.bear):null;
  const anch=x.terminal_margin_anchored;
  const bandTxt=(lo!=null&&hi!=null&&hi>lo)
    ? ` <span class="muted" style="font-size:calc(9px*var(--fs))">(${lo}–${hi}% ถ้า margin ±5pp)</span>`:'';
  const warn=anch?'' : ` <span title="${esc('terminal margin ของหุ้นขาดทุนวัดจากงบไม่ได้ จึงเป็นสมมติฐาน — implied CAGR ตัวเดียวจึงเชื่อไม่ได้ ต้องอ่านคู่กับช่วง ±5pp\n'+(x.terminal_margin_label||''))}" style="cursor:help;font-size:calc(9px*var(--fs));color:#b45309">margin สมมติ &#9432;</span>`;
  const gap=(act!=null&&imp!=null)
    ? `<div class="muted" style="font-size:calc(9px*var(--fs));margin-top:1px">ทำได้จริงล่าสุด <b>${act>=0?'+':''}${act}%</b>/ปี &middot; ${imp<=act?'ตลาดคิดต่ำกว่าที่ทำได้':'ตลาดคิดสูงกว่าที่ทำได้'}</div>`:'';
  return `<div style="margin-top:6px;padding-top:5px;border-top:0.5px dashed #e2e8f0">
    <span style="font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase" title="Reverse DCF = เกม pricing (ตลาดคาดอะไร) ไม่ใช่ valuation — ใช้เป็นตัวเทียบกับมูลค่าข้างบน">ราคาปัจจุบัน price-in (10y)</span>
    <div style="font-size:calc(10px*var(--fs));margin-top:1px">โต <b>${imp}%</b>/ปี${bandTxt} ${warn}</div>${gap}</div>`;
}
// ---- Tier2: Value-first gauge — มูลค่าต้องมาก่อนราคาเสมอ (S5/S12-13) ----
function valueGauge(x){
  if(x.upside_pct!=null&&x.anchor_value!=null){
    const ratio=1/(1+x.upside_pct/100);                     // price / FV
    const pos=Math.max(2,Math.min(98,(ratio-0.5)*100));     // สเกล 0.5x → 1.5x ของ FV
    const net=x.net_upside_pct, meth=methodShort(x.anchor_method);
    return `<div style="margin:9px 0 2px;font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase">Price vs Value &middot; FV ${f(x.anchor_value)} (${meth})</div>
      <div style="position:relative;height:12px;border-radius:6px;overflow:hidden;display:flex" title="เขียว = ราคาต่ำกว่า FV เกิน 15% (โซน margin of safety) · เทา = fair ±15% · แดง = แพงกว่า FV เกิน 15% — ขีดดำ = ราคาปัจจุบัน">
        <div style="flex:35;background:#bbf7d0"></div><div style="flex:30;background:#e2e8f0"></div><div style="flex:35;background:#fecaca"></div>
        <div style="position:absolute;left:${pos.toFixed(1)}%;top:0;width:3px;height:12px;background:#0f172a;border-radius:2px"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:calc(11px*var(--fs));margin-top:2px">
        <b class="${x.upside_pct>=0?'up':'down'}">upside ${x.upside_pct>=0?'+':''}${x.upside_pct}%${net!=null?` &rarr; net <span style="font-size:calc(12.5px*var(--fs))">${net>=0?'+':''}${net}%</span>`:''}</b>
        <span class="muted" style="font-size:calc(9px*var(--fs))" title="net = หลังค่าเทรด ~0.2% + ภาษี capital gains ~15% — ตัดสินใจด้วย net เสมอ (Damodaran S6)">net หลัง cost/tax (S6)</span></div>`;
  }
  if(x.rev_implied_cagr!=null){
    const imp=x.rev_implied_cagr, act=x.rev_actual_1y, mx=Math.max(imp||0,act||0,1);
    const bar=(v,c)=>`<div style="flex:1;height:9px;background:#f1f5f9;border-radius:5px"><div style="height:9px;width:${Math.max(3,Math.min(100,v/mx*100)).toFixed(0)}%;background:${c};border-radius:5px"></div></div>`;
    const verd=(x.rev_verdict||'').split('—')[0].trim();
    const vc={Plausible:['#dcfce7','#166534'],Ambitious:['#ffedd5','#9a3412'],Aggressive:['#fee2e2','#991b1b'],Exceptional:['#fee2e2','#991b1b']}[verd]||['#e2e8f0','#475569'];
    return `<div style="margin:9px 0 2px;font-size:calc(9px*var(--fs));color:var(--grey);letter-spacing:.03em;text-transform:uppercase" title="Reverse DCF: ราคาปัจจุบัน price-in การเติบโตรายได้กี่ %/ปี (10 ปี) เทียบที่ทำได้จริงล่าสุด">Market prices-in vs ทำได้จริง (10y CAGR)</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:calc(10px*var(--fs))"><span style="width:76px;flex:none">implied <b>${imp}%</b></span>${bar(imp,'#fca5a5')}</div>
      ${act!=null?`<div style="display:flex;align-items:center;gap:6px;font-size:calc(10px*var(--fs));margin-top:2px"><span style="width:76px;flex:none">actual <b>${act}%</b>/y</span>${bar(act,'#86efac')}</div>`:''}
      <div style="margin-top:3px"><span style="display:inline-block;font-size:calc(9px*var(--fs));font-weight:700;background:${vc[0]};color:${vc[1]};border-radius:4px;padding:1px 6px" title="${esc(x.rev_verdict||'')}">${esc(verd||'priced-in')}</span> <span class="muted" style="font-size:calc(9px*var(--fs))">ช่องว่างระหว่างแท่ง = ศรัทธาที่ตลาดจ่ายล่วงหน้า</span></div>`;
  }
  return '';
}
