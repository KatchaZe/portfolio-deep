// The card's "คู่แข่ง & Moat" tab: is the competitive position widening or narrowing?
// Reads row.peer_panel and row.moat_trend (domain/peer_panel.py, domain/moat_trend.py;
// docs/peer-panel/SPEC.md). Display only: nothing here feeds a score or a fair value.
// Functions only (no DOM work at load), so tests/test_peer_panel_render.py runs it in node.

const PP_MINUS='−';
const PP_LABEL={improving:['ดีขึ้น','↗','--bull'],stable:['ทรงตัว','→','--base'],eroding:['เสื่อม','↘','--bear']};
function ppLab(l){return PP_LABEL[l]||['ข้อมูลไม่พอ','·','--muted'];}
function ppNum(v){return typeof v==='number'&&isFinite(v);}
function ppSgn(v,d){return ppNum(v)?(v<0?PP_MINUS:v>0?'+':'')+Math.abs(v).toFixed(d==null?1:d):'—';}
function ppPct(f,d){return ppNum(f)?(f*100).toFixed(d==null?1:d)+'%':'—';}
function ppMoney(v){if(!ppNum(v))return '—';const a=Math.abs(v);return a>=1e9?'$'+(v/1e9).toFixed(a>=1e11?0:1)+'B':'$'+(v/1e6).toFixed(0)+'M';}
function ppMult(v){return ppNum(v)?v.toFixed(1)+'×':'—';}
function moatLabel(x){return ((x||{}).moat_trend||{}).label||null;}

// R25 fail-closed (D2): is gross margin off for this target? A panel built under R25-R29
// says so in gm_compare; a panel stored before has no gm_compare, so any SIC the row
// knows decides (the panel's, else SEC's from the profile, used only to hide), and a
// target with no known SIC shows no GM number until the next refresh.
const PP_GM_NA='gross margin ไม่ใช่ตัวชี้วัดของธุรกิจนี้ (ธนาคาร/ประกัน/การเงิน SIC 6000–6799)';
const PP_FIN_TARGET='หุ้นนี้เป็นธุรกิจการเงิน/ประกัน จึงไม่เทียบ GM';
function ppFinSic(c){const n=parseInt(Array.isArray(c)?c[0]:c,10);return n>=6000&&n<=6799;}
function ppSicKnown(c){const n=parseInt(Array.isArray(c)?c[0]:c,10);return n>0&&n<=9999;}
function ppIsFinancial(x){
  x=x||{}; const p=x.peer_panel||{}, g=(p.table||{}).gm_compare, sub=(p.industry_share||{}).sub_group;
  if(g&&g.applies===false)return true;
  return [g&&g.sic,sub&&sub.sic,(p.target_sic||{}).code,x.sec_sic].some(ppFinSic);
}
function ppGmState(x){
  x=x||{}; const p=x.peer_panel||{}, g=(p.table||{}).gm_compare, sub=(p.industry_share||{}).sub_group;
  if(ppIsFinancial(x))return {na:true,fin:true,reason:PP_GM_NA};
  if(!g&&![sub&&sub.sic,(p.target_sic||{}).code,x.sec_sic].some(ppSicKnown))
    return {na:true,fin:false,reason:'ข้อมูลชุดเก่ายังไม่บอกรหัส SIC ของหุ้นนี้ จึงไม่แสดง gross margin จนกว่าจะ refresh'};
  return {na:false,fin:false,reason:null};
}
// D9: every English reason the engine stores renders in Thai. The stored fields stay
// English (logs, tests, the verifier read them); this table translates at render time.
// A reason no entry matches renders marked data-untranslated, and
// tests/test_peer_panel_th.py fails on any such mark.
const PP_METRIC_TH={revenue_ttm:'รายได้ TTM',growth_fy:'การเติบโตปีงบ',cagr3_fy:'โต/ปี (3 ปี)',gross_margin_fy:'GM ปีงบ',
  op_margin_ttm:'OPM TTM',ev_sales:'EV/Sales',ev_ebit:'EV/EBIT',pe:'P/E'};
const PP_HINT_EN={intangible:1,scale:1,efficient_scale:1,cost_advantage:1,switching_costs:1,network:1};
const PP_THAI=/[฀-๿]/;
// [pattern, (match, tr) => Thai]; tr() translates an embedded reason (null = unmapped)
const PP_TH=[
  [/^(S[1-4]): (.+)$/,(m,t)=>t(m[2])&&`${m[1]}: ${t(m[2])}`],
  [/^sub-group SIC (\d{4}): (.+)$/,(m,t)=>t(m[2])&&`ธุรกิจเดียวกัน SIC ${m[1]}: ${t(m[2])}`],
  [/^gross margin: (.+)$/,(m,t)=>t(m[1])&&`gross margin: ${t(m[1])}`],
  [/^hints: gross margin hints skipped \((.+)\)$/,(m,t)=>t(m[1])&&`ไม่แสดงแหล่ง moat ที่อ่าน gross margin (${t(m[1])})`],
  [/^hints: no excess return \(ROIC − WACC ≤ 0\)$/,()=>'ยังไม่มีกำไรส่วนเกิน (ROIC − WACC ≤ 0) จึงไม่แสดงแหล่ง moat'],
  [/^target not on the common clock: no target row(?: \((.+)\))?$/,(m,t)=>'หุ้นนี้ไม่มีข้อมูลบนนาฬิกาเดียวกับคู่แข่ง จึงไม่มีแถวของหุ้นนี้'+(m[1]?(t(m[1])?` (${t(m[1])})`:null):'')],
  [/^peer panel failed: (\w+)$/,m=>`สร้างแท็บคู่แข่งไม่สำเร็จ (${m[1]})`],
  // S1-S4 (domain/moat_trend.py)
  [/^no WACC$/,()=>'ไม่มี WACC'],
  [/^(\d+) contiguous fiscal years of ROIC \(need (\d+)\)$/,m=>`ROIC ปีงบต่อเนื่องมี ${m[1]} ปี (ต้องมีอย่างน้อย ${m[2]} ปี)`],
  [/^fiscal-year dates do not vary$/,()=>'วันที่ปีงบไม่ต่างกัน คิดความชันไม่ได้'],
  [/^no industry share$/,()=>'ไม่มีส่วนแบ่งในอุตสาหกรรม'],
  [/^no target share on the filers reporting in both CY(\d{4}) and CY(\d{4})$/,m=>`ไม่มีส่วนแบ่งของหุ้นนี้ในกลุ่มบริษัทที่มีรายได้ทั้ง CY${m[1]} และ CY${m[2]}`],
  [/^target share in CY(\d{4}) is zero: no relative change$/,m=>`ส่วนแบ่งของหุ้นนี้ใน CY${m[1]} เป็นศูนย์ จึงคิดการเปลี่ยนเทียบส่วนแบ่งเดิมไม่ได้`],
  [/^target gross margin missing at FY0 or FY3$/,()=>'ไม่มี gross margin ของหุ้นนี้ใน FY0 หรือ FY3'],
  [/^(\d+) peers with gross margin at FY0 and FY3 \(need (\d+)\)$/,m=>`คู่แข่งที่มี gross margin ทั้ง FY0 และ FY3 มี ${m[1]} ราย (ต้องอย่างน้อย ${m[2]} ราย)`],
  [/^revenue or operating income missing at FY0 or FY3$/,()=>'ไม่มีรายได้หรือกำไรจากการดำเนินงานใน FY0 หรือ FY3'],
  [/^revenue not positive at FY0 or FY3$/,()=>'รายได้ใน FY0 หรือ FY3 ไม่เป็นบวก'],
  [/^revenue did not grow FY3 -> FY0$/,()=>'รายได้ไม่โตจาก FY3 ถึง FY0'],
  [/^revenue grew (-?[\d.]+)% in 3 years \(need ≥(\d+)%\)$/,m=>`รายได้โต ${m[1].replace('-',PP_MINUS)}% ใน 3 ปี (ต้องโตอย่างน้อย ${m[2]}%)`],
  [/^(\d+) signal\(s\): no label \(need (\d+)\)$/,m=>`มีสัญญาณ ${m[1]} ตัว จึงยังไม่สรุปแนวโน้ม (ต้องมีอย่างน้อย ${m[2]} ตัว)`],
  // industry share (domain/peer_panel.py)
  [/^(\d+) industry members have no SEC CIK \(not counted\)$/,m=>`สมาชิกอุตสาหกรรม ${m[1]} รายไม่มี SEC CIK (ไม่นับ)`],
  [/^(\d+) members not US-domiciled file us-gaap revenue with the SEC \(counted\)$/,m=>`สมาชิกที่จดทะเบียนนอกสหรัฐ ${m[1]} รายยื่นรายได้ us-gaap กับ SEC (นับรวม)`],
  [/^no industry members with an SEC CIK$/,()=>'ไม่มีสมาชิกอุตสาหกรรมที่มี SEC CIK'],
  [/^target has no SEC CIK: no share$/,()=>'หุ้นนี้ไม่มี SEC CIK จึงไม่มีส่วนแบ่ง'],
  [/^CY(\d{4}): no SEC revenue frames$/,m=>`CY${m[1]}: ไม่มีข้อมูลรายได้จาก SEC frames`],
  [/^CY(\d{4}): (\d+) of (\d+) members report revenue(?: or a balance sheet)? \((\d+)% < (\d+)%\): share not shown$/,m=>`CY${m[1]}: มีข้อมูลใน SEC ${m[2]} จาก ${m[3]} ราย (${m[4]}% ต่ำกว่าเกณฑ์ ${m[5]}%) จึงไม่แสดงส่วนแบ่งปีนี้`],
  [/^CY(\d{4}): no revenue frame for the target$/,m=>`CY${m[1]}: ไม่พบรายได้ของหุ้นนี้ใน SEC frames`],
  [/^CY(\d{4})\.\.CY(\d{4}): (\d+) of the (\d+) CY\d{4} filers report in CY\d{4} \((\d+)% < (\d+)%\): no share change$/,m=>`CY${m[1]}..CY${m[2]}: บริษัทที่มีรายได้ CY${m[1]} มีรายได้ใน CY${m[2]} ด้วย ${m[3]} จาก ${m[4]} ราย (${m[5]}% ต่ำกว่าเกณฑ์ ${m[6]}%) จึงไม่คิดการเปลี่ยนส่วนแบ่ง`],
  [/^CY(\d{4})\.\.CY(\d{4}): share change over the (\d+) filers with revenue in both years \((\d+) only in CY\d{4}, (\d+) only in CY\d{4}\)$/,m=>`CY${m[1]}..CY${m[2]}: คิดการเปลี่ยนส่วนแบ่งจาก ${m[3]} บริษัทที่มีรายได้ทั้งสองปี (มีเฉพาะ CY${m[1]} ${m[4]} ราย, เฉพาะ CY${m[2]} ${m[5]} ราย)`],
  [/^CY(\d{4}): (.+)$/,(m,t)=>t(m[2])&&`CY${m[1]}: ${t(m[2])}`],
  [/^no Damodaran industry for this ticker: no industry share$/,()=>'หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran จึงไม่มีส่วนแบ่งในอุตสาหกรรม'],
  [/^no stored peer points \(peer fit predates the panel or failed\): no table$/,()=>'ยังไม่มีข้อมูลคู่แข่งที่บันทึกไว้ (สร้างก่อนมีแท็บนี้หรือรอบนี้ล้มเหลว) จึงไม่มีตาราง'],
  [/^no usable peers: no table$/,()=>'ไม่มีคู่แข่งที่ใช้ได้ จึงไม่มีตาราง'],
  [/^every peer was excluded: no peer statistics$/,()=>'คู่แข่งถูกตัดออกทั้งหมด จึงไม่มีสถิติคู่แข่ง'],
  // why a table cell is empty (peer_panel.why_null): "SYM metric: reason"
  [/^([A-Z0-9.\-]{1,8}) (revenue_ttm|growth_fy|cagr3_fy|gross_margin_fy|op_margin_ttm|ev_sales|ev_ebit|pe): (.+)$/,(m,t)=>t(m[3])&&`${m[1]} ${PP_METRIC_TH[m[2]]}: ${t(m[3])}`],
  [/^no fiscal year on or before as-of$/,()=>'ไม่มีปีงบที่จบก่อนวันที่ข้อมูล'],
  [/^no positive revenue for FY0 \((.+)\)$/,m=>`รายได้ FY0 (${m[1]}) ไม่เป็นบวก`],
  [/^no fiscal year about one year before FY0 \((.+)\)$/,m=>`ไม่มีปีงบก่อน FY0 (${m[1]}) หนึ่งปี`],
  [/^FY-1 \((.+)\) revenue not positive$/,m=>`รายได้ FY-1 (${m[1]}) ไม่เป็นบวก`],
  [/^no contiguous fiscal year three years before FY0 \((.+)\)$/,m=>`ไม่มีปีงบต่อเนื่องย้อนหลังสามปีจาก FY0 (${m[1]})`],
  [/^FY3 \((.+)\) revenue not positive$/,m=>`รายได้ FY3 (${m[1]}) ไม่เป็นบวก`],
  [/^no GrossProfit or CostOfRevenue for FY0 \((.+)\)$/,m=>`ไม่ได้ยื่น GrossProfit หรือ CostOfRevenue ของ FY0 (${m[1]})`],
  [/^no (\S+) value for FY0 \((.+)\)$/,m=>`ไม่มีค่า ${m[1]} ของ FY0 (${m[2]})`],
  [/^no positive TTM revenue$/,()=>'ไม่มีรายได้ TTM ที่เป็นบวก'],
  [/^no TTM operating income$/,()=>'ไม่มีกำไรจากการดำเนินงาน TTM'],
  [/^enterprise value missing or not positive$/,()=>'ไม่มี EV หรือ EV ไม่เป็นบวก'],
  [/^TTM EBIT not positive$/,()=>'EBIT TTM ไม่เป็นบวก'],
  [/^no market cap$/,()=>'ไม่มีมูลค่าตลาด'],
  [/^TTM net income not positive$/,()=>'กำไรสุทธิ TTM ไม่เป็นบวก'],
  [/^not computable from its legs$/,()=>'คำนวณจากข้อมูลที่มีไม่ได้'],
  // peer selection notes and exclusions (pipeline/peers.py, domain/relative.py): "SYM: reason"
  [/^EV\/Sales outlier \(IQR fence\)$/,()=>'EV/Sales อยู่นอกกรอบ IQR (ตัดออกจากโมเดล)'],
  [/^fewer than two fiscal years$/,()=>'มีปีงบไม่ถึงสองปี'],
  [/^fiscal years not one year apart$/,()=>'ปีงบสองปีล่าสุดห่างกันไม่ใช่หนึ่งปี'],
  [/^prior-year revenue not positive$/,()=>'รายได้ปีก่อนไม่เป็นบวก'],
  [/^reports in (\S+)$/,m=>`รายงานเป็นสกุล ${m[1]}`],
  [/^files under (.+), not us-gaap$/,m=>`ยื่นงบตาม ${m[1]} ไม่ใช่ us-gaap`],
  [/^facts dated after (\S+)$/,m=>`ข้อมูลลงวันที่หลัง ${m[1]}`],
  [/^newest fiscal year (\S+) lags (\S+) by more than a year$/,m=>`ปีงบล่าสุด ${m[1]} ช้ากว่า ${m[2]} เกินหนึ่งปี`],
  [/^no TTM revenue$/,()=>'ไม่มีรายได้ TTM'],
  [/^no operating income$/,()=>'ไม่มีกำไรจากการดำเนินงาน'],
  [/^latest filing (\S+) filed after as-of (\S+)$/,m=>`งบล่าสุด ${m[1]} ยื่นหลังวันที่ข้อมูล ${m[2]}`],
  [/^latest filing (\S+) older than 6 months$/,m=>`งบล่าสุด ${m[1]} เก่ากว่า 6 เดือน`],
  [/^no T-1 close(?: \(Yahoo and Stooq\))?$/,()=>'ไม่มีราคาปิดวันทำการก่อนหน้า'],
  [/^no share count$/,()=>'ไม่มีจำนวนหุ้น'],
  [/^enterprise value not positive \(cash above market cap \+ debt\)$/,()=>'EV ไม่เป็นบวก (เงินสดมากกว่ามูลค่าตลาดรวมหนี้)'],
  [/^operating margin (-?\d+)% below (-?\d+)%$/,m=>`operating margin ${m[1].replace('-',PP_MINUS)}% ต่ำกว่า ${m[2].replace('-',PP_MINUS)}%`],
  [/^(\d+) usable peers \(need (\d+)\)$/,m=>`มีคู่แข่งที่ใช้ได้ ${m[1]} ราย (ต้องอย่างน้อย ${m[2]} ราย)`],
  [/^peers do not vary on margin\/growth \(singular fit\)$/,()=>'margin/การเติบโตของคู่แข่งไม่ต่างกันพอจะ fit โมเดล'],
  [/^not an SEC filer$/,()=>'ไม่ได้ยื่นงบกับ SEC'],
  [/^SEC facts unavailable \((.*)\)$/,m=>`ดึงข้อมูล SEC ไม่ได้ (${m[1]})`],
  [/^SEC facts stale$/,()=>'ข้อมูล SEC เก่า'],
  [/^SEC companyfacts lacks the (\S+) filed (\S+) \(not read\)$/,m=>`SEC companyfacts ยังไม่มีงบ ${m[1]} ที่ยื่น ${m[2]} (ยังไม่ได้อ่าน)`],
  [/^target SIC unknown: peers not industry-checked$/,()=>'ไม่ทราบ SIC ของหุ้นนี้ จึงไม่ได้ตรวจอุตสาหกรรมของคู่แข่ง'],
  [/^other industry: SIC (.+) vs (.+)$/,m=>`คนละอุตสาหกรรม: SIC ${m[1]} เทียบ ${m[2]}`],
  [/^close (\S+) not within (\d+) days of (\S+)$/,m=>`ราคาปิด ${m[1]} ห่างจาก ${m[3]} เกิน ${m[2]} วัน`],
  [/^no peers$/,()=>'ไม่มีคู่แข่ง'],
  [/^target margin outside the peers' range \(extrapolated\)$/,()=>'margin ของหุ้นนี้อยู่นอกช่วงของคู่แข่ง (คาดการณ์นอกช่วง)'],
  [/^target growth outside the peers' range \(extrapolated\)$/,()=>'การเติบโตของหุ้นนี้อยู่นอกช่วงของคู่แข่ง (คาดการณ์นอกช่วง)'],
  [/^the fit implies a non-positive multiple at this margin$/,()=>'โมเดลให้ multiple ไม่เป็นบวกที่ margin นี้'],
  [/^(.+) value not positive: net debt exceeds the implied EV$/,m=>`มูลค่า ${m[1]} ไม่เป็นบวก: หนี้สุทธิมากกว่า EV ที่โมเดลให้`],
  [/^only (\d+) of (\d+) peers share SIC group (\S+) \((.*)\); all kept$/,m=>`คู่แข่งที่อยู่กลุ่ม SIC ${m[3]} (${m[4]}) มี ${m[1]} จาก ${m[2]} ราย จึงเก็บไว้ทั้งหมด`],
  [/^(\d+) usable Damodaran peers: filled from the FMP list$/,m=>`คู่แข่งจาก Damodaran ใช้ได้ ${m[1]} ราย จึงเติมจากรายชื่อ FMP`],
  [/^peer fit from (\S+) reused \(this week's failed\)$/,m=>`ใช้โมเดลคู่แข่งของ ${m[1]} (รอบนี้ล้มเหลว)`],
  [/^size band widened to 0\.02-50x revenue$/,()=>'ขยายช่วงขนาดเป็น 0.02–50 เท่าของรายได้'],
  [/^fewer than 5 Damodaran peers within 0\.02-50x revenue$/,()=>'คู่แข่งจาก Damodaran ในช่วง 0.02–50 เท่าของรายได้มีไม่ถึง 5 ราย'],
  [/^no peer list from FMP$/,()=>'รอบนี้ไม่ได้รายชื่อคู่แข่งจาก FMP'],
  [/^no FMP key$/,()=>'ไม่มี FMP key'],
  [/^(SEC revenue frames|SEC Assets frames) from cache \(refresh failed\)$/,m=>`${m[1]} จาก cache (ดึงใหม่ไม่สำเร็จ)`],
  [/^(SEC revenue frames|SEC Assets frames|Damodaran company list|Damodaran global list|peer universe) unavailable \((\w+)\)$/,m=>`ไม่มี ${m[1]} (${m[2]})`],
  [/^(Damodaran company list|Damodaran global list) from (\S+) \(refresh failed\)$/,m=>`${m[1]} ชุด ${m[2]} (ดึงใหม่ไม่สำเร็จ)`],
  // hint evidence stored before the UI wrote hints from their measures (R23)
  [/^gross margin (-?\d+)pp above the peer median \(FY0\): consistent with pricing power from brand or IP$/,m=>`gross margin สูงกว่า median คู่แข่ง ${m[1]}% (ปีงบล่าสุด) สอดคล้องกับอำนาจกำหนดราคาจากแบรนด์หรือทรัพย์สินทางปัญญา`],
  [/^(\d+)% of industry revenue among US SEC filers \(CY(\d+)\): consistent with a scale advantage$/,m=>`${m[1]}% ของรายได้ทั้งอุตสาหกรรม (บริษัทที่ยื่น SEC, CY${m[2]}) สอดคล้องกับความได้เปรียบด้านขนาด`],
  [/^top 5 filers hold (\d+)% of industry revenue \(CY(\d+)\), the target's share moved ([+\-][\d.]+)pp over (\S+), (\d+) SEC filers: consistent with efficient scale$/,m=>`5 บริษัทใหญ่สุดมีรายได้รวม ${m[1]}% ของอุตสาหกรรม (CY${m[2]}) ส่วนแบ่งของหุ้นนี้เปลี่ยน ${m[3].replace('-',PP_MINUS)}% (${m[4]}) ในกลุ่ม ${m[5]} บริษัท สอดคล้องกับตลาดที่รองรับผู้เล่นได้ไม่กี่ราย`],
  [/^top 5 filers hold (\d+)% of industry revenue$/,m=>`5 บริษัทใหญ่สุดมีรายได้รวม ${m[1]}% ของอุตสาหกรรม`],
  [/^operating margin (\d+)pp above the peer median \(TTM\) with gross margin ([+\-]\d+)pp vs the median \(FY0\): consistent with a cost advantage$/,m=>`operating margin สูงกว่า median คู่แข่ง ${m[1]}% (TTM) ขณะที่ gross margin ต่างจาก median ${m[2].replace('-',PP_MINUS)}% (ปีงบล่าสุด) สอดคล้องกับความได้เปรียบด้านต้นทุน`],
  [/^operating margin (\d+)pp above the peer median \(TTM\)$/,m=>`operating margin สูงกว่า median คู่แข่ง ${m[1]}% (TTM)`],
  [/^(intangible|scale|efficient_scale|cost_advantage|switching_costs|network): (.+)$/,(m,t)=>t(m[2])&&`${PP_HINT[m[1]]}: ${t(m[2])}`],
  [/^([A-Z0-9.\-]{1,8}): (.+)$/,(m,t)=>t(m[2])&&`${m[1]}: ${t(m[2])}`],
];
// Thai for one stored reason, or null when no entry matches it. A reason already in
// Thai (the engine writes the SIC rules' reasons in Thai) passes as written.
function ppThai(s){
  s=String(s==null?'':s).trim();
  for(const [re,fn] of PP_TH){const m=s.match(re); if(m){const out=fn(m,ppThai); if(out)return out;}}
  return PP_THAI.test(s)&&!/[A-Za-z]{3,} [a-z]{3,} [a-z]{3,}/.test(s.replace(/gross margin|ROIC|WACC|SEC frames|SEC CIK|Damodaran|us-gaap/g,''))?s:null;
}
// escaped HTML for a stored reason: Thai, or the English marked as untranslated
function ppReason(s){
  const t=ppThai(s);
  return t!=null?esc(t):`<span class="pp-untr" data-untranslated="1" lang="en">${esc(pp2pct(String(s)))}</span>`;
}

// the arrow inside the card face's Moat chip; nothing when there is no label
function moatArrow(x){
  const l=moatLabel(x); if(!PP_LABEL[l])return '';
  const [w,a]=PP_LABEL[l];
  return ` <span class="pp-arrow" aria-hidden="true">${a}</span><span class="sr">แนวโน้ม ${w}</span>`;
}

// S1-S4: Thai name, band in display units, value formatter, value -> display units
const PP_SIG={
  spread_slope:{n:'ROIC − WACC เปลี่ยนต่อปี',s:'S1',th:1.0,u:'%/ปี',fmt:v=>ppSgn(v,2)+'%/ปี',k:v=>v},
  share_change:{n:'ส่วนแบ่งรายได้ในอุตสาหกรรม (เปลี่ยนเทียบส่วนแบ่งเดิม)',s:'S2',th:10,u:'%',fmt:v=>ppSgn(v*100,1)+'% ของเดิม',k:v=>v*100},
  gm_gap_change:{n:'Gross margin เทียบ median คู่แข่ง (เปลี่ยน)',s:'S3',th:2.0,u:'%',fmt:v=>ppSgn(v*100,1)+'%',k:v=>v*100},
  incremental_margin:{n:'Margin ส่วนเพิ่ม เทียบ margin เฉลี่ย',s:'S4',th:5.0,u:'%',fmt:v=>ppSgn(v*100,1)+'%',k:v=>v*100}};
const PP_DIR={'1':'ดีขึ้น','0':'ไม่ชัด','-1':'แย่ลง'};

// a diverging bar in band units, clamped to ±3; dashed lines mark the ±1 band
function ppSigBar(x,dir){
  const c=Math.max(-3,Math.min(3,x)),W=300,mid=150,u=W/6,w=Math.abs(c)*u;
  const fill=dir>0?'--bull':dir<0?'--bear':'--c6';
  return `<svg viewBox="0 0 ${W} 22" preserveAspectRatio="none" aria-hidden="true" focusable="false">
   <rect x="0" y="9" width="${W}" height="4" style="fill:var(--track)"/>
   <rect x="${(c<0?mid-w:mid).toFixed(1)}" y="5" width="${w.toFixed(1)}" height="12" rx="2" style="fill:var(${fill})"/>
   <line x1="${mid}" x2="${mid}" y1="1" y2="21" style="stroke:var(--ink-2);stroke-width:1.5"/>
   <line x1="${mid-u}" x2="${mid-u}" y1="2" y2="20" style="stroke:var(--ctl);stroke-dasharray:3 3"/>
   <line x1="${mid+u}" x2="${mid+u}" y1="2" y2="20" style="stroke:var(--ctl);stroke-dasharray:3 3"/></svg>`;
}

// R26/R28: the set each signal was measured on, in words (S2 sub-group or industry, S3 SIC)
// D7: how many filers a share figure counts, and why that many (both CYs of the window)
function ppMatchedTxt(n,of,win){
  const y=String(win||'').match(/CY(\d{4})\.\.CY(\d{4})/), yy=y?`ทั้ง CY${y[1]} และ CY${y[2]}`:'ทั้งสองปี';
  return `${ppNum(n)?n:'—'} รายที่มีรายได้${yy}${ppNum(of)?' จาก '+of+' ราย':''}`;
}
function ppSigSet(s){
  const c=s.comparators;
  if(s.name==='gm_gap_change'&&c&&c.level)return `เทียบ SIC ${c.level===4?c.sic:String(c.sic).slice(0,3)+'x'} ${c.n} ราย`;
  if(s.name==='share_change'&&s.basis==='sub_group')return `ธุรกิจเดียวกัน SIC ${s.sic} (${ppMatchedTxt(s.matched_n,s.n_members,s.window)})`;
  if(s.name==='share_change'&&s.basis==='industry')return `ทั้งอุตสาหกรรม${ppNum(s.matched_n)?' ('+ppMatchedTxt(s.matched_n,s.n_members,s.window)+')':''}`;
  return '';
}
function ppSignals(m,gm){
  const sigs=m.signals||[];
  const why=k=>(m.flags||[]).filter(f=>String(f).startsWith(PP_SIG[k].s+':')).map(f=>ppReason(String(f).slice(4))).join('; ')||'ไม่มีข้อมูล';
  return Object.keys(PP_SIG).map(k=>{
    const s=k==='gm_gap_change'&&gm&&gm.na?null:sigs.find(z=>z.name===k),D=PP_SIG[k];
    const w=k==='gm_gap_change'&&gm&&gm.na?esc(gm.reason):why(k);
    if(!s||!ppNum(s.value))return `<div class="pp-sig"><span class="pp-name">${D.n}</span><span class="note">${w}</span><span class="pp-val note">—</span></div>`;
    const dir=String(s.direction), set=ppSigSet(s);
    const br=s.basis==='industry'&&s.basis_reason?`<br><span class="note">${ppReason(s.basis_reason)}</span>`:'';
    return `<div class="pp-sig" role="img" aria-label="${esc(D.n)}: ${esc(D.fmt(s.value))}, เกณฑ์ ±${D.th}${D.u}, ${PP_DIR[dir]||'ไม่ชัด'}, ช่วง ${esc(s.window||'')}${set?', '+esc(set):''}"><span class="pp-name">${D.n}<br><span class="note">${esc(s.window||'')}${set?' · '+esc(set):''}</span>${br}</span>${ppSigBar(D.k(s.value)/D.th,s.direction)}<span class="pp-val num">${esc(D.fmt(s.value))}<br><span class="note">${PP_DIR[dir]||'ไม่ชัด'}</span></span></div>`;
  }).join('');
}

// width (the app's ROIC* - WACC class, card_drawer.js), trend, and the uncertainty rating
function ppHeadline(x){
  const sp=(x.key_metrics||{}).spread_pct, m=x.moat_trend||{}, u=x.uncertainty||{};
  const width=!ppNum(sp)?'—':sp>=10?'กว้าง':sp>=0?'แคบ':'ไม่มี';
  const [w,a]=ppLab(m.label);
  return `<div class="pp-head">
    <div><span class="cap">ความกว้าง moat</span><b>${width}</b><span class="note">วัดจากงบย้อนหลัง${ppNum(sp)?' · ROIC−WACC '+ppSgn(sp,1)+'%':''}</span></div>
    <div><span class="cap">แนวโน้ม</span><b><span aria-hidden="true">${a}</span> ${w}</b><span class="note">${m.label?(m.n_signals||0)+' สัญญาณ':'สัญญาณไม่ถึง 2 ตัว'}</span></div>
    <div><span class="cap">ความไม่แน่นอน</span><b>${esc(u.rating||'—')}</b><span class="note">ของมูลค่า (แท็บ มูลค่า)</span></div></div>`;
}

function ppRanks(p){
  const t=((p.table||{}).rows||[]).find(r=>r.is_target);
  if(!t||!t.rank3)return `<h3 class="pp-sub">ตำแหน่งในกลุ่มคู่แข่ง</h3><p class="note" style="margin:0">ไม่มีตารางคู่แข่ง จึงจัดอันดับไม่ได้</p>`;
  const W={1:'กลุ่มบน',2:'กลุ่มกลาง',3:'กลุ่มล่าง'}, n=p.table.rows.length, tv=p.table.target_vs_median||{};
  const cell=(k,l,extra)=>`<div><span class="cap">${l}</span><b>${W[t.rank3[k]]||'—'}</b><span class="note">${extra}</span></div>`;
  return `<h3 class="pp-sub">ตำแหน่งในกลุ่มคู่แข่ง (${n} บริษัท รวมหุ้นนี้ · ทั้งกลุ่ม Damodaran)</h3>
   <div class="pp-ranks">${cell('scale','ขนาด','รายได้ TTM')}${cell('growth','การเติบโต',ppNum(tv.cagr3_fy)?'โต 3 ปี '+ppSgn(tv.cagr3_fy,1)+'% จาก median':'โต 3 ปี')}${cell('margin','Margin',ppNum(tv.op_margin_ttm)?'OPM '+ppSgn(tv.op_margin_ttm,1)+'% จาก median':'OPM TTM')}</div>
   <p class="note" style="margin:0">แบ่ง 3 กลุ่มเท่า ๆ กันตามลำดับ (บน = ดีที่สุดหนึ่งในสาม)</p>`;
}

function ppLead(x){
  const m=x.moat_trend||{}, p=x.peer_panel||{}, [w,a,tok]=ppLab(m.label), sigs=m.signals||[], gm=ppGmState(x);
  const ups=sigs.filter(s=>s.direction>0).length, dns=sigs.filter(s=>s.direction<0).length;
  const v=m.votes||{}, poss=ppNum(v.possible)?v.possible:4;
  const na=(v.not_applicable||[]).length?` · S3 gross margin ไม่ใช้กับธุรกิจนี้`:'';
  const of=` (วัดได้ ${sigs.length} จาก ${poss} ตัวที่ใช้กับธุรกิจนี้${na})`;
  const sum=m.label?`สัญญาณดีขึ้น ${ups} · แย่ลง ${dns} · ไม่ชัด ${sigs.length-ups-dns} จาก ${sigs.length} ที่มีข้อมูล${of} (ต้องรวมได้ +2 หรือ ${PP_MINUS}2 จึงจะไม่ใช่ “ทรงตัว”)`:`มีสัญญาณ ${sigs.length} ตัว${of} ต้องมีอย่างน้อย 2 ตัวจึงจะสรุปได้`;
  const watch=(m.watch||[]).length?`<p class="note" style="margin:0"><b>ติดตามไตรมาสหน้า:</b> ${m.watch.map(esc).join(' · ')}</p>`:'';
  return `<div class="box pp-lead pp-s5"><span class="cap">แนวโน้ม moat</span>
     <div class="pp-big" style="color:color-mix(in srgb,var(${tok}) 75%,var(--ink))"><span class="pp-arrow" aria-hidden="true">${a}</span><span>${w}</span></div>
     <span class="pill" style="${tone('--warn')};justify-self:start">เกณฑ์ชั่วคราว · ยังไม่ backtest</span>
     ${ppHeadline(x)}
     <p class="note" style="margin:0">${sum}</p>${watch}
     ${ppRanks(p)}</div>
   <div class="box pp-s7"><h3>สัญญาณ 4 ตัว (เส้นประ = เกณฑ์ ± ที่นับว่าเปลี่ยนจริง)</h3>${ppSignals(m,gm)}</div>`;
}

// R22-R23: the UI writes the Thai sentence from the hint's measures; a hint stored before
// measures existed falls back to its English evidence, translated where the shape is known
const PP_HINT={intangible:'ความได้เปรียบไม่มีตัวตน (แบรนด์/สิทธิบัตร)',scale:'ความได้เปรียบด้านขนาด',
  efficient_scale:'ขนาดตลาดที่พอดี (efficient scale)',cost_advantage:'ความได้เปรียบด้านต้นทุน',
  switching_costs:'ต้นทุนการเปลี่ยนเจ้า',network:'network effect'};
const PP_TREND={'1':['↗','แข็งขึ้น'],'0':['→','ทรงตัว'],'-1':['↘','อ่อนลง']};
function ppHintTh(h){
  const ms={}; (h.measures||[]).forEach(m=>{ms[m.metric]=m;});
  const v=k=>(ms[k]||{}).value, per=k=>esc((ms[k]||{}).period||'')+ppCompTxt((ms[k]||{}).comparators);
  if(h.type==='intangible'&&ppNum(v('gm_gap_pp')))return `gross margin สูงกว่า median คู่แข่ง ${v('gm_gap_pp').toFixed(0)}% (${per('gm_gap_pp')}) สอดคล้องกับอำนาจกำหนดราคาจากแบรนด์หรือทรัพย์สินทางปัญญา`;
  if(h.type==='scale'&&ppNum(v('industry_share')))return `${ppPct(v('industry_share'),0)} ของรายได้ทั้งอุตสาหกรรม (บริษัทที่ยื่น SEC, ${per('industry_share')}) สอดคล้องกับความได้เปรียบด้านขนาด`;
  if(h.type==='efficient_scale'&&ppNum(v('top5_share')))return `5 บริษัทใหญ่สุดมีรายได้รวม ${ppPct(v('top5_share'),0)} ของอุตสาหกรรม (${per('top5_share')}) ส่วนแบ่งของหุ้นนี้เปลี่ยน ${ppSgn(v('share_change_pp'),1)}% (${per('share_change_pp')}) ในกลุ่ม ${esc(v('n_members'))} บริษัท สอดคล้องกับตลาดที่รองรับผู้เล่นได้ไม่กี่ราย`;
  if(h.type==='cost_advantage'&&ppNum(v('opm_gap_pp')))return `operating margin สูงกว่า median คู่แข่ง ${v('opm_gap_pp').toFixed(0)}% (TTM) ขณะที่ gross margin ต่างจาก median ${ppSgn(v('gm_gap_pp'),0)}% (${per('gm_gap_pp')}) สอดคล้องกับความได้เปรียบด้านต้นทุน`;
  return ppReason(h.evidence||'');
}
function ppCompTxt(c){return c&&c.level?`, เทียบ SIC ${esc(c.level===4?c.sic:String(c.sic).slice(0,3)+'x')} ${esc(c.n)} ราย`:'';}
// R24: no hint without excess returns; the server says so in a flag and the tab says it in Thai
const PP_NO_EXCESS='hints: no excess return';
const PP_GM_HINTS={intangible:1,cost_advantage:1};
function ppHints(m,gm){
  const h=(m.source_hints||[]).filter(x=>!(gm&&gm.na&&PP_GM_HINTS[x.type]));
  if(!h.length)return (m.flags||[]).some(f=>String(f).startsWith(PP_NO_EXCESS))
    ?`<h3>แหล่ง moat ที่ตัวเลข “สอดคล้อง”</h3><p class="pp-empty">ยังไม่มีกำไรส่วนเกิน (ROIC ≤ WACC) จึงไม่นับเป็นแหล่ง moat</p>`:'';
  const tr=t=>{const x=PP_TREND[String(t)];return x?` · <span aria-hidden="true">${x[0]}</span> ${x[1]}`:(t===null?' · แนวโน้มยังไม่ทราบ':'');};
  return `<h3>แหล่ง moat ที่ตัวเลข “สอดคล้อง” (ไม่ใช่ข้อสรุป)</h3><ul class="pp-hints">${h.map(x=>`<li>${esc(PP_HINT[x.type]||x.type)} <span class="note">(${x.strength==='moderate'?'ปานกลาง':'อ่อน'}${tr(x.trend)})</span><br><span class="note">${ppHintTh(x)}</span></li>`).join('')}</ul>`;
}

// Market share (mockup approved 2026-11-22, $HOME/work/mock/build.py): one view is one basis,
// the same-SIC sub-group or the whole Damodaran industry, drawn from the engine's `chart`
// block (domain/peer_panel.py). Peer bands carry a pattern by rank so they read without
// colour; the same swatch marks that company in the legend, the dumbbell and the table.
const PP_PAT=['h','v','d','g','x'];   // peer band 1..5: horizontal, vertical, dots, diagonal, cross-hatch; others solid
const PP_CAVEAT='รายได้ทั้งบริษัท ไม่ใช่เฉพาะสายธุรกิจ · นับเฉพาะบริษัทที่ยื่น SEC · ปีปฏิทิน ไม่ใช่ TTM · ไม่มีผลต่อคะแนน';
let PP_SEQ=0;
function ppSp(f){if(!ppNum(f))return '—';const v=f*100;return (Math.abs(v)<1?v.toFixed(2):v.toFixed(1))+'%';}
// a change in percentage points -> [arrow, words, kind]; sign, arrow and word, never colour alone
function ppMove(c){
  const s=Math.abs(c).toFixed(Math.abs(c)<1?2:1);
  if(Number(s)===0)return ['→','ทรงตัว 0','flat'];
  return c>0?['↑','เพิ่ม +'+s,'up']:['↓','ลด '+PP_MINUS+s,'down'];
}
function ppRel(a,b){
  if(!a)return '';
  const r=(b-a)/a*100, s=Math.abs(r).toFixed(Math.abs(r)>=10?0:1);
  return Number(s)===0?'(0%)':`(${r>0?'+':PP_MINUS}${s}%)`;
}
function ppNiceMax(v){for(const m of [0.05,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9])if(v<=m+1e-9)return m;return 1;}
function ppHasSub(p){const g=((p||{}).industry_share||{}).sub_group;return !!(g&&g.sic&&!g.reason);}
function ppViewOf(p,v){return ppHasSub(p)?(v==='all'?'all':'sub'):'all';}

// the drawn model of one basis: dumbbell rows (top 8, this stock, others), bands (this stock,
// the newest CY's top 5, others), gainers/losers; null when the panel predates the chart block
function ppShareView(b,kind,tkr){
  const c=b&&b.chart; if(!c||!(c.members||[]).length)return null;
  const ms=c.members, yrs=c.years||[], L=yrs.length-1, w0=(c.window||[])[0], w1=(c.window||[])[1];
  const isT=m=>(m.symbols||[]).includes(tkr), sym=m=>(m.symbols||[]).join('/')||'CIK '+m.cik;
  const order=ms.filter(m=>ppNum(m.share[L])).sort((a,z)=>z.share[L]-a.share[L]);
  const rank={}; order.forEach((m,i)=>{rank[m.cik]=i+1;});
  const tm=ms.find(isT)||null;
  const v={kind,b,yrs,w0,w1,order,rank,isT,sym,tm,tkr,tr:c.target_rank||{},pat:{}};
  const top5=order.slice(0,5), bands=[];
  if(tm&&!top5.includes(tm))bands.push({m:tm,tgt:true});
  top5.forEach(m=>bands.push({m,tgt:isT(m)}));
  bands.sort((a,z)=>z.tgt-a.tgt);
  let pi=0;
  bands.forEach(bd=>{bd.label=sym(bd.m);bd.vals=bd.m.share.map(s=>s||0);bd.key=bd.tgt?'t':PP_PAT[pi++];v.pat[bd.m.cik]=bd.key;});
  const nRest=ms.length-bands.length;
  if(nRest>0)bands.push({m:null,key:'o',tgt:false,label:`อื่น ๆ ${nRest} ราย`,
    vals:yrs.map((y,i)=>Math.max(0,1-bands.reduce((a,bd)=>a+bd.vals[i],0)))});
  v.bands=bands;
  const matched=order.filter(m=>m.matched);
  if(!matched.length){
    const cov=(b.coverage||{})[w0], nc=(b.n_covered||{})[w0];
    v.empty=ppNum(cov)&&cov<0.7&&ppNum(nc)
      ?`ยังวาดกราฟไม่ได้ เพราะ CY${w0} มีบริษัทในกลุ่มนี้ที่มีข้อมูลใน SEC ${nc} จาก ${b.n_members} ราย (${(cov*100).toFixed(0)}% ต่ำกว่าเกณฑ์ 70%) จึงเทียบ CY${w0} กับ CY${w1} ไม่ได้`
      :`ยังวาดกราฟไม่ได้ เพราะไม่มีบริษัทในกลุ่มนี้ที่มีรายได้ทั้ง CY${w0} และ CY${w1}`;
    return v;
  }
  const shown=matched.slice(0,8), extra=tm&&tm.matched&&!shown.includes(tm)?tm:null;
  const rest=matched.filter(m=>!shown.includes(m)&&m!==extra);
  const row=(m,gap)=>({m,label:sym(m),rank:rank[m.cik],then:m.share_then,now:m.share_now,tgt:isT(m),gap:!!gap});
  const rows=shown.map(m=>row(m)); if(extra)rows.push(row(extra,true));
  const sum=k=>rows.reduce((a,r)=>a+r[k],0);
  if(rest.length)rows.push({m:null,label:`อื่น ๆ ${rest.length} ราย`,rank:null,then:Math.max(0,1-sum('then')),now:Math.max(0,1-sum('now')),tgt:false,others:true});
  const last=Math.max(...rows.filter(r=>r.rank&&!r.gap).map(r=>r.rank));
  v.skipped=order.filter(m=>!m.matched&&rank[m.cik]<=last);
  v.rows=rows; v.matchedN=matched.length;
  const peers=matched.filter(m=>!isT(m));
  v.gain=peers.filter(m=>m.change_pp>0.05).sort((a,z)=>z.change_pp-a.change_pp).slice(0,2);
  v.loss=peers.filter(m=>m.change_pp<-0.05).sort((a,z)=>a.change_pp-z.change_pp).slice(0,2);
  return v;
}

// per-box patterns and arrowheads; `u` is unique per render so several cards share a page
function ppDefs(u){
  const ln=d=>`<path d="${d}" class="pp-ph"/>`, dg='M-1 1L1 -1M0 6L6 0M5 7L7 5';
  const P={h:ln('M0 3H6'),v:ln('M3 0V6'),d:'<circle cx="3" cy="3" r="1.5" class="pp-pd"/>',g:ln(dg),x:ln(dg+'M-1 5L1 7M0 0L6 6M5 -1L7 1')};
  return `<svg class="pp-defs" width="0" height="0" aria-hidden="true" focusable="false"><defs>`
    +PP_PAT.map(k=>`<pattern id="${u}-${k}" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" class="pp-pb"/>${P[k]}</pattern>`).join('')
    +['t','p','o'].map(k=>`<marker id="${u}-a${k}" viewBox="0 0 10 10" refX="16" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto"><path d="M0,1 L9,5 L0,9 z" class="pp-ah pp-ah-${k}"/></marker>`).join('')
    +'</defs></svg>';
}
function ppPatFill(u,k){return PP_PAT.includes(k)?` style="fill:url(#${u}-${k})"`:'';}
function ppSw(u,k){return `<svg class="pp-sw" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><rect x="0.5" y="0.5" width="11" height="11" class="pp-band pp-band-${k}"${ppPatFill(u,k)}/></svg>`;}

function ppDumbbell(v,u){
  const rows=v.rows, xmax=ppNiceMax(Math.max(...rows.map(r=>Math.max(r.then,r.now))));
  const grid=[0,50,100].map(g=>`<line x1="${g}%" x2="${g}%" y1="2" y2="24" class="pp-dbg"/>`).join('');
  const out=rows.map(r=>{
    const a=r.then/xmax*100, z=r.now/xmax*100, cls=r.tgt?'t':r.others?'o':'p', [arr,txt,k]=ppMove((r.now-r.then)*100);
    const line=Math.abs(z-a)>=2.5?`<line x1="${a.toFixed(2)}%" x2="${z.toFixed(2)}%" y1="13" y2="13" class="pp-dbl" marker-end="url(#${u}-a${cls})"/>`:'';
    const rk=r.rank?(r.tgt?`อันดับ ${r.rank} จาก ${esc(v.tr.of)}`:`อันดับ ${r.rank}`):'รวมกัน';
    const pk=r.m&&!r.tgt&&v.pat[r.m.cik];
    return `<div class="pp-dbr pp-dbr-${cls}${r.gap?' pp-dbr-gap':''}" data-label="${esc(r.label)}" data-then="${r.then.toFixed(6)}" data-now="${r.now.toFixed(6)}" data-cx0="${a.toFixed(2)}" data-cx1="${z.toFixed(2)}" data-xmax="${xmax}">`
      +`<div class="pp-dbn">${pk?ppSw(u,pk):''}<b>${esc(r.label)}</b>${r.tgt?' <span class="pp-tg">หุ้นนี้</span>':''}<span class="note">${rk}</span></div>`
      +`<svg class="pp-dbt" width="100%" height="26" aria-hidden="true" focusable="false">${grid}${line}<circle cx="${a.toFixed(2)}%" cy="13" r="5" class="pp-d0"/><circle cx="${z.toFixed(2)}%" cy="13" r="5.5" class="pp-d1"/></svg>`
      +`<div class="pp-dbv num">${ppSp(r.then)} → ${ppSp(r.now)} · <span class="pp-mv"><span aria-hidden="true">${arr}</span> ${txt}</span><span class="pp-rel"> ${ppRel(r.then,r.now)}</span></div></div>`;
  }).join('');
  const ax=[0,50,100].map(g=>`<span style="left:${g}%">${Math.round(xmax*g)}%</span>`).join('');
  const t=rows.find(r=>r.tgt), lead=rows[0];
  const verb=t?{up:'เพิ่ม',down:'ลด',flat:'ทรงตัว'}[ppMove((t.now-t.then)*100)[2]]:'';
  const lbl=(t?`${v.tkr} ส่วนแบ่ง${verb}จาก ${ppSp(t.then)} เป็น ${ppSp(t.now)}${ppNum(v.tr.rank)?` อันดับ ${v.tr.rank} จาก ${v.tr.of}`:''}`
    :`${v.tkr} ไม่อยู่ในชุดเทียบ CY${v.w0} → CY${v.w1}`)+(lead.tgt?'':` · อันดับ 1 คือ ${lead.label} ${ppSp(lead.now)}`);
  return `<div class="pp-db" role="img" aria-label="${esc(lbl)}">
    <div class="pp-dbr pp-dbh" aria-hidden="true"><span></span><div class="pp-dbax">${ax}</div><span class="note">CY${v.w0} → CY${v.w1} · เปลี่ยน <span class="pp-rel">(เทียบเดิม)</span></span></div>${out}</div>`;
}

function ppStack(v,u){
  const yrs=v.yrs, bands=v.bands, n=yrs.length, W=400, H=180, X=i=>n>1?i*W/(n-1):W/2, Y=f=>((1-f)*H).toFixed(2);
  let cum=yrs.map(()=>0); const polys=[], labels=[];
  bands.forEach(bd=>{
    const lo=cum, hi=cum.map((c,i)=>c+bd.vals[i]); cum=hi;
    const idx=yrs.map((y,i)=>i), pts=idx.map(i=>`${X(i).toFixed(2)},${Y(hi[i])}`).concat(idx.reverse().map(i=>`${X(i).toFixed(2)},${Y(lo[i])}`)).join(' ');
    polys.push(`<polygon points="${pts}" class="pp-band pp-band-${bd.key}"${ppPatFill(u,bd.key)} data-label="${esc(bd.label)}"/>`);
    const th=bd.vals[n-1];
    if(th>=0.085)labels.push(`<span class="pp-sl" style="top:${((1-(lo[n-1]+hi[n-1])/2)*100).toFixed(1)}%">${esc(bd.label)} ${ppSp(th)}</span>`);
  });
  const grid=[0,H/2,H].map(y=>`<line x1="0" x2="${W}" y1="${y}" y2="${y}" class="pp-sg"/>`).join('');
  const tb=bands.find(bd=>bd.tgt), ob=bands[bands.length-1];
  const lbl=`ส่วนแบ่งรายปี CY${yrs[0]} ถึง CY${yrs[n-1]}: `+(tb?`${v.tkr} ${ppSp(tb.vals[0])} เป็น ${ppSp(tb.vals[n-1])}`:`${v.tkr} ไม่มีส่วนแบ่ง`)
    +(ob.key==='o'?`; กลุ่มอื่น ๆ ${ppSp(ob.vals[0])} เป็น ${ppSp(ob.vals[n-1])}`:'; ไม่มีกลุ่มอื่น');
  const leg=bands.slice().reverse().map(bd=>`<li>${ppSw(u,bd.key)}<span><b>${esc(bd.label)}</b>${bd.tgt?' (หุ้นนี้)':''}</span><span class="num">${ppSp(bd.vals[0])} → ${ppSp(bd.vals[n-1])}</span></li>`).join('');
  const xl=yrs.map((y,i)=>`<span style="left:${(X(i)/W*100).toFixed(2)}%">CY${y}</span>`).join('');
  return `<div class="pp-st"><div class="pp-sty" aria-hidden="true"><span style="top:0">100%</span><span style="top:50%">50%</span><span style="top:100%">0%</span></div>
    <div class="pp-stp"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(lbl)}">${grid}${polys.join('')}</svg><div class="pp-sll" aria-hidden="true">${labels.join('')}</div></div>
    <div></div><div class="pp-stx" aria-hidden="true">${xl}</div></div><ul class="pp-sleg">${leg}</ul>`;
}

// Top-5 and HHI, first and newest CY; a group of 5 or fewer has no Top-5 worth showing
function ppConc(b){
  const yrs=(b.years||[]).filter(y=>ppNum((b.top5_share||{})[y])); if(!yrs.length)return '';
  const y0=yrs[0], y1=yrs[yrs.length-1], t0=b.top5_share[y0], t1=b.top5_share[y1], h0=(b.hhi||{})[y0], h1=(b.hhi||{})[y1];
  const [arr,txt]=ppMove((t1-t0)*100), dh=ppNum(h0)&&ppNum(h1)?h1-h0:null;
  const w=dh==null?'':dh>=0.005?['↑','กระจุกตัวขึ้น']:dh<=-0.005?['↓','กระจุกตัวลง']:['→','ทรงตัว'];
  const t5=b.n_members>5?`<div><span class="cap">Top-5 รวม CY${y0} → CY${y1}</span><b class="num">${ppSp(t0)} → ${ppSp(t1)}</b><span class="note"><span aria-hidden="true">${arr}</span> ${txt}</span></div>`
    :`<div><span class="cap">Top-5 รวม</span><b>ไม่ใช้</b><span class="note">กลุ่มนี้มี ${esc(b.n_members)} ราย Top-5 จึงเท่ากับทั้งกลุ่ม</span></div>`;
  const hh=dh==null?'':`<div><span class="cap">HHI CY${y0} → CY${y1}</span><b class="num">${h0.toFixed(2)} → ${h1.toFixed(2)}</b><span class="note"><span aria-hidden="true">${w[0]}</span> ${w[1]}</span></div>`;
  return `<div class="pp-conc">${t5}${hh}</div>`;
}

function ppShareTable(v,u){
  const yrs=v.yrs, ms=v.order.concat(v.b.chart.members.filter(m=>!ppNum(m.share[yrs.length-1]))), mid=i=>i>0&&i<yrs.length-1;
  const th=yrs.map((y,i)=>`<th scope="col"${mid(i)?' class="pp-hsm"':''}>CY${y}</th>`).join('');
  const body=ms.map(m=>{
    const cells=m.share.map((s,i)=>`<td class="num${mid(i)?' pp-hsm':''}">${ppNum(s)?ppSp(s):'—'}</td>`).join('');
    const ch=m.matched?`<td class="num">${ppSp(m.share_then)} → ${ppSp(m.share_now)}</td><td class="num">${ppMove(m.change_pp)[1]} ${ppRel(m.share_then,m.share_now)}</td>`
      :'<td class="num">ไม่อยู่ในชุดเทียบ</td><td class="num">—</td>';
    const k=v.pat[m.cik], t=v.isT(m);
    return `<tr class="${t?'pp-target':''}"><td class="num">${v.rank[m.cik]||'—'}</td><th scope="row">${k?ppSw(u,k):''}${esc(v.sym(m))}${t?' (หุ้นนี้)':''}</th><td class="num pp-hsm">${ppMoney(m.revenue_now)}</td>${cells}${ch}</tr>`;
  }).join('');
  return `<button type="button" class="pp-disc" aria-expanded="false" aria-controls="${u}-tab">ดูตารางตัวเลข (${ms.length} ราย)</button>
   <div id="${u}-tab" class="tw" hidden><table class="pp-table pp-shtab"><caption>ส่วนแบ่งรายได้ทุกบริษัทในฐานนี้ เรียงตามส่วนแบ่ง CY${yrs[yrs.length-1]} · คอลัมน์รายปีคิดจากทุกรายที่มีข้อมูลปีนั้น · คอลัมน์ CY${v.w0} → CY${v.w1} คิดจากชุดเทียบ ${v.matchedN||0} ราย · แถบลายเดียวกับกราฟ</caption>
   <thead><tr><th scope="col">อันดับ</th><th scope="col">บริษัท</th><th scope="col" class="pp-hsm">รายได้ CY${yrs[yrs.length-1]}</th>${th}<th scope="col">CY${v.w0} → CY${v.w1}</th><th scope="col">เปลี่ยน (เทียบเดิม)</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

// which basis this view is, and which basis the moat trend's S2 used
function ppBasis(kind,b,x,p){
  const sg=(((x||{}).moat_trend||{}).signals||[]).find(z=>z.name==='share_change'), sub=kind==='sub';
  const base=sub?`ธุรกิจเดียวกัน SIC ${esc(b.sic)} (${esc(b.n_members)} ราย)`:`ทั้งกลุ่ม Damodaran: ${esc(p.industry||'')} (${esc(b.n_members)} ราย)`;
  const why=()=>{const t=ppThai(sg.basis_reason||'');return t?' เพราะ'+esc(t.split('จึงใช้')[0].trim()):sg.basis_reason?' เพราะ '+ppReason(sg.basis_reason):'';};
  const s2=!sg?'ไม่มีสัญญาณ S2':(sg.basis==='sub_group')===sub?'สัญญาณ S2 ของแนวโน้ม moat ใช้ฐานนี้'
    :sg.basis==='industry'?'สัญญาณ S2 ใช้ทั้งกลุ่ม Damodaran แทน'+why():`สัญญาณ S2 ใช้ฐานธุรกิจเดียวกัน SIC ${esc(sg.sic||'')}`;
  const g=(p.industry_share||{}).sub_group, none=!sub&&g&&g.reason?` · ไม่มีมุมมองธุรกิจเดียวกัน: ${esc(g.reason)}`:'';
  return `<p class="pp-basis"><b>ฐานของกราฟ:</b> ${base} · ${s2}${none}${sub?ppSicSrc(p):''}</p>`;
}

function ppSharePanel(v,x,p,u){
  const head=ppBasis(v.kind,v.b,x,p), flags=v.kind==='sub'?ppSubFlags(v.b):'';
  if(v.empty)return `${head}<p class="pp-empty"><b>ไม่มีกราฟส่วนแบ่งสำหรับฐานนี้</b><br>${esc(v.empty)}${v.kind==='sub'?' · กด “ทั้งกลุ่ม Damodaran” เพื่อดูทั้งอุตสาหกรรม':''}</p>${flags}<p class="note pp-cav">${PP_CAVEAT}</p>`;
  const mv=ms=>ms.map(m=>{const [a,t]=ppMove(m.change_pp);return `${esc(v.sym(m))} <span aria-hidden="true">${a}</span> ${t}`;}).join(', ')||'ไม่มี';
  const sk=v.skipped.length?' · ไม่อยู่ในกราฟเพราะไม่มีรายได้ครบสองปี: '+v.skipped.map(m=>`${esc(v.sym(m))} (อันดับ ${v.rank[m.cik]})`).join(', '):'';
  const t=v.rows.find(r=>r.tgt);
  const rk=ppNum(v.tr.rank)?`อันดับ ${v.tr.rank} จาก ${esc(v.tr.of)}`:`ไม่มีรายได้ CY${esc(v.tr.cy||v.w1)} จึงไม่จัดอันดับ`;
  let lead;
  if(t){const [a,tx]=ppMove((t.now-t.then)*100);
    lead=`<b>${esc(v.tkr)} ${rk}</b> <span class="num">· ส่วนแบ่ง ${ppSp(t.then)} → ${ppSp(t.now)} · <span aria-hidden="true">${a}</span> ${tx}<span class="pp-rel"> ${ppRel(t.then,t.now)}</span></span>`;}
  else lead=`<b>${esc(v.tkr)} ${rk}</b> <span class="num">· ไม่อยู่ในชุดเทียบ CY${v.w0} → CY${v.w1} (ไม่มีรายได้ครบสองปี)</span>`;
  const n5=v.b.n_members>5?`5 รายใหญ่สุดของ CY${v.yrs[v.yrs.length-1]} ทุกปี`:`ทุกรายในกลุ่ม (${esc(v.b.n_members)} ราย)`;
  return `${head}<p class="pp-lead1">${lead}</p>
   <div class="pp-ch2">
    <div class="pp-chm"><h4>ใครครองตลาด และหุ้นนี้อยู่ตรงไหน · CY${v.w0} → CY${v.w1}</h4>
     <p class="note pp-lg"><span class="pp-k0" aria-hidden="true"></span> CY${v.w0} <span class="pp-k1" aria-hidden="true"></span> CY${v.w1} · ตัวเลขหลังลูกศร = ส่วนแบ่งที่เปลี่ยน (จุดเปอร์เซ็นต์) · ในวงเล็บ = เปลี่ยนเทียบส่วนแบ่งเดิม · ลายหน้าชื่อ = ลายเดียวกับกราฟขวา</p>
     ${ppDumbbell(v,u)}
     <p class="pp-gl">ช่วงเดียวกัน คู่แข่งที่ได้ส่วนแบ่งมากสุด: ${mv(v.gain)} · เสียมากสุด: ${mv(v.loss)}</p>
     <p class="note">ชุดเทียบ: ${v.matchedN} จาก ${esc(v.b.n_members)} รายที่มีรายได้ทั้ง CY${v.w0} และ CY${v.w1} (ผลรวม 100% ทั้งสองปี)${sk}</p></div>
    <div class="pp-chs"><h4>สัดส่วนรายปี CY${v.yrs[0]}–CY${v.yrs[v.yrs.length-1]} (รวม 100%)</h4>${ppStack(v,u)}
     <p class="note">${n5} · ทุกรายที่มีข้อมูลปีนั้น</p></div>
   </div>${ppConc(v.b)}${flags}<p class="note pp-cav">${PP_CAVEAT}</p>${ppShareTable(v,u)}`;
}

// a panel stored before the chart block: the figures it has, and when the charts arrive
function ppShareOld(kind,b,x,p,tkr){
  const yrs=b.years||[], y=yrs[yrs.length-1], ts=(b.target_share||{})[y];
  const ch=ppNum(b.share_change_pp)?` · เปลี่ยน CY${y-3} → CY${y}: <span aria-hidden="true">${ppMove(b.share_change_pp)[0]}</span> ${ppMove(b.share_change_pp)[1]}`:'';
  return `${ppBasis(kind,b,x,p)}<p class="pp-lead1"><b>${esc(tkr)} ส่วนแบ่ง CY${esc(y)} ${ppSp(ts)}</b><span class="num">${ch}</span></p>
   <p class="pp-empty">กราฟรายบริษัทจะแสดงหลัง refresh รอบหน้า (ข้อมูลชุดนี้สร้างก่อนมีกราฟ)</p>${ppConc(b)}<p class="note pp-cav">${PP_CAVEAT}</p>`;
}

// the share box; its toggle drives this box and the peer table together
function ppShare(p,x,view,u){
  u=u||'ppu'+(++PP_SEQ);
  const s=p.industry_share, tkr=(x||{}).ticker||(((p.table||{}).rows||[]).find(r=>r.is_target)||{}).symbol||'หุ้นนี้';
  const g=ppHasSub(p)?s.sub_group:null, vw=ppViewOf(p,view);
  const b=(v,l)=>`<button type="button" data-pview="${v}" aria-pressed="${vw===v}">${l}</button>`;
  const bar=g?`<div class="seg pp-view" role="group" aria-label="เลือกกลุ่มคู่แข่งที่แสดง (กราฟและตารางคู่แข่ง)">${b('sub',`เฉพาะธุรกิจเดียวกัน (${esc(g.n_members)} ราย)`)}${b('all','ทั้งกลุ่ม Damodaran')}</div>`:'';
  const head=`<div class="pp-shh"><h3>ส่วนแบ่งรายได้ในอุตสาหกรรม</h3>${bar}</div>`;
  const has=s&&s.target_share&&Object.values(s.target_share).some(ppNum);
  if(!has){
    const cov=s?Object.values(s.coverage||{}).filter(ppNum):[];
    const why=!s?'หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran จึงคำนวณส่วนแบ่งไม่ได้'
      :cov.length&&Math.max(...cov)<0.7?`บริษัทในกลุ่ม (${esc(s.n_members)} ราย) มีข้อมูลใน SEC เพียง ${ppPct(Math.max(...cov),0)} ต่ำกว่าเกณฑ์ 70% จึงไม่คำนวณส่วนแบ่ง`
      :'ไม่พบรายได้ของหุ้นนี้ใน SEC frames (เช่น ยื่นงบแบบ 20-F/IFRS หรือใช้ tag รายได้ที่ไม่ได้ดึง) จึงไม่มีส่วนแบ่ง';
    return `<div class="box pp-s12 pp-share">${head}<p class="pp-empty">${why} · ดูรายละเอียดในหมายเหตุข้อมูล</p></div>`;
  }
  const bs=vw==='sub'?g:s, v=ppShareView(bs,vw,tkr);
  return `<div class="box pp-s12 pp-share">${ppDefs(u)}${head}${v?ppSharePanel(v,x,p,u):ppShareOld(vw,bs,x,p,tkr)}</div>`;
}

// R28: where the target's SIC came from when it is not Damodaran's
function ppSicSrc(p){
  const t=(p||{}).target_sic||{};
  return t.source&&t.source!=='damodaran_indname'?` · รหัส SIC ของหุ้นนี้จาก ${esc(t.source)} (คู่แข่งยังเป็นบริษัทที่ยื่น SEC ในอุตสาหกรรมเดียวกัน)`:'';
}
function ppSubFlags(g){
  const f=(g.flags||[]).map(x=>String(x).replace(/^sub-group SIC \d{4}: /,''));
  return f.length?`<ul class="note pp-subflags" style="margin:0">${f.map(x=>`<li>${ppReason(x)}</li>`).join('')}</ul>`:'';
}

const PP_COLS=[['symbol','บริษัท',''],['revenue_ttm','รายได้ TTM',''],['cagr3_fy','โต/ปี (3 ปี)',''],['gross_margin_fy','GM ปีงบ','pp-hsm'],['op_margin_ttm','OPM TTM',''],['ev_sales','EV/Sales',''],['ev_ebit','EV/EBIT','pp-hsm'],['pe','P/E','pp-hsm']];
const PP_FMT={revenue_ttm:ppMoney,cagr3_fy:v=>ppPct(v),gross_margin_fy:v=>ppPct(v),op_margin_ttm:v=>ppPct(v),ev_sales:ppMult,ev_ebit:ppMult,pe:ppMult};
const PP_SORT={};   // card id -> {k, dir}
const PP_VIEW={};   // card id -> 'sub' (same SIC only) | 'all'
// R25: a financial target or row has no gross margin; the cell says so in text, and the
// guard holds even for a stored row that still carries a number
function ppCell(r,c,gm){
  if(c==='gross_margin_fy'&&gm.na)return '—';
  if(c==='gross_margin_fy'&&r.gm_na)return '—<span class="note pp-na"> (ไม่ใช้กับธุรกิจการเงิน)</span>';
  return PP_FMT[c](r[c]);
}
function ppSubRows(p){
  const g=(p.industry_share||{}).sub_group, rows=(p.table||{}).rows||[];
  if(!g||!g.sic||g.reason||!rows.some(r=>'same_business' in r))return null;
  return g;
}
function ppGmNote(p,gm,sub){
  const g=(p.table||{}).gm_compare, n=((p.table||{}).stats||{}).op_margin_ttm, ts=(p.target_sic||{});
  const rest=sub?`ทุกคอลัมน์: median ของคู่แข่ง SIC ${esc(sub.sic)} ที่แสดงในมุมมองนี้ (ไม่รวมหุ้นนี้)`
    :`OPM, การเติบโต, EV/Sales, EV/EBIT, P/E: median ทั้งกลุ่ม Damodaran${n&&ppNum(n.n)?' ('+n.n+' ราย)':''}`;
  if(gm.na)return `GM: ${gm.fin?PP_FIN_TARGET+' · '+PP_GM_NA:esc(gm.reason)} · ${rest}`;
  if(!g)return rest;
  const src=ts.source&&ts.source!=='damodaran_indname'?` · รหัส SIC ของหุ้นนี้จาก ${esc(ts.source)}`:'';
  if(!g.level)return `GM median: ${esc(g.reason)}${ts.reason?' · '+esc(ts.reason):''}${src} · ${rest}`;
  const code=g.level===4?g.sic:String(g.sic).slice(0,3)+'x';
  if(sub)return `${rest}${src}`;
  return `GM median: เทียบเฉพาะ SIC ${esc(code)} ${esc(g.n)} ราย (${(g.symbols||[]).map(esc).join(', ')})${g.level===3?' · SIC 4 หลักเดียวกันมีไม่ถึง 3 ราย จึงใช้ 3 หลัก':''}${src} · ${rest}`;
}
function ppSorted(rows,k,dir){
  return [...rows].sort((a,b)=>{const x=a[k],y=b[k];
    if(k==='symbol')return dir*String(x).localeCompare(String(y));
    if(!ppNum(x))return ppNum(y)?1:0; if(!ppNum(y))return -1; return dir*(x-y);});
}
function ppTable(p,sort,view,x){
  view=view==='sub'?'sub':'all';
  const gm=ppGmState(x||{peer_panel:p});
  const all=(p.table||{}).rows||[], sub=view==='sub'&&ppSubRows(p);
  const rows=sub?all.filter(r=>r.is_target||r.same_business):all;
  if(!rows.length){
    const fl=(p.flags||[]).join(' '), m=fl.match(/(\d+) usable peers \(need (\d+)\)/);
    const why=m?`มีคู่แข่งที่ใช้ได้ ${m[1]} ราย ต้องมีอย่างน้อย ${m[2]} ราย`:/no Damodaran industry/.test(fl)?'หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran':'ข้อมูลคู่แข่งยังไม่ถูกสร้างในรอบ refresh นี้';
    if(all.length)return `<div class="box pp-s12 pp-tbl"><p class="pp-empty">ไม่มีคู่แข่ง SIC ${esc(sub.sic)} ในตารางคู่แข่ง · ทุกรายของธุรกิจนี้อยู่ใน “ดูตารางตัวเลข” ของกล่องส่วนแบ่ง</p></div>`;
    return `<div class="pp-s12 pp-tbl"><p class="pp-empty"><b>ยังไม่มีตารางคู่แข่ง:</b> ${esc(why)}${/no peer list from FMP/.test(fl)?' (รอบนี้ไม่ได้รายชื่อคู่แข่งจาก FMP)':''} · ดูรายละเอียดในหมายเหตุข้อมูล</p></div>`;
  }
  const st=p.table.stats||{}, k=(sort||{}).k||'revenue_ttm', dir=(sort||{}).dir||-1;
  const th=PP_COLS.map(([c,l,cls])=>`<th scope="col" class="${cls}"${k===c?` aria-sort="${dir>0?'ascending':'descending'}"`:''}><button type="button" data-psort="${c}">${l}<span aria-hidden="true">${k===c?(dir>0?' ▲':' ▼'):''}</span></button></th>`).join('');
  const out=r=>r.outlier||[];
  const tr=ppSorted(rows,k,dir).map(r=>`<tr class="${r.is_target?'pp-target':''}">${PP_COLS.map(([c,l,cls],i)=>i===0
      ?`<th scope="row">${esc(r.symbol)}${r.is_target?' <span class="note">(หุ้นนี้)</span>':''}</th>`
      :`<td class="num ${cls}${out(r).includes(c)?' pp-out':''}" data-col="${c}">${ppCell(r,c,gm)}${out(r).includes(c)?'<span aria-hidden="true">*</span><span class="sr"> (นอกกรอบ)</span>':''}</td>`).join('')}</tr>`).join('');
  const ss=sub?(p.table.sub_stats||null):st, ok=c=>!(c==='gross_margin_fy'&&gm.na);
  const med=PP_COLS.map(([c,l,cls],i)=>i===0?`<th scope="row">${sub?'median ธุรกิจเดียวกัน':'median คู่แข่ง'}</th>`:`<td class="num ${cls}" data-col="${c}">${ss&&ss[c]&&ppNum(ss[c].median)&&ok(c)?PP_FMT[c](ss[c].median):'—'}</td>`).join('');
  const few=sub&&ss?PP_COLS.slice(1).filter(([c])=>ok(c)&&ss[c]&&!ppNum(ss[c].median)).map(([c,l])=>`${l} ${ss[c].n} ราย`):[];
  const medTxt=!sub?'':!ss?' · median ของมุมมองนี้ยังไม่ได้คำนวณ (ข้อมูลชุดเก่า กด refresh)'
    :` · median คิดจากคู่แข่ง SIC ${esc(sub.sic)} ที่แสดง (ไม่รวมหุ้นนี้) คอลัมน์ที่มีไม่ถึง 3 รายแสดง —${few.length?' ('+few.map(esc).join(', ')+')':''}`;
  const shown=sub?` · แสดง ${rows.length} จาก ${all.length} แถว (SIC ${esc(sub.sic)} เท่านั้น)${medTxt}`
    :view==='sub'&&ppHasSub(p)?' · ข้อมูลชุดนี้ยังไม่แยกธุรกิจเดียวกัน จึงแสดงทั้งกลุ่ม':'';
  return `<div class="box pp-s12 pp-tbl"><div class="tw"><table class="pp-table"><caption>ตารางคู่แข่ง · กดหัวคอลัมน์เพื่อเรียง${shown}</caption><thead><tr>${th}</tr></thead><tbody>${tr}</tbody><tfoot><tr>${med}</tr></tfoot></table></div>
   <p class="note" style="margin:0">* นอกกรอบ IQR 1.5 เท่า · median ไม่รวมหุ้นนี้ · OPM ใช้ EBIT หลังบวกกลับรายการครั้งเดียว · บนมือถือซ่อน GM, EV/EBIT, P/E</p>
   <p class="note" style="margin:0">${ppGmNote(p,gm,sub)}</p></div>`;
}

function ppNotes(p,m,gm){
  const ev=(m.source_hints||[]).filter(h=>!(gm&&gm.na&&PP_GM_HINTS[h.type])).map(h=>h.evidence?`${h.type}: ${h.evidence}`:'').filter(Boolean);
  const sh=p.industry_share||{}, sub=sh.sub_group||{};
  const u=[...new Set([...(p.flags||[]),...(sh.flags||[]),...(sub.flags||[]),...(m.flags||[]),...ev].map(String))];
  if(!u.length)return '';
  return `<details class="box pp-notes"><summary>หมายเหตุข้อมูล (${u.length})</summary><ul>${u.map(f=>`<li>${ppReason(f)}</li>`).join('')}</ul></details>`;
}

// the whole tab; `id` is the card id (keeps the table's sort between redraws)
function peerPane(x,id){
  x=x||{};
  const p=x.peer_panel||{}, m=x.moat_trend||{}, gm=ppGmState(x), hb=ppHints(m,gm), vw=ppViewOf(p,PP_VIEW[id]);
  return `<div class="pp-grid" data-ppcard="${esc(id||'')}">${ppLead(x)}${ppShare(p,x,vw)}${hb?`<div class="box pp-s12">${hb}</div>`:''}${ppTable(p,PP_SORT[id],vw,x)}
    <div class="pp-s12">${ppNotes(p,m,gm)}<p class="note pp-foot">ข้อมูลจากงบที่ยื่น SEC (10-K/10-Q) และรายชื่ออุตสาหกรรมของ Damodaran · peer panel ณ ${esc(p.as_of||'—')} · ใช้ประกอบการตัดสินใจ ไม่มีผลต่อคะแนน DEEP หรือ Fair Value</p></div></div>`;
}
// a sort button redraws only the table and keeps focus on the same column
function ppSortClick(btn){
  const grid=btn.closest('.pp-grid'), id=grid&&grid.dataset.ppcard, x=id&&CARD_ROWS[id]; if(!x)return;
  const k=btn.dataset.psort, cur=PP_SORT[id]||{k:'revenue_ttm',dir:-1};
  PP_SORT[id]=cur.k===k?{k,dir:-cur.dir}:{k,dir:k==='symbol'?1:-1};
  grid.querySelector('.pp-tbl').outerHTML=ppTable(x.peer_panel||{},PP_SORT[id],ppViewOf(x.peer_panel||{},PP_VIEW[id]),x);
  const nb=grid.querySelector(`[data-psort="${k}"]`); if(nb)nb.focus();
}
// the view toggle redraws the share box and the table, and keeps focus on the pressed button
function ppViewClick(btn){
  const grid=btn.closest('.pp-grid'), id=grid&&grid.dataset.ppcard, x=id&&CARD_ROWS[id]; if(!x)return;
  const v=btn.dataset.pview, p=x.peer_panel||{}; PP_VIEW[id]=v;
  grid.querySelector('.pp-share').outerHTML=ppShare(p,x,v);
  grid.querySelector('.pp-tbl').outerHTML=ppTable(p,PP_SORT[id],ppViewOf(p,v),x);
  const nb=grid.querySelector(`[data-pview="${v}"]`); if(nb)nb.focus();
}
// "ดูตารางตัวเลข": the button's aria-expanded and its region's hidden move together
function ppDiscClick(btn,doc){
  const open=btn.getAttribute('aria-expanded')!=='true', r=doc.getElementById(btn.getAttribute('aria-controls'));
  btn.setAttribute('aria-expanded',String(open)); if(r)r.hidden=!open;
  return open;
}
if(typeof document!=='undefined')document.addEventListener('click',e=>{const t=e.target.closest&&e.target, b=t&&t.closest('[data-psort]'), w=t&&t.closest('[data-pview]'), d=t&&t.closest('.pp-disc');
  if(b)ppSortClick(b); else if(w)ppViewClick(w); else if(d)ppDiscClick(d,document);});
