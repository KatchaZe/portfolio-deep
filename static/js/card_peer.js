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

function ppShare(p,wide){
  const sp=wide?'pp-s12':'pp-s7', s=p.industry_share;
  const has=s&&s.target_share&&Object.values(s.target_share).some(ppNum);
  if(!has){
    const cov=s?Object.values(s.coverage||{}).filter(ppNum):[];
    const why=!s?'หุ้นนี้ไม่มีกลุ่มอุตสาหกรรมของ Damodaran จึงคำนวณส่วนแบ่งไม่ได้'
      :cov.length&&Math.max(...cov)<0.7?`บริษัทในกลุ่ม (${esc(s.n_members)} ราย) มีข้อมูลใน SEC เพียง ${ppPct(Math.max(...cov),0)} ต่ำกว่าเกณฑ์ 70% จึงไม่คำนวณส่วนแบ่ง`
      :'ไม่พบรายได้ของหุ้นนี้ใน SEC frames (เช่น ยื่นงบแบบ 20-F/IFRS หรือใช้ tag รายได้ที่ไม่ได้ดึง) จึงไม่มีส่วนแบ่ง';
    return `<div class="box ${sp}"><h3>ส่วนแบ่งรายได้ในอุตสาหกรรม</h3><p class="pp-empty">${why} · ดูรายละเอียดในหมายเหตุข้อมูล</p>${s?ppSubShare(s.sub_group,p):''}</div>`;
  }
  const yrs=s.years||[], ts=yrs.map(y=>s.target_share[y]), t5=yrs.map(y=>(s.top5_share||{})[y]);
  const vals=[...ts,...t5].filter(ppNum);
  const W=520,H=190,L=44,Rm=30,T=12,B=28, ymax=Math.min(1,Math.ceil(Math.max(...vals)*10)/10+0.05);
  const X=i=>L+(yrs.length>1?i*(W-L-Rm)/(yrs.length-1):(W-L-Rm)/2), Y=v=>T+(1-v/ymax)*(H-T-B);
  const line=(arr,tok,dash)=>{let d='';arr.forEach((v,i)=>{if(!ppNum(v))return;d+=(d&&ppNum(arr[i-1])?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1);});
    return (d?`<path d="${d}" style="fill:none;stroke:var(${tok});stroke-width:2.5${dash?';stroke-dasharray:6 4':''}"/>`:'')+arr.map((v,i)=>ppNum(v)?`<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="3.5" style="fill:var(${tok})"/>`:'').join('');};
  const ticks=[0,ymax/2,ymax].map(v=>`<line x1="${L}" x2="${W-Rm}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" style="stroke:var(--line)"/><text x="${L-6}" y="${(Y(v)+4).toFixed(1)}" text-anchor="end" font-size="11" style="fill:var(--muted)">${Math.round(v*100)}%</text>`).join('');
  const xl=yrs.map((y,i)=>`<text x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="middle" font-size="11" style="fill:var(--muted)">CY${y}</text>`).join('');
  const last=yrs.length-1, hhi=(s.hhi||{})[yrs[last]];
  const lbl=`ส่วนแบ่ง CY${yrs[0]} ถึง CY${yrs[last]}: ${ts.map(v=>ppNum(v)?ppPct(v):'ไม่มี').join(', ')}; Top-5 รวม ${ppPct(t5[last])} ปีล่าสุด`;
  const cov=yrs.map(y=>(s.coverage||{})[y]).filter(ppNum);
  const tgt=(((p.table||{}).rows||[]).find(r=>r.is_target)||{}).symbol||'หุ้นนี้';
  return `<div class="box ${sp} pp-chart"><h3>ส่วนแบ่งรายได้ในอุตสาหกรรม (${esc(p.industry||'')})</h3>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(lbl)}">${ticks}${xl}${line(t5,'--c6',true)}${line(ts,'--accent')}</svg>
    <div class="pp-legend"><span><i style="background:var(--accent)"></i>${esc(tgt)}</span><span><i class="dash"></i>Top-5 รวม</span></div>
    <div class="pp-kfig">
      <div><span class="cap">ส่วนแบ่ง CY${yrs[last]}</span><b>${ppPct(ts[last])}</b></div>
      <div><span class="cap">เปลี่ยน 3 ปี</span><b>${ppNum(s.share_change_pp)?ppSgn(s.share_change_pp,2)+'%':'—'}</b><span class="note">${ppNum(s.matched_n)?'คิดจาก '+esc(ppMatchedTxt(s.matched_n,null,'CY'+(yrs[last]-3)+'..CY'+yrs[last])):'ไม่มีชุดที่มีรายได้ครบสองปี (ดูหมายเหตุข้อมูล)'}</span></div>
      <div><span class="cap">Top-5 รวม</span><b>${ppPct(t5[last])}</b><span class="note">${ppNum(s.top5_change_pp)?ppSgn(s.top5_change_pp,1)+'% ใน 3 ปี':''}</span></div>
      <div><span class="cap">HHI</span><b>${ppNum(hhi)?hhi.toFixed(2):'—'}</b><span class="note">${!ppNum(hhi)?'':hhi>0.25?'กระจุกตัวสูง':hhi>0.15?'กระจุกตัวปานกลาง':'กระจายตัว'}</span></div></div>
    <p class="note" style="margin:0">สัดส่วนรายได้ในบริษัทที่ยื่น SEC ในอุตสาหกรรมเดียวกัน (${esc(s.n_members)} ราย, ครอบคลุม ${cov.length?ppPct(Math.min(...cov),0)+'–'+ppPct(Math.max(...cov),0):'—'}) ไม่ใช่ market share จริง</p>${ppSubShare(s.sub_group,p)}</div>`;
}

// R28: the same figures within the same-SIC sub-group, beside the industry's
function ppSicSrc(p){
  const t=(p||{}).target_sic||{};
  return t.source&&t.source!=='damodaran_indname'?` · รหัส SIC ของหุ้นนี้จาก ${esc(t.source)} (คู่แข่งยังเป็นบริษัทที่ยื่น SEC ในอุตสาหกรรมเดียวกัน)`:'';
}
function ppSubFlags(g){
  const f=(g.flags||[]).map(x=>String(x).replace(/^sub-group SIC \d{4}: /,''));
  return f.length?`<ul class="note pp-subflags" style="margin:0">${f.map(x=>`<li>${ppReason(x)}</li>`).join('')}</ul>`:'';
}
function ppSubShare(g,p){
  if(!g)return '';
  const yrs=g.years||[], y=yrs[yrs.length-1];
  const win=y?`CY${y-3}..CY${y}`:'';
  const both=ppNum(g.matched_n)?`มีรายได้${y?'ทั้ง CY'+(y-3)+' และ CY'+y:'ทั้งสองปี'} ${g.matched_n} ราย ใช้คิดการเปลี่ยนส่วนแบ่ง`:'ไม่มีชุดที่มีรายได้ครบทั้งสองปีให้คิดการเปลี่ยนส่วนแบ่ง';
  const head=g.sic?`ธุรกิจเดียวกัน (SIC ${esc(g.sic)}, ${esc(g.n_members)} ราย · ${esc(both)})`:'ธุรกิจเดียวกัน';
  if(g.reason)return `<h3 class="pp-sub">${g.sic?`ธุรกิจเดียวกัน (SIC ${esc(g.sic)}, ${esc(g.n_members)} ราย)`:head}</h3><p class="pp-empty">${esc(g.reason)}${ppSicSrc(p)}</p>`;
  const ts=(g.target_share||{})[y], t5=(g.top5_share||{})[y], hhi=(g.hhi||{})[y];
  if(!ppNum(ts)&&!ppNum(t5))return `<h3 class="pp-sub">${head}</h3><p class="pp-empty">ไม่มีส่วนแบ่ง CY${esc(y||'')} ในกลุ่มนี้ เพราะ:</p>${ppSubFlags(g)||'<p class="note" style="margin:0">ไม่พบรายได้ของหุ้นนี้ใน SEC frames</p>'}${ppSicSrc(p)?`<p class="note" style="margin:0">${ppSicSrc(p).slice(3)}</p>`:''}`;
  return `<h3 class="pp-sub">${head}</h3>
    <div class="pp-kfig">
      <div><span class="cap">ส่วนแบ่ง CY${esc(y)}</span><b>${ppPct(ts)}</b></div>
      <div><span class="cap">เปลี่ยน 3 ปี</span><b>${ppNum(g.share_change_pp)?ppSgn(g.share_change_pp,2)+'%':'—'}</b><span class="note">${ppNum(g.matched_n)?'คิดจาก '+esc(ppMatchedTxt(g.matched_n,g.n_members,win)):'ไม่มีชุดที่มีรายได้ครบสองปี (เหตุผลด้านล่าง)'}</span></div>
      <div><span class="cap">Top-5 รวม</span><b>${ppPct(t5)}</b></div>
      <div><span class="cap">HHI</span><b>${ppNum(hhi)?hhi.toFixed(2):'—'}</b><span class="note">${!ppNum(hhi)?'':hhi>0.25?'กระจุกตัวสูง':hhi>0.15?'กระจุกตัวปานกลาง':'กระจายตัว'}</span></div></div>
    <p class="note" style="margin:0">เฉพาะบริษัทในอุตสาหกรรมเดียวกันที่มีรหัส SIC 4 หลักเท่ากับหุ้นนี้ (รายชื่อ Damodaran) · กฎเดียวกับทั้งอุตสาหกรรม${ppSicSrc(p)}</p>${ppSubFlags(g)}`;
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
function ppViewBar(p,view){
  const g=ppSubRows(p); if(!g)return '';
  const b=(v,l)=>`<button type="button" data-pview="${v}" aria-pressed="${view===v}">${l}</button>`;
  return `<div class="seg pp-view" role="group" aria-label="เลือกกลุ่มคู่แข่งที่แสดง">${b('sub',`เฉพาะธุรกิจเดียวกัน (${esc(g.n_members)} ราย)`)}${b('all','ทั้งกลุ่ม Damodaran')}</div>`;
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
// D8: where the target sits by newest-CY revenue among the same-business filers
function ppRank(p,g){
  const tgt=(((p.table||{}).rows||[]).find(r=>r.is_target)||{}).symbol||'หุ้นนี้';
  let r=((g.chart||{}).target_rank)||null;
  if(!r){const ms=g.members||[], i=ms.findIndex(m=>(m.symbols||[]).includes(tgt));
    r={rank:i>=0&&ppNum(ms[i].revenue)?i+1:null,of:g.n_members,cy:(ms[0]||{}).cy};}
  return ppNum(r.rank)?`${esc(tgt)} อันดับ ${r.rank} จาก ${esc(r.of)} (รายได้ CY${esc(r.cy||'')})`
    :`${esc(tgt)}: ไม่มีรายได้ CY${esc(r.cy||'')} ใน SEC frames จึงไม่จัดอันดับ (กลุ่มนี้ ${esc(r.of)} ราย)`;
}
function ppMembers(p){
  const g=ppSubRows(p); if(!g)return '';
  const out=(g.members||[]).filter(m=>!m.in_panel);
  const cap=`<p class="note" style="margin:0">คู่แข่งธุรกิจเดียวกันทั้งหมด เรียงตามรายได้ CY · ใช้ดูว่าใครครองตลาดและหุ้นนี้อยู่ตรงไหน · รายได้ทั้งบริษัท ไม่ใช่เฉพาะสายธุรกิจ · นับเฉพาะบริษัทที่ยื่น SEC</p><p class="pp-rank" style="margin:0"><b>${ppRank(p,g)}</b></p>`;
  if(!out.length)return `${cap}<p class="note" style="margin:0">สมาชิกธุรกิจเดียวกันทุกรายอยู่ในตารางคู่แข่งแล้ว</p>`;
  const tr=out.map(m=>`<tr><th scope="row">${(m.symbols||[]).map(esc).join('/')}</th><td class="num">${ppMoney(m.revenue)}</td><td class="num">${ppPct(m.share)}</td></tr>`).join('');
  return `${cap}<div class="tw"><table class="pp-table"><caption>สมาชิกธุรกิจเดียวกันที่ไม่อยู่ในตารางคู่แข่ง ${out.length} ราย · มีเฉพาะรายได้และส่วนแบ่ง CY${esc(out[0].cy||'')} จาก SEC frames (ไม่ใช่ TTM)</caption><thead><tr><th scope="col">บริษัท</th><th scope="col">รายได้ CY${esc(out[0].cy||'')}</th><th scope="col">ส่วนแบ่งในกลุ่ม</th></tr></thead><tbody>${tr}</tbody></table></div>`;
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
    if(all.length)return `<div class="box pp-s12 pp-tbl">${ppViewBar(p,view)}<p class="pp-empty">ไม่มีคู่แข่ง SIC เดียวกันในตาราง</p>${ppMembers(p)}</div>`;
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
  const shown=sub?` · แสดง ${rows.length} จาก ${all.length} แถว (SIC ${esc(sub.sic)} เท่านั้น)${medTxt}`:'';
  return `<div class="box pp-s12 pp-tbl">${ppViewBar(p,view)}<div class="tw"><table class="pp-table"><caption>ตารางคู่แข่ง · กดหัวคอลัมน์เพื่อเรียง${shown}</caption><thead><tr>${th}</tr></thead><tbody>${tr}</tbody><tfoot><tr>${med}</tr></tfoot></table></div>
   <p class="note" style="margin:0">* นอกกรอบ IQR 1.5 เท่า · median ไม่รวมหุ้นนี้ · OPM ใช้ EBIT หลังบวกกลับรายการครั้งเดียว · บนมือถือซ่อน GM, EV/EBIT, P/E</p>
   <p class="note" style="margin:0">${ppGmNote(p,gm,sub)}</p>${sub?ppMembers(p):''}</div>`;
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
  const p=x.peer_panel||{}, m=x.moat_trend||{}, gm=ppGmState(x), hb=ppHints(m,gm);
  return `<div class="pp-grid" data-ppcard="${esc(id||'')}">${ppLead(x)}${ppShare(p,!hb)}${hb?`<div class="box pp-s5">${hb}</div>`:''}${ppTable(p,PP_SORT[id],PP_VIEW[id],x)}
    <div class="pp-s12">${ppNotes(p,m,gm)}<p class="note pp-foot">ข้อมูลจากงบที่ยื่น SEC (10-K/10-Q) และรายชื่ออุตสาหกรรมของ Damodaran · peer panel ณ ${esc(p.as_of||'—')} · ใช้ประกอบการตัดสินใจ ไม่มีผลต่อคะแนน DEEP หรือ Fair Value</p></div></div>`;
}
// a sort button redraws only the table and keeps focus on the same column
function ppSortClick(btn){
  const grid=btn.closest('.pp-grid'), id=grid&&grid.dataset.ppcard, x=id&&CARD_ROWS[id]; if(!x)return;
  const k=btn.dataset.psort, cur=PP_SORT[id]||{k:'revenue_ttm',dir:-1};
  PP_SORT[id]=cur.k===k?{k,dir:-cur.dir}:{k,dir:k==='symbol'?1:-1};
  grid.querySelector('.pp-tbl').outerHTML=ppTable(x.peer_panel||{},PP_SORT[id],PP_VIEW[id],x);
  const nb=grid.querySelector(`[data-psort="${k}"]`); if(nb)nb.focus();
}
// the view toggle redraws only the table and keeps focus on the pressed button
function ppViewClick(btn){
  const grid=btn.closest('.pp-grid'), id=grid&&grid.dataset.ppcard, x=id&&CARD_ROWS[id]; if(!x)return;
  const v=btn.dataset.pview; PP_VIEW[id]=v;
  grid.querySelector('.pp-tbl').outerHTML=ppTable(x.peer_panel||{},PP_SORT[id],v,x);
  const nb=grid.querySelector(`[data-pview="${v}"]`); if(nb)nb.focus();
}
if(typeof document!=='undefined')document.addEventListener('click',e=>{const t=e.target.closest&&e.target, b=t&&t.closest('[data-psort]'), w=t&&t.closest('[data-pview]'); if(b)ppSortClick(b); else if(w)ppViewClick(w);});
