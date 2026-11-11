// The Performance tab: the transaction ledger (pipeline/performance.ledger_view) and what
// it adds up to. The server replays the ledger; this file only draws it and posts entries.

let __ledger=null, __ledgerEdit=null;

async function loadLedger(){
  const el=document.getElementById('ledgerBody');
  try{
    const r=await fetch('/api/ledger'); __ledger=await r.json();
    el.innerHTML=__ledger.active?ledgerHTML(__ledger):ledgerStartHTML();
    if(__ledger.active)txnTypeChanged();
  }catch(e){el.innerHTML='<div class="banner tone t-bear">&#9888; โหลด ledger ไม่ได้ ('+esc(String(e))+')</div>';}
}

function ledgerStartHTML(){
  const t=(window.__last||{}).totals||{};
  return `<section class="pnl s12"><div class="phead"><h2>เริ่ม ledger</h2></div>
    <p>แอปจะยกยอดถือปัจจุบันมาเป็นรายการ <b>OPEN</b>: แต่ละหุ้นที่ต้นทุนเฉลี่ย (นับเป็นเงินต้น) และราคาปิดวันนี้ (จุดเริ่มวัดผลงาน) พร้อมเงินสด
    หลังจากนี้การซื้อ ขาย ฝาก ถอน และปันผล ให้บันทึกเป็นรายการ แล้วแอปจะคำนวณจำนวนหุ้น ต้นทุน และเงินสดเอง (ช่อง "เพิ่ม / แก้หุ้น" กับ "เงินสด" เดิมจะปิดไป)</p>
    <p class="note">มูลค่าพอร์ตตอนนี้ ${money(t.market_value)} · เงินต้นในหน่วยบาทของยอดยกมาจะประเมินจากเรทวันนี้ แก้ภายหลังได้ที่รายการ OPEN</p>
    <button type="button" class="btn primary" onclick="startLedger()"><span class="spin" id="spLs"></span>เริ่ม ledger จากยอดถือวันนี้</button>
    <span id="ledgerMsg" class="note" role="status"></span></section>`;
}
async function startLedger(){
  const sp=document.getElementById('spLs'),msg=document.getElementById('ledgerMsg');
  sp.style.display='inline-block';
  try{
    const r=await fetch('/api/ledger/start',{method:'POST'}); const d=await r.json();
    if(!r.ok){msg.textContent='✗ '+(d.error||r.status);return;}
    afterLedgerWrite(d);
  }catch(e){msg.textContent='✗ '+e}finally{sp.style.display='none'}
}

function afterLedgerWrite(d){
  __ledger=d.ledger; if(d.view){renderPortfolio(d.view);} if(d.quota)renderQuota(d.quota);
  __riskCache=null; lockOldEditors(true);
  document.getElementById('ledgerBody').innerHTML=ledgerHTML(__ledger); txnTypeChanged();
}

// once the ledger runs, holdings and cash come from it: the old editors would fight it
function lockOldEditors(on){
  ['t','sh','ac','cashIn'].forEach(id=>{const e=document.getElementById(id);if(e)e.disabled=on;});
  document.querySelectorAll('#tools .addform:first-child button').forEach(b=>b.disabled=on);
  const msg=document.getElementById('addMsg');
  if(msg&&on)msg.innerHTML='ledger ทำงานอยู่ — ซื้อ / ขาย / ฝาก / ถอน บันทึกที่แท็บ <a href="#" onclick="showTab(7);return false">Performance</a>';
}

const TXN_FIELDS={
  BUY:['ticker','shares','price','fee'], SELL:['ticker','shares','price','fee'],
  DEPOSIT:['amount','thb'], WITHDRAW:['amount','thb'],
  DIVIDEND:['ticker','amount','withheld'], SPLIT:['ticker','ratio'],
  OPEN:['ticker','shares','price','mkt_price','amount','thb'],
};
const TXN_LABEL={ticker:'ticker',shares:'จำนวนหุ้น',price:'ราคา $',fee:'ค่าธรรมเนียม $',amount:'จำนวน $',
  thb:'จำนวนบาท',withheld:'ภาษีที่ถูกหัก $ (ว่าง = 15%)',ratio:'อัตราแตก (4 = 1→4)',mkt_price:'ราคาปิดวันยกมา $'};

function txnFormHTML(){
  const n=new Date(), today=`${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`;   // local, not UTC
  const types=['BUY','SELL','DEPOSIT','WITHDRAW','DIVIDEND','SPLIT'];
  return `<div class="addform" id="txnForm">
    <span class="lbl" id="txnFormLbl">บันทึกรายการ</span>
    <label class="sr" for="txType">ประเภท</label><select id="txType" class="inp" onchange="txnTypeChanged()">${types.map(t=>`<option>${t}</option>`).join('')}<option hidden>OPEN</option></select>
    <label class="sr" for="txDate">วันที่</label><input id="txDate" type="date" class="inp" value="${today}">
    ${Object.keys(TXN_LABEL).map(k=>`<label class="sr" for="tx_${k}">${TXN_LABEL[k]}</label><input id="tx_${k}" ${k==='ticker'?'maxlength="8" style="width:calc(80px*var(--fs))"':'class="n" type="number" step="any" min="0"'} placeholder="${TXN_LABEL[k]}">`).join('')}
    <button type="button" class="btn primary" onclick="saveTxn()"><span class="spin" id="spTx"></span><span id="txBtn">บันทึก</span></button>
    <button type="button" class="btn" id="txCancel" style="display:none" onclick="cancelEdit()">ยกเลิกแก้ไข</button>
    <span id="txMsg" class="note" role="status"></span>
  </div>`;
}
function txnTypeChanged(){
  const ty=document.getElementById('txType'); if(!ty)return;
  const show=TXN_FIELDS[ty.value]||[];
  Object.keys(TXN_LABEL).forEach(k=>{const e=document.getElementById('tx_'+k);if(e)e.style.display=show.includes(k)?'':'none';});
}
async function saveTxn(){
  const msg=document.getElementById('txMsg'),sp=document.getElementById('spTx');
  const body={type:document.getElementById('txType').value,date:document.getElementById('txDate').value};
  if(__ledgerEdit!=null)body.id=__ledgerEdit;
  for(const k of TXN_FIELDS[body.type]||[]){
    const v=document.getElementById('tx_'+k).value.trim();
    if(v!=='')body[k]=k==='ticker'?v.toUpperCase():Number(v);
  }
  const btn=document.querySelector('#txnForm button.primary'); if(btn.disabled)return; btn.disabled=true;
  sp.style.display='inline-block'; msg.textContent=body.ticker&&!(__ledger.positions||[]).some(p=>p.ticker===body.ticker)?'หุ้นใหม่ — กำลังดึงข้อมูล '+body.ticker+'…':'';
  try{
    const r=await fetch('/api/ledger/txn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok){msg.textContent='✗ '+(d.error||(d.detail&&JSON.stringify(d.detail))||r.status);return;}
    __ledgerEdit=null; afterLedgerWrite(d);
    document.getElementById('txMsg').textContent='✓ บันทึก '+body.type+(body.ticker?' '+body.ticker:'');
  }catch(e){msg.textContent='✗ '+e}finally{const s=document.getElementById('spTx');if(s)s.style.display='none';btn.disabled=false;}
}
function editTxn(id){
  const t=(__ledger.txns||[]).find(x=>x.id===id); if(!t)return;
  __ledgerEdit=id;
  document.getElementById('txType').value=t.type; txnTypeChanged();
  document.getElementById('txDate').value=t.date;
  Object.keys(TXN_LABEL).forEach(k=>{const e=document.getElementById('tx_'+k);if(e)e.value=t[k]??'';});
  document.getElementById('txBtn').textContent='บันทึกการแก้ไข #'+id;
  document.getElementById('txCancel').style.display='';
  document.getElementById('txnForm').scrollIntoView({block:'center'});
}
function cancelEdit(){__ledgerEdit=null;document.getElementById('ledgerBody').innerHTML=ledgerHTML(__ledger);txnTypeChanged();}
async function deleteTxn(id){
  if(!confirm('ลบรายการ #'+id+'? (หุ้นและเงินสดจะคำนวณใหม่)'))return;
  const r=await fetch('/api/ledger/delete?id='+id,{method:'POST'}); const d=await r.json();
  if(!r.ok){alert(d.error||r.status);return;}
  afterLedgerWrite(d);
}

function ledgerHTML(L){
  const p=L.performance||{}, th=L.thb, c=L.closed||{};
  const twr=p.status==='ok'?`${signed(p.twr_pct)}% vs SPY ${signed(p.spy_pct)}% (${signed(p.active_pp)}pp)`:(p.note||'—');
  const warn=[];
  if((L.implied_deposits||[]).length)warn.push(`ซื้อเกินเงินสดที่บันทึก ${L.implied_deposits.length} ครั้ง (รวม ${money(L.implied_deposits.reduce((a,x)=>a+x.amount,0))}) — นับเป็นเงินฝากอัตโนมัติ; บันทึก DEPOSIT จริงพร้อมยอดบาทเพื่อให้เงินต้นบาทถูก`);
  if((L.unpriced||[]).length)warn.push('ยังไม่มีราคา: '+L.unpriced.join(', ')+' (คิดที่ต้นทุนไปก่อน — กด Run Daily)');
  if(th&&th.principal_estimated_usd>0)warn.push(`เงินต้น ${money(th.principal_estimated_usd)} ยังไม่มียอดบาท (ประเมินที่เรทวันนี้ ${nf(th.usdthb,2)}) — แก้ที่รายการ OPEN / DEPOSIT`);
  const kpi1=kpisHTML([
    ['มูลค่าพอร์ต (NAV)',money(L.nav),'',`หุ้น ${money(L.invested_value)} · เงินสด ${money(L.cash)}`],
    ['เงินต้นสุทธิ',money(L.principal_usd),'',`= ${pctFixed(L.principal_share_pct)} ของพอร์ต`,'ต้นทุนยอดยกมา + เงินฝาก − เงินถอน'],
    ['กำไรรวมจากเงินต้น',`${money(L.total_gain)}`,upcls(L.total_gain),pctFixed(L.total_gain_pct,2)],
    ['Realized / Unrealized',`${money(L.realized)} / ${money(L.unrealized)}`,upcls(L.realized+L.unrealized),'ขายแล้ว / ยังถือ (เทียบต้นทุนเฉลี่ย)'],
    ['ปันผลสุทธิ',money(L.dividends_net),'',`ถูกหักภาษี ${money(L.withheld)} (${Math.round((L.withholding||0)*100)}%)`],
    ['ค่าธรรมเนียมสะสม',money(L.fees),L.fees>0?'down':'']]);
  const kpi2=kpisHTML([
    ['TWR (ฝีมือเลือกหุ้น)',p.twr_pct!=null?signed(p.twr_pct)+'%':'—',upcls(p.active_pp),twr,'ผลตอบแทนแบบตัดผลของเงินเข้า-ออก เทียบ SPY ช่วงเดียวกัน'],
    ['XIRR (เงินของคุณจริง)',L.xirr_pct!=null?signed(L.xirr_pct)+'%/ปี':'—',upcls(L.xirr_pct),L.xirr_note||`ตั้งแต่ ${esc(L.start)} · รวมจังหวะเติมเงิน`,'XIRR ต่ำกว่า TWR = เติมเงินผิดจังหวะ'],
    ['Max drawdown (NAV จริง)',p.max_drawdown_pct!=null?pctFixed(p.max_drawdown_pct,1):'—',p.max_drawdown_pct<0?'down':'','จาก NAV log (Run Daily ทุกวัน)'],
    ['Turnover',L.turnover_pct!=null?pctFixed(L.turnover_pct,0)+'/ปี':'—','','ยอดขาย ÷ NAV เฉลี่ย ต่อปี'],
    th?['มูลค่า / กำไรในหน่วยบาท',`฿${Number(th.nav).toLocaleString()}`,upcls(th.gain),`เงินต้น ฿${Number(th.principal).toLocaleString()} · กำไร ${pctFixed(th.gain_pct,2)} · USD/THB ${nf(th.usdthb,2)} (${esc(L.usdthb_date||'')})`]:null]);
  const pos=(L.positions||[]).filter(r=>r.held||r.realized||r.dividends);
  const posT=`<div class="tw"><table><thead><tr><th class="l">Ticker</th><th class="n">หุ้น</th><th class="n">ต้นทุนเฉลี่ย</th><th class="n">ราคา</th><th class="n">มูลค่า</th><th class="n">Unrealized</th><th class="n">Realized</th><th class="n">ปันผล</th><th class="n" title="มูลค่า + ขายได้ + ปันผล − มูลค่าวันเริ่ม − เงินซื้อเพิ่ม">P&amp;L ตั้งแต่เริ่ม</th><th class="n">สัดส่วนของกำไร</th></tr></thead><tbody>${
    pos.map(r=>`<tr><th scope="row" class="l num" style="color:var(--ink)">${esc(r.ticker)}${r.held?'':' <span class="note">(ขายหมด)</span>'}</th><td class="n">${r.held?nf(r.shares,r.shares%1?4:0):'—'}</td><td class="n">${r.avg_cost!=null?usd(r.avg_cost):'—'}</td><td class="n">${r.held?usd(r.price):'—'}</td><td class="n">${money(r.market_value)}</td>
      <td class="n ${upcls(r.unrealized)}">${r.held?money(r.unrealized)+` <span class="note">${pctFixed(r.unrealized_pct)}</span>`:'—'}</td><td class="n ${upcls(r.realized)}">${r.realized?money(r.realized):'—'}</td><td class="n">${r.dividends?money(r.dividends):'—'}</td>
      <td class="n ${upcls(r.since_start_pnl)}">${money(r.since_start_pnl)}</td><td class="n">${r.contribution_pct!=null?pctFixed(r.contribution_pct):'—'}</td></tr>`).join('')}</tbody></table></div>`;
  const closed=c.n?kpisHTML([['ไม้ที่ปิดแล้ว',String(c.n),'',`ชนะ ${c.wins}`],['Win rate',pctFixed(c.win_rate_pct),''],
      ['กำไรเฉลี่ย / ขาดทุนเฉลี่ย',`${c.avg_win_pct!=null?'+'+nf(c.avg_win_pct,1)+'%':'—'} / ${c.avg_loss_pct!=null?nf(c.avg_loss_pct,1)+'%':'—'}`,''],
      ['Profit factor',c.profit_factor!=null?nf(c.profit_factor,2):'—','','กำไรรวม ÷ ขาดทุนรวม'],['ถือเฉลี่ย',c.avg_days+' วัน','']])
    +`<div class="tw"><table><thead><tr><th class="l">วันที่ขาย</th><th class="l">Ticker</th><th class="n">หุ้น</th><th class="n">ราคาขาย</th><th class="n">ต้นทุนเฉลี่ย</th><th class="n">กำไร/ขาดทุน</th><th class="n">ถือ (วัน)</th></tr></thead><tbody>${
      (L.trades||[]).slice().reverse().map(x=>`<tr><td class="l">${esc(x.date)}</td><td class="l">${esc(x.ticker)}</td><td class="n">${nf(x.shares,x.shares%1?4:0)}</td><td class="n">${usd(x.price)}</td><td class="n">${usd(x.avg_cost)}</td><td class="n ${upcls(x.pnl)}">${money(x.pnl)} <span class="note">${pctFixed(x.pnl_pct)}</span></td><td class="n">${x.days}</td></tr>`).join('')}</tbody></table></div>`
    :'<p class="note">ยังไม่มีการขาย — สถิติไม้ที่ปิดแล้วจะขึ้นหลังรายการ SELL แรก</p>';
  const txT=`<div class="tw"><table><thead><tr><th class="n">#</th><th class="l">วันที่</th><th class="l">ประเภท</th><th class="l">Ticker</th><th class="n">หุ้น / อัตรา</th><th class="n">ราคา</th><th class="n">จำนวน $</th><th class="n">ค่าธรรมเนียม</th><th class="n">บาท</th><th class="l"></th></tr></thead><tbody>${
    (L.txns||[]).map(t=>`<tr><td class="n">${t.id}</td><td class="l">${esc(t.date)}</td><td class="l">${esc(t.type)}</td><td class="l">${esc(t.ticker||'')}</td><td class="n">${t.shares!=null?nf(t.shares,t.shares%1?4:0):t.ratio!=null?t.ratio+':1':''}</td><td class="n">${t.price!=null?usd(t.price):''}</td><td class="n">${t.amount!=null?money(t.amount):(t.shares&&t.price?money(t.shares*t.price):'')}</td><td class="n">${t.fee?usd(t.fee):''}</td><td class="n">${t.thb!=null?'฿'+Number(t.thb).toLocaleString():''}</td>
      <td class="l"><button type="button" class="btn" onclick="editTxn(${t.id})">แก้</button> <button type="button" class="btn" onclick="deleteTxn(${t.id})" aria-label="ลบรายการ ${t.id}">ลบ</button></td></tr>`).join('')}</tbody></table></div>`;
  return (warn.length?`<div class="banner tone t-bear">&#9888; ${warn.map(esc).join('<br>&#9888; ')}</div>`:'')
    +secHTML(1,'ภาพรวมเงินต้นและกำไร','ledger · ต้นทุนเฉลี่ย',kpi1)
    +secHTML(2,'ผลตอบแทน','TWR · XIRR · drawdown',kpi2)
    +secHTML(3,'รายหุ้น',`ตั้งแต่ ${esc(L.start||'')}`,posT)
    +secHTML(4,'ไม้ที่ปิดแล้ว','realized',closed)
    +secHTML(5,'รายการ','ล่าสุดก่อน',txnFormHTML()+txT);
}

// on load: the old editors stay locked while the ledger runs
fetch('/api/ledger').then(r=>r.ok?r.json():null).then(d=>{if(d&&d.active)lockOldEditors(true);}).catch(()=>{});
