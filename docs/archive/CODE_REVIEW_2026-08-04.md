# Code Review — 2026-08-04

ขอบเขต: `portfolio-app-v2` (เส้นทาง fair value ทั้งเส้น) + `ifa-stock-analysis-v8/scripts`
เกณฑ์ Damodaran: `Philosophy2026_Summary.md` (ภาคผนวก ก + S5/S12/S19/S20/S21)
ฐาน: `ce59f7c` — *fix(engine): terminal-phase realism + data-integrity guards (build 2026-08-03a)*

---

## สรุปผู้บริหาร

พบและแก้ **28 จุด** (tag `REV-1` … `REV-28` ในโค้ด) — 15 จุดจากรีวิวหลัก, 2 จุดจากงาน
S20 (REV-16/17), 5 จุดจาก backlog (REV-18…22) และ 6 จุดจากช่องว่างที่เหลือ (REV-23…28)
เทสต์ **45 ชุด ผ่านหมด** · จุดหนึ่ง (REV-28) คือบั๊กที่ผมสร้างเองใน REV-3 แล้วมาเจอ
ตอนแก้ REV-24 — และมันเปิดโปง solver ที่ผิดมาตั้งแต่ต้น
ทุกจุดมีรูปร่างเดียวกับที่ build ที่แล้วไล่จับไว้ คือ **ค่าคงที่ที่ใช้กับทุกบริษัท / guard
ที่หายเงียบเมื่ออินพุตขาด / clamp ที่ดันทางเดียว** ที่ยังตกค้างอยู่ในเส้นทางอื่น

| # | ปัญหา | ระดับ | ผลกระทบที่วัดได้ |
|---|---|---|---|
| REV-1 | `f.sbc` ไม่เคยถูก populate — SBC dilution ไม่เคยทำงานเลย | 🔴 สูง | FV ของหุ้น SBC หนัก สูงเกินจริง 12–25% |
| REV-2 | ROIC วัดไม่ได้ → แจกฟรี ROIC 15% + growth 30% | 🔴 สูง | FV −48% ในเคส net-cash |
| REV-8 | ไม่มี fair value → pillar Price หายไป → ได้ **BUY** | 🔴 สูง | comp 3.10 → 4.00 (HOLD → BUY) |
| REV-3 | reverse DCF cap reinvestment ที่ 0.9 — growth ฟรีอีกครั้ง | 🟠 กลาง | implied CAGR ต่ำไป → verdict ใจดีเกิน |
| REV-4 | `spread_prior` ใช้ IC ที่ไม่รวม lease ขณะที่ `spread` รวม | 🟠 กลาง | E_econ (น้ำหนัก 30%) เพี้ยน −1.0 |
| REV-7 | pillar Price ใช้ upside/price ไม่ใช่ MoS = (V−P)/V | 🟠 กลาง | band บนหลวมไป, band ล่างแน่นไป |
| REV-11 | `ke_eff = max(ke, rf+3.5%)` เป็นพื้นทางเดียว | 🟠 กลาง | หุ้น beta ต่ำโดนกด FV ~9% |
| REV-12 | EV ของ reverse DCF ไม่รวม lease ที่ WACC/ROIC รวมไปแล้ว | 🟠 กลาง | EV ขาด $14B, implied CAGR −1.5pp |
| REV-15 | `test_skill_parity` เทียบ path เก่า ไม่ใช่ path production | 🟠 กลาง | drift 21.8% ผ่านการ์ดไปเฉย ๆ |
| REV-5/6/9/10/13/14 | out-of-band เงียบ · solve ซ้ำ 2 เท่า · reinvestment ไม่นับ M&A · guard falsy-zero · inc_ROIC ไม่มีเพดาน · label ผิด | 🟡 ต่ำ | — |

รัน `python run_tests.py` → **ผ่านครบทุกชุด**

---

## 1. ความผิดพลาดจาก code

### REV-1 · `f.sbc` ไม่เคยมีค่า — ทั้ง feature ตายเงียบ 🔴

`FinancialFacts.sbc` ถูกประกาศไว้ และมีผู้ใช้ 1 ราย (`earnings_quality`) แต่
**ไม่มีอะไร set มันเลยบน live path**

```
sources/sec_edgar.py   ไม่มี ShareBasedCompensation
pipeline/normalize.py  ไม่มี "sbc" ใน _MONEY
sources/fmp.py:455     set ให้ — แต่ fmp.parse() ถูกเรียก 0 ครั้งใน pipeline/ + domain/
```

ผลสองชั้น:

1. สาขา `SBC >10% of revenue` ใน `earnings_quality()` เป็น **dead code** — ยิงไม่ได้เลย
2. กฎที่ system prompt เขียนไว้ชัดว่า *"SBC Dilution: Must dilute share count in EPS
   forward projections"* — **ไม่เคยถูก implement** ทั้งสองเส้นทาง (PEG ใช้ `forward_eps`
   ดิบ, FVP ใช้ `eps0` ดิบ) บริษัทที่จ่ายหุ้น 14% ของรายได้ถูกตีมูลค่าเหมือนหุ้นนั้นเสกมาฟรี

**แก้:** ดึง `ShareBasedCompensation` จาก SEC (+ 2 tag สำรอง) · ใส่ `sbc` ใน `_MONEY`
(สำคัญกับ NVO ที่รายงาน DKK) · เพิ่ม `sbc_dilution_rate()` + `dilute()`

```python
# proxy มาตรฐานบนข้อมูลฟรี: ต้นทุนซื้อหุ้นคืนที่ออกไป
annual dilution ≈ SBC$ / market cap        # cap ที่ 6%
forward_eps → หาร (1+d)^1 · eps0 ของ FVP → หาร (1+d)^5
```

| เคส | FV เดิม | FV ใหม่ | dilution |
|---|---|---|---|
| SBC_HEAVY_GROWTH (SBC 14% ของรายได้) | $15.76 | **$13.83** (−12%) | 2.6%/ปี |
| VALUE_DESTROYER | — | — | 6.0%/ปี (ชนเพดาน) |

---

### REV-2 · ROIC วัดไม่ได้ = ได้ค่าที่ใจดีที่สุด 🔴

```python
roic_high = roic_used if (roic_used and roic_used > 0) else ROIC_TERMINAL   # 15%
g_cap     = sustainable_growth_cap(roic_used)   # คืน GROWTH_CAP = 30% เมื่อ ROIC ไม่รู้
```

Docstring เดิมเขียนว่า *"pre-profit names are valued off the reverse DCF, not off this
path"* — **ไม่จริง** บริษัทที่ invested capital ติดลบ (เงินสด > equity + debt ซึ่งเป็นเรื่อง
ปกติของ software/biotech ที่เงินสดหนา) มี NOPAT บวก, FCFF บวก, `roic = None` และลงมาที่
เส้นทางนี้พอดี → ได้ทั้ง ROIC 15% และ growth 30% แบบไม่ต้องพิสูจน์อะไรเลย

นี่คือ default ที่แย่ที่สุดเท่าที่จะเป็นไปได้: **บริษัทที่เราวัดไม่ได้ กลับได้ค่าที่ใจดีที่สุด**

**แก้:** ย้าย `roic_term` ขึ้นมาคำนวณก่อน แล้วใช้เป็น fallback ของทั้งสองตัว
(`roic_term` มีพื้นที่ WACC และเพดานที่ industry อยู่แล้ว)

```
NET_CASH_NO_ROIC:  growth 30.0% → 15.0% · justified PE 30.55 → 16.55
                   FV $39.72 → $20.79 (−48%) · HOLD/Accumulate → HOLD
```

---

### REV-8 · ไม่มี fair value → คำแนะนำ **ดีขึ้น** 🔴

```python
def composite(scores):
    avail = {k: scores[k] for k in WEIGHTS if scores.get(k) is not None}
    wsum = sum(WEIGHTS[k] for k in avail)         # renormalize
```

การ renormalize แปลว่า pillar ที่หายไป **ไม่ถูกลงโทษ — มันถูกลบทิ้ง**

```
D4 · E4 · E4 · P1 (แพง)          → composite 3.10 → HOLD / Accumulate
D4 · E4 · E4 · P หาย (ไม่มี FV)   → composite 4.00 → BUY
```

Yahoo ล่ม → forward EPS หาย → PEG คำนวณไม่ได้ → **คำแนะนำถูกอัปเกรดเป็น BUY**
และยิ่งหุ้นแพงเท่าไหร่ การอัปเกรดยิ่งใหญ่ ขัดกับแก่นข้อ 2 ของทั้งคอร์ส (Price ≠ Value)
โดยตรง — ครึ่ง price ของสมการหายไปแล้วยังตัดสินใจต่อ

**แก้:** `cap_reco_without_price()` — BUY ต้องมีความเห็นเรื่องราคา ถ้าไม่มี ตัดเหลือ
HOLD / Accumulate พร้อม flag

---

## 2. Bug ที่ซ่อนเงียบ — ไล่ทีละขั้นของ pipeline

### ขั้น fetch — `sources/sec_edgar.py`

| จุด | อาการ |
|---|---|
| **REV-1** | `ShareBasedCompensation` ไม่ถูกดึง (ข้างบน) |
| **REV-4** | ดึง `operating_leases` ปีปัจจุบัน แต่ไม่ดึงปีก่อน ทั้งที่ `prior_instant()` มีอยู่แล้ว |
| **REV-19** | `receivables` ถูกดึงมา แต่ **ไม่มีใครใช้** — comment สัญญา "channel-stuffing check" ที่ไม่มีจริง (ปิดแล้ว, หัวข้อ 6) |
| **REV-27** | `pick()` ตัดสินเสมอด้วย `abs(ttm)` ที่ใหญ่กว่า — filer ที่แท็กทั้ง `Revenues` และ `RevenueFromContract...` ได้ตัวที่ใหญ่กว่า ซึ่งอาจรวมรายได้อื่น (ปิดแล้ว, หัวข้อ 8) |

### ขั้น normalize / validate

ส่วนนี้แข็งแรงแล้ว — gate `forward_eps` แบบสองชั้น (S1/S2) ปิดช่องที่เพดาน
revenue-capacity หายไปเมื่อ `shares` ขาดได้ถูกต้อง และ `_annual_cagr` min(3y, 5y) floor 0
ตรงกับที่ Damodaran ว่า past growth พยากรณ์อนาคตได้อ่อน

จุดเดียวที่เหลือ: `sbc` ไม่อยู่ใน `_MONEY` → NVO (DKK) จะเทียบ SBC สกุลหนึ่งกับรายได้อีก
สกุลหนึ่ง (แก้แล้ว)

### ขั้น engine — `domain/engine/deep_v82.py`

**REV-3 · reinvestment cap ที่ 0.9 = growth ฟรีรอบสอง** 🟠

```python
reinvest = min(0.9, max(0.0, x / roic_t)) if x > 0 else 0.0
```

รูปร่างเดียวกับ `max(0, 1 - g/ROIC)` ที่ P-H แก้ไปแล้วในเส้นทาง PEG แต่ตกค้างที่นี่
ROIC terminal 10% แปลว่า **ทุก CAGR ตั้งแต่ 9% ถึง 100% ถูกคิด reinvestment เท่ากันหมด
ที่ 90%** — FCFF หยุดตอบสนองต่อ growth ไปเลย, PV สูงเกิน, solver จึงรายงาน implied
growth **ต่ำกว่า** ที่ราคาเรียกร้องจริง → verdict ใจดีเกินอย่างเป็นระบบ

แก้เป็น 1.0 (เพดานที่ซื่อสัตย์ของบริษัทที่เลี้ยงตัวเอง) + รายงาน `funding_ratio`
(implied CAGR ÷ terminal ROIC) แทนที่จะเงียบ

**REV-4 · lease asymmetry ใน spread trend** 🟠

`ic` รวม lease (P1-5) แต่ `ic_prior` ไม่รวม → บริษัท lease หนักถูกวัดกับฐานทุนอดีตที่เล็ก
กว่าความจริง ROIC ปีก่อนอ่านสูงเกิน เทรนด์จึงแสดง "แย่ลง" ทุกปีทั้งที่ไม่ได้แย่

```
spread ปัจจุบัน            +1.89pp  (IC $38B รวม lease)
spread ปีก่อน AS CODED     +5.48pp  (IC $26B ไม่รวม lease)   → delta −3.58pp → E_econ −1.0
spread ปีก่อน like-for-like +1.06pp                          → delta +0.83pp → E_econ  0.0
```

E_econ ถ่วงน้ำหนัก 30% — โทษเต็ม −1.0 จากบั๊กหน่วยล้วน ๆ
`LEASE_HEAVY_RETAIL: E_econ 0.0 → 1.0 · composite 1.6 → 1.9`

**REV-7 · pillar Price วัด upside ไม่ใช่ margin of safety** 🟠

comment เขียน `margin-of-safety` แต่โค้ดหารด้วย **ราคา**

```python
mos = (fv_anchor - price) / price     # นี่คือ upside (ผลตอบแทนถ้าราคาวิ่งเข้าหามูลค่า)
```

ภาคผนวก ก ของสรุปนิยามไว้ตรง ๆ: `MoS = (Value − Price)/Value` (S4/S12)
การหารด้วยราคาบิด band **แบบไม่สมมาตร**:

| score | เกณฑ์ (V−P)/P | จริง ๆ คือ MoS |
|---|---|---|
| 5.0 | +40% | **+28.6%** ← ถึงง่ายเกิน |
| 4.0 | +15% | +13.0% |
| 3.0 | −15% | −17.6% |
| 2.0 | −40% | **−66.7%** ← แทบไม่มีทางถึง |

pillar 30% จึงเอียงใจดีทั้งสองปลาย แก้เป็น `/fv_anchor` + guard `fv_anchor > 0`
และ `advice.py` เลิกเรียก upside ว่า margin of safety แล้ว (แสดงทั้งสองค่าแยกกัน)

**REV-11 · `ke_eff = max(ke, rf + 3.5%)` — พื้นที่ไม่มีเพดานคู่** 🟠

```
beta 0.24 → Ke  5.57% → 8.00%   ยกขึ้น +2.43pp
beta 0.51 → Ke  6.77% → 8.00%   ยกขึ้น +1.23pp
beta 1.00 → Ke  8.95% → 8.95%   ไม่แตะ
beta 2.21 → Ke 14.33% → 14.33%  ไม่แตะ
```

ปัญหาไม่ใช่แค่ทางเดียว — มัน **ขัดกับ docstring ของตัวเอง** P-B2 เขียนว่า
*"the high-growth years keep the current one"* และ P-K เพิ่ง fade risk ให้ถูกต้อง
สองทางผ่าน `terminal_beta` ไปแล้ว พื้นนี้จึงเป็น fade รอบที่สอง ทางเดียว
และใส่ผิดเฟส (ใส่ที่ช่วง high-growth ที่บริษัท *ยัง* มี beta ต่ำจริง)

ไม่มีความจำเป็นเชิงตัวเลขด้วย: `two_stage_pe` รับ `ke <= g_h` ได้ และการหารด้วย
`(Ke − g)` ทุกจุดใช้ rate ของ stable phase ซึ่งยังคงพื้นไว้
`HIGHQ_LOWBETA: FV $1,480 → $1,612 (+9%)`

**REV-12 · EV ของ reverse DCF ไม่รวม lease**

```python
rdcf = reverse_dcf(..., f.total_debt, ...)     # แต่ WACC/ROIC ใช้ debt_eff
```

โมเดลตัดสินไปแล้ว (P1-5/S5) ว่า lease liability **คือ** หนี้ — มันอยู่ใน WACC weights
และอยู่ใน invested capital การเว้นมันไว้เฉพาะใน EV ทำให้ reverse DCF ตีมูลค่า
"บริษัทคนละตัว" กับที่ WACC คิดราคาให้
`EV $185B → $199B · implied CAGR 28.4% → 29.9%`

**REV-5 · out-of-band ถูกกลืน** — `implied()` คืน `None` เมื่อราคาอยู่นอกแถบ
[−50%, +100%] CAGR แล้วไปรวมกับ verdict `"Unknown"` ที่แปลว่า "ไม่มีรายได้ปีก่อนให้เทียบ"
ซึ่งเป็นคนละเรื่องกันเลย — และเป็น verdict ที่น่าสนใจกว่าด้วย (สคริปต์ skill รายงาน,
แอปไม่รายงาน) ตอนนี้แยกเป็น `out_of_band` + verdict ของตัวเอง

**REV-9 · reinvestment ไม่นับ M&A** — Damodaran S5: reinvestment = capex +
**acquisitions** + ΔWC − D&A · `f.acquisitions_net` ถูกดึงมาแล้ว (ใช้แค่ในโทษ organic
growth) แต่ไม่เข้าสมการ → serial acquirer ดู capital-light เท่าคนที่โตเอง FCFF สูงเกิน

**REV-10 · guard falsy-zero ที่ยังเหลือ** — `if f.net_income and f.shares_diluted`
อ่านปีที่กำไร **เท่าทุนพอดี (0.0)** ว่าเป็นข้อมูลขาด แล้วตกไป `eps_gaap` เงียบ ๆ
รูปเดียวกับ `or 0.08` ที่ REVIEW-1 แก้ไป — ศูนย์คือการวัด ไม่ใช่ช่องว่าง

**REV-13 · `inc_roic` ไม่มีเพดาน** — `d_nopat / (capex − D&A)` ถ้า capex บังเอิญใกล้ D&A
ตัวหารจะ ~0 และได้ "incremental ROIC" หลักพันเปอร์เซ็นต์ที่ไหลเข้า band ของ E_exec ตรง ๆ
clamp ที่ ±200%

**REV-6 · แก้สมการซ้ำ 2 เท่า** — dict comprehension เรียก `implied()` สองครั้งต่อ scenario
(หนึ่งในเงื่อนไข หนึ่งในค่า) = 6 การ solve เต็ม ๆ (60 iterations × 10 ปี) แทนที่จะเป็น 3

**REV-14 · label ผิด** — note เขียน `5y-spread-trend` แต่อินพุตคือ
`operating_income_annuals[1]` + งบดุลปีก่อน = **1 ปี**

---

## 3. เทียบกับหลัก Damodaran

### ✅ ตรงแล้ว

| หลัก | ในโค้ด |
|---|---|
| Ke = Rf + β×ERP, ERP implied refresh รายเดือน | `cost_of_equity()` + flag เมื่อ ERP เกิน 3 เดือน |
| WACC ถ่วงน้ำหนัก + Kd จาก synthetic rating | `wacc_true()` + `SYNTH_SPREAD` |
| Matching rule: FCFF→WACC, FCFE→Ke | reverse DCF ใช้ WACC · PEG/FVP ใช้ Ke |
| g ≤ Rf ใน terminal | `g_stable = min(rf, ke_st − 3%)` |
| Justified PE 2-stage ไม่ใช่ค่าเฉลี่ยลอย ๆ | `two_stage_pe()` ตรงสูตรในภาคผนวก ก |
| Reverse DCF ต้อง discount **ทั้ง path** | `pv_at()` วน 10 ปี + TV |
| Excess return ต้อง fade เข้าหา industry | `terminal_roic()` + ตาราง ROC ของเขาเอง |
| ต้นทุน + ภาษี วัดหลังหัก (S6) | `costs.net_upside()` |
| Lease = หนี้, R&D capitalize (S5) | `debt_eff` + `rd_capitalize()` |

### ⚠️ ยังไม่ตรง (แก้แล้วในรอบนี้)

- **MoS นิยามผิด** → REV-7
- **SBC dilution ไม่ทำงาน** → REV-1
- **growth ต้องซื้อด้วย reinvestment** — ครึ่งเดียว: `sustainable_growth_cap` บังคับ
  g ≤ ROIC บนเส้นทาง PEG แล้ว แต่ reverse DCF ยังปล่อยฟรีเหนือ cap 0.9 → REV-3
- **reinvestment ไม่ครบสูตร** — ขาด acquisitions → REV-9 (ยังขาด ΔWC, ดูข้างล่าง)

### ✅ ช่องว่างที่เหลือ — ปิดครบแล้ว

| หัวข้อ | สถานะ |
|---|---|
| **S20 young company** | ✅ หัวข้อ 7 |
| **ΔWorking capital** | ✅ REV-18 (หัวข้อ 6) |
| `TERMINAL_MARGIN_CAP` | ✅ REV-20 (หัวข้อ 6) |
| `reinvest` clamp ใน fcff gate | ✅ REV-23 (หัวข้อ 8) |
| reverse DCF ตรึง margin ตั้งแต่ปี 1 | ✅ REV-24 (หัวข้อ 8) |
| `price × shares` vs `f.market_cap` | ✅ REV-25 (หัวข้อ 8) |
| `SECTOR_TO_INDUSTRY` ใจกว้างเกิน | ✅ REV-26 (หัวข้อ 8) |
| `pick()` ตัดสินเสมอด้วยตัวเลขที่ใหญ่กว่า | ✅ REV-27 (หัวข้อ 8) |

---

## 4. Parity: แอป ↔ สกิลล์ v8.3

**REV-15 — การ์ดกันดริฟต์เดิมเป็นการ์ดหลอก** 🟠

```python
args = dict(g_h=0.18, n=5, g_st=0.043, ke=0.0895, roic_h=0.20, roic_st=0.15)
pe_app = E.two_stage_pe(**args)          # ← ไม่ส่ง ke_st = ตกไป default เก่า
```

เทสต์เรียก **ทั้งสองฝั่ง** ด้วย signature เก่า จึงเทียบ back-compat default ของแอป
กับสกิลล์ — ผ่านสบาย ขณะที่ production ต่างกันจริง:

```
two_stage_pe ไม่ส่ง ke_st (เส้นทางเทสต์) : 24.484   ← สกิลล์ตรงกับตัวนี้
two_stage_pe ส่ง ke_st  (เส้นทางจริง)    : 29.818   ← สกิลล์ไม่มี argument นี้ด้วยซ้ำ
                                          ต่างกัน 21.8% ของ fair value
```

สกิลล์ตามหลังแอปอยู่ 4 fix: ไม่มี `ke_st` (P-B2), ไม่มี `wacc_term` (P-L),
`roic_stable` default = **0.15** (P-D ตัวที่เป็นต้นเหตุทั้งหมดของ build ที่แล้ว),
ไม่มี `terminal_beta` / `sustainable_growth_cap` เลย

**แก้:**
- port `terminal_beta` / `terminal_roic` / `sustainable_growth_cap` เข้า `justified_peg.py`
- `two_stage_pe(..., ke_st=)` + `fundamental_peg(..., ke_stable=, sbc=, market_cap=)`
- **ลบ default `roic_stable=0.15`** — ถ้าไม่ส่งมา ต้อง derive ผ่าน `terminal_roic()`
- `implied_cagr_fullpath(..., wacc_term=)` + reinvestment cap 0.9 → 1.0
- เทสต์ตอนนี้ยิงทั้ง legacy path **และ** production path พร้อม assert ว่า
  argument ใหม่มีผลจริง (`assert abs(pe_app2 - pe_app) > 1.0`) กันการ์ดหลอกซ้ำ

```
two-stage PE parity OK (legacy 24.4843 / production ke_st 29.8181)
terminal-phase helper parity OK (beta / ROIC / growth cap)
full-path reverse DCF parity OK (legacy 52.6% / production 48.3%)
```

---

## 5. ผลรวม before/after

```
                     FV เดิม → FV ใหม่      composite       หมายเหตุ
NET_CASH_NO_ROIC    $39.72 → $20.79 (−48%)  3.29 → 2.86    HOLD/Acc → HOLD
SBC_HEAVY_GROWTH    $15.76 → $13.83 (−12%)      —          SBC 2.6%/ปี
HIGHQ_LOWBETA      $1480   → $1612  (+9%)       —          ปลดพื้น ke
LEASE_HEAVY_RETAIL  $27.90 → $27.85            1.6 → 1.9   E_econ 0.0 → 1.0
NO_FWD_EPS          $63.55 → $62.43             —          —
BREAKEVEN_YEAR         —   →   —                —          implied CAGR 32.8 → 33.5
```

ทิศทางรวม: **หุ้นที่วัดไม่ได้ / จ่าย SBC หนัก / lease หนัก ถูกกด · หุ้นคุณภาพสูง
beta ต่ำ ถูกปลดโทษที่ไม่ควรโดน** — ตรงกับสิ่งที่ Damodaran ว่าไว้ทุกข้อ

ไฟล์พิสูจน์: `repro_findings.py` (F1–F10) และ `ab_compare.py` (before/after ทั้ง 7 เคส)

---

## 6. Backlog — ปิดครบแล้ว (REV-18 … REV-22)

| # | งาน | สถานะ |
|---|---|---|
| REV-18 | ΔWC เข้า reinvestment | ✅ |
| REV-19 | `receivables` → AR-vs-revenue check | ✅ |
| REV-20 | flag เมื่อ terminal-margin band bind | ✅ |
| REV-21 | คืน implied-vs-actual ให้ pre-profit พร้อม band | ✅ |
| REV-22 | CI pin node (ไม่งั้น render test skip เงียบ) | ✅ |
| — | แสดง young DCF บนการ์ด | ✅ `youngPanel()` |

### REV-18 · ΔWorking capital

Damodaran S5: reinvestment = capex + acquisitions + **ΔWC** − D&A ขาขาไปเหลือแค่ capex
(+ acquisitions ตั้งแต่ REV-9) ขา ΔWC หายไปทั้งขา — บริษัทที่โตต้องเอาเงินก้อนเดียวกัน
ไปจมใน ลูกหนี้/สต็อก พอไม่นับ ต้นทุนของการเติบโตก็ต่ำกว่าความจริง หนักสุดกับธุรกิจ
working-capital heavy (ค้าปลีก, ค้าส่ง, ฮาร์ดแวร์)

**เลือกคำนวณจากงบดุล ไม่ใช่จาก cash-flow tag:**

```
ΔWC = (AR + inventory − AP)_now − (AR + inventory − AP)_prior
```

`IncreaseDecreaseInOperatingCapital` มี sign convention ต่างกันระหว่าง filer — sign
พลิกเงียบ ๆ จะเปลี่ยน "เงินจม" เป็น "เงินไหลเข้า" ซึ่งคือ bug คลาสเดียวกับที่ทั้งรีวิวนี้
ไล่จับอยู่ ส่วน (AR + สต็อก − เจ้าหนี้) อ่านได้แบบเดียว ใช้ tag ladder ชุดเดียวกันทั้ง
ปีปัจจุบันและปีก่อน (`AR_TAGS`/`INV_TAGS`/`AP_TAGS`) จะได้ไม่ดริฟต์ไปคนละ concept แล้ว
ได้ delta ปลอม ๆ · ขาดข้อมูล → คืน `None` ไม่เดา · ขยับเกิน 2% ของรายได้ → ยิง flag

### REV-19 · `receivables` ได้ใช้งานแล้ว

field นี้มี comment `# AR vs revenue (channel-stuffing check)` มาตั้งแต่ v8.2 ดึงจาก SEC
ทุกรอบ refresh และ **ไม่มีใครอ่านเลย** — เขียนไม่ได้เพราะขาด AR ปีก่อน (เพิ่งเพิ่มใน REV-19)
ตอนนี้เข้า `earnings_quality()` แล้ว: AR โตเกิน 10% **และ** เร็วกว่ารายได้เกิน 15pp
→ flag "sales not turning into cash" (เกณฑ์สองชั้นกันสัญญาณหลอกจาก noise ปกติ)

### REV-20 · terminal-margin band ประกาศตัวแล้ว

`[5%, 40%]` เป็นค่าคงที่ตัวสุดท้ายที่ยัง bind เงียบ ๆ เป็น clamp สองด้านก็จริง แต่ผลไม่
สมมาตร: เพดาน 40% กดบริษัทซอฟต์แวร์ margin 55% ลงมา ทำให้ reverse DCF เรียกร้อง growth
มากขึ้นและ verdict อ่านแข็งเกินจริง ส่วนพื้น 5% ไปอุ้มธุรกิจ margin บาง ตอนนี้บอกทั้ง
สองทาง (`CAPPED 55% -> 40%` / `FLOORED 2% -> 5%`) — ยังคงตัว band ไว้ เพราะการยืด margin
วันนี้ไปตลอดกาลควรมีเบรก แต่เบรกที่เปลี่ยนคำตอบต้องพูด

### REV-21 · implied vs actual กลับมาแล้ว (ตามที่ขอ) — พร้อม band

**ตรวจที่มาก่อน ตามที่สั่ง:**

| ตัวเลข | ที่มา | เชื่อได้แค่ไหน |
|---|---|---|
| `actual_1y` | `revenue_annuals[0]/revenue_annuals[1] − 1` จาก SEC XBRL | **ของจริง 100%** ไม่มีสมมติฐาน |
| `implied_cagr` | bisection บน EV = PV(FCFF path) + PV(TV) · price/shares/revenue/debt(รวม lease)/cash/WACC/tax/ROIC = ค่าจริงทั้งหมด | จริงหมด **ยกเว้น terminal margin** |

หุ้นขาดทุนไม่มี margin ให้วัด → `terminal_margin()` ตกไปใช้ตารางหรือ generic 25%
และมันคือตัวที่ swing แรงที่สุด:

```
margin 15% → implied 21.7%      margin 30% → implied 10.9%
margin 20% → implied 18.3%      margin 35% → implied  5.4%
margin 25% → implied 15.7%      margin 40% → implied  0.1%

เดา margin ผิด ±5pp  ⇒  implied CAGR ขยับ 7.4pp  (ตัวเลขเองแค่ 15.7%)
```

**สรุป: สูตรและอินพุตเชื่อถือได้ แต่ตัวเลขเดี่ยวไม่ควรเชื่อ** จึงคืนกลับมาแบบนี้ —
`pricedInLine()` แสดง implied CAGR **คู่กับช่วง ±5pp เสมอ** + ป้าย "margin สมมติ ⓘ"
(hover เห็นที่มา) + บรรทัดเทียบกับที่ทำได้จริง

และมันจะ **ไม่หายไปอีกแล้วตอน young DCF ได้เป็น anchor** — ซึ่งคือสิ่งที่หายไปก่อนหน้านี้
เพราะ `valueGauge` สลับไปใช้ gauge ปกติ สองอย่างนี้ตอบคนละคำถามและควรอยู่ด้วยกัน:

> young DCF = *"มันควรมีค่าเท่าไหร่"* (valuation, S20)
> reverse DCF = *"ตลาดคิดราคาไว้ที่การโตเท่าไหร่"* (pricing, S19/S21)

ซึ่งตรงกับที่ reference เขียนไว้เองว่า *"worth $X (1.7); price assumes Y% CAGR (1.6)"*
เทสต์ `test_young_panel_render` assert ว่าบล็อกนี้ต้องรอดทั้งเคส promoted และ blocked
และห้ามโชว์ implied เดี่ยวโดยไม่มี band

### REV-22 · CI

`.github/workflows/tests.yml` มีอยู่แล้วและรัน `run_tests.py` ทุก push/PR — แต่ setup
เฉพาะ python ส่วน `test_young_panel_render` ต้องใช้ node และมันออกแบบให้ **skip เงียบ**
เมื่อไม่มี node (จะได้ไม่ fail เครื่องที่ไม่มี) ซึ่งบน CI แปลว่าอาจไม่เคยรันเลย
เพิ่ม `setup-node@v4` + ขั้น `node --version` ให้ fail ทันทีถ้าหาย

---

## 7. REV-16 — Young-Company DCF (S20) เข้าแอปแล้ว

### ปัญหาเดิม

หุ้น pre-profit ได้ `anchor_value = None` และไม่มี Price pillar สิ่งเดียวบนการ์ดคือ
reverse DCF ซึ่ง docstring ของตัวเองเขียนไว้ว่า *"this is a PRICING cross-check, NOT a
valuation"* — มันตอบว่าตลาดคิดราคาไว้ที่การเติบโตเท่าไหร่ ไม่เคยตอบว่ามันควรมีค่าเท่าไหร่
แอปจึงไม่มีความเห็นเรื่องความถูกแพงกับหุ้นกลุ่มที่ความเห็นมีค่าที่สุด

### วิธี (`domain/engine/young_dcf.py`)

```
value/share = p_survival × going_concern + (1 − p_survival) × distress     ← S20
reinvestment_t = (rev_t − rev_{t−1}) / sales_to_capital                    ← growth ซื้อด้วยเงิน
```

**failure risk อยู่นอก discount rate เสมอ** — Damodaran เรียก discount rate ว่า
"blunt instrument" สำหรับงานนี้ `failure_adjusted()` แยกเป็นฟังก์ชันของตัวเองเพื่อให้
ยุบกลับเข้า WACC ทีหลังไม่ได้ (มีเทสต์กันไว้)

### อินพุตทุกตัว derive จากข้อมูลที่ยื่นจริง

| อินพุต | ที่มา |
|---|---|
| `sales_to_capital` | revenue ÷ invested capital · ถ้า IC ≤ 0 → revenue ÷ (assets − cash) · clamp [0.4, 6.0] **และบอกเสมอเมื่อ clamp bind** |
| `p_survival` | cash runway (cash ÷ burn) + premium ตาม scale — ไม่มีตัวเลขที่เดาเอง |
| `distress_per_share` | max(0, cash − debt) ÷ shares — สมมติว่ากิจการมีค่าศูนย์ เหลือแค่เงินสดสุทธิ |
| `annual_dilution` | SBC ของบริษัทเอง (ต่อยอดจาก REV-1) |
| `g_high` | consensus next-FY revenue ก่อน → SEC CAGR เป็น fallback |
| `wacc` / `roic_stable` | ตัวที่ engine คำนวณอยู่แล้ว |

สมมติฐานที่หลีกเลี่ยงไม่ได้มีตัวเดียวคือ `target_margin` (บริษัทขาดทุนไม่มี margin ให้
extrapolate) — ใช้ `terminal_margin()` ตัวเดิม, flag ว่าเป็นสมมติฐาน, และ Monte Carlo ±6pp

### เกณฑ์ promote (ตามที่คุณเลือก — ตัวเลือกที่ 3)

Monte Carlo 1000 sims (seeded — refresh ซ้ำต้องได้เลขเดิม) แล้ว **p50 จะขึ้นเป็น anchor
ก็ต่อเมื่อเรารู้จริง**:

```
p50  > 0
p10  > 0                      ← downside ไม่ใช่การสูญเสียทั้งหมด
p90/p10 < 4.0x                ← ไม่ใช่สมมติฐานที่ขับคำตอบ
```

ไม่ผ่าน → ยังคำนวณและโชว์ แต่ `anchor_value` เป็น None เหมือนเดิม พร้อมเหตุผลที่**แยก
สามกรณีออกจากกัน** (เดิมข้อความเดียวครอบหมด ซึ่งพูดผิดเวลา band แคบแต่ติดลบ):

| กรณี | ข้อความ |
|---|---|
| p50 ≤ 0 | "the model values the equity at or below zero across the band" |
| p10 ≤ 0 | "the downside case is a total loss" |
| band ≥ 4x | "spans Nx — the assumptions, not the company, are driving the answer" |

### ผลลัพธ์

```
YOUNGCO (รายได้ $2.1B, ขาดทุน, runway 2.6y, SBC 4.5%/ปี)
  anchor      : Young-Company DCF (failure-adjusted) $20.24   [เดิม: None]
  range       : $15.32 – $27.03   (band 1.76x → ผ่านเกณฑ์)
  P pillar    : 2.0               [เดิม: None]
  going concern $27.96 → failure-adjusted $20.35 (survival 68%, distress $4.17)
  terminal    : 80.1% ของมูลค่า
  verdict     : HOLD ★★½☆☆ - Pre-profit forward DCF $20.24 (-28% upside); band
                $15.32-$27.03, survival 0.68, distress $4.17. Market prices ~5.7% 10y CAGR.

WIDECO (รายได้ $0.35B, เผาเงินเร็ว)
  promote     : False — "values the equity at or below zero (p50 $-0.66)"
  anchor      : Terminal-Anchored Reverse DCF / None   ← ถอยไปพฤติกรรมเดิมอย่างถูกต้อง
```

ต้นทุน: **~40ms ต่อ ticker** เฉพาะหุ้น pre-profit (Monte Carlo 1000 sims × 10 ปี)

### REV-17 — bug ที่โผล่ตามมาจาก REV-2

`terminal_roic()` มีกฎ 3 ว่า *"cost of capital คือพื้น"* แต่สาขา `roic_now is None`
**ข้ามพื้นนั้นไป** — บังคับใช้เฉพาะตอนที่รู้ ROIC เท่านั้น

เดิมพอทนได้เพราะสาขานั้นป้อนแค่ perpetuity แต่ REV-2 เพิ่งทำให้ growth cap และ
`roic_high` วิ่งผ่านมันด้วย พื้นจึงต้องยืนทุกที่ ไม่งั้นบริษัท WACC สูงที่วัด ROIC ไม่ได้
จะถูกสมมติว่า **ทำลายมูลค่าถาวรตลอดกาล** ซึ่ง Damodaran ไม่ได้โมเดลแบบนั้น
(ธุรกิจแบบนั้นถูก restructure / ขาย / ปิด)

เทสต์เดิมปักพฤติกรรมผิดนี้ไว้ (`terminal_roic(None, 0.08, 0.0553) == 0.0553`)
อัปเดตแล้วพร้อมเหตุผล — และเพิ่ม assert ว่าสองสาขาต้องให้คำตอบเดียวกัน

### เทสต์

`tests/test_young_dcf.py` — 8 เคส: p_survival จาก runway (monotonic), sales-to-capital
ทั้งสาม source + clamp ที่ต้องประกาศตัว, failure risk ต้องอยู่นอก discount rate,
terminal reinvestment cap, band gate ทั้งสองทาง, blocked_reason แยกกรณี,
determinism, และหุ้นกำไรต้องไม่ถูกแตะ

`tests/test_skill_parity.py` — เพิ่ม `test_young_company_dcf_parity()` ตรึง
going-concern build ของแอปกับสกิลล์ให้ตรงกันถึง 1e-6

### หน้าการ์ด (`index.html`, build `2026-08-04a`)

`youngPanel(x)` วางอยู่ในบล็อกมูลค่าของ `pcard()` แสดงสามชั้น:

1. **แถบ band** — distress floor · p10 – p90 (แถบม่วง) · p50 (ขีดเข้ม) · ราคาปัจจุบัน (ขีดดำ)
   สเกลครอบตั้งแต่ค่าต่ำสุดถึงสูงสุดของทั้งชุด ทำให้เห็นทันทีว่าราคาอยู่ตรงไหนของช่วงมูลค่า
2. **โซ่ S20** — `ถ้ารอด $27.96 × 68% + ถ้าไม่รอด $4.17 × 32% = $20.35`
   เขียนสมการออกมาตรง ๆ เพื่อให้เห็นว่า failure risk เป็นความน่าจะเป็นแยก ไม่ได้ยัดเข้า discount rate
3. **ป้าย survival + inputs** — hover ที่ป้ายเห็นที่มาของทุกอินพุต (growth จากไหน,
   sales-to-capital คำนวณยังไง, runway กี่ปี, terminal กี่ % ของมูลค่า)

ตัวที่ไม่ผ่านเกณฑ์ได้ป้าย **"ข้อมูลเสริม"** สีเหลือง + เหตุผลใต้แถบ และช่อง Upside
เขียนว่า **"ตีมูลค่าไม่ได้"** แทนขีด `—` เปล่า ๆ (เดิมหุ้นที่ไม่มี upside กับหุ้นที่
ตีมูลค่าไม่ได้ แสดงเหมือนกันทุกประการ)

**`tests/test_young_panel_render.py`** — `test_frontend` พิสูจน์ได้แค่ว่า token อยู่ในไฟล์
ซึ่งไม่เคยจับ panel ที่ throw หรือพ่น `undefined` ได้เลย เทสต์นี้ดึงฟังก์ชันออกจาก
`index.html` จริง ๆ (brace-matching) รันใน node กับ payload ที่ engine สร้าง แล้ว assert
บน HTML ที่ได้กลับมา: ตัวเลข p10/p50/p90 ต้องปรากฏ, ตัวที่ผ่านเกณฑ์ต้องไม่มีป้าย
"ข้อมูลเสริม", หุ้นกำไรต้องไม่มี panel, และห้ามมี `undefined`/`NaN`/`[object Object]`
โผล่ที่ไหนเลย บวกเคส payload ไม่ครบ 4 แบบที่ต้องไม่ throw

รวม **45 ชุด ผ่านหมด**

---

## 8. REV-23 … REV-28 — ปิดช่องว่างที่เหลือ

รอบนี้มีจุดหนึ่งที่ **ผมทำพังเองในรอบก่อน แล้วมาเจอตอนแก้ตัวอื่น** — เขียนไว้ตรง ๆ ที่ REV-28

### REV-25 · EV ใช้ market cap คนละตัวกับ WACC

`reverse_dcf` คำนวณ `mcap = price × shares` ใหม่เองข้างใน ทั้งที่ engine เลือก
`f.market_cap` (T4) ไปแล้วเพราะสำหรับ ADR การคูณ price กับจำนวนหุ้น SEC ได้หน่วยผิด
→ reverse DCF ตีมูลค่า "บริษัทคนละตัว" กับที่ WACC คิดราคาให้ และ `_resolve_shares`
เข้าแทรกก็ต่อเมื่อต่างกันเกิน 20% เท่ากับ 20% แรกผ่านไปเงียบ ๆ แก้โดยส่ง `mcap` เข้าไป

### REV-23 · FCFF gate ที่ยิงไม่ออกตลอดกาล

```python
reinvest = _clamp((capex + acq + dWC - D&A) / nopat, 0.0, 0.8)
fcff = nopat * (1 - reinvest)
```

เพดาน 0.8 แปลว่า `fcff` **อย่างน้อย 20% ของ NOPAT เสมอ** → `fcf_pos` เป็นจริงทุกครั้ง
ที่ NOPAT เป็นบวก ไม่ว่าบริษัทจะเผาเงินแค่ไหน gate นี้มีไว้จับบริษัทที่ลงทุนเกินกำไร
แต่ clamp รับประกันว่ามันจะไม่มีวันจับได้ — ปลดเพดาน (คงพื้นที่ 0) + flag เมื่อเกิน 100%

### REV-24 · reverse DCF ตรึง margin ที่ปลายทางตั้งแต่ปีแรก

docstring เดิมอ้างว่าทดสอบแล้วขยับ <0.6pp — จริงสำหรับบริษัทที่ margin วันนี้ใกล้ปลายทาง
อยู่แล้ว แต่หุ้น **pre-profit** มี margin ติดลบวันนี้ ขณะที่ terminal margin เป็นค่าสมมติ 25%
เท่ากับโมเดลยกให้บริษัทที่ขาดทุนอยู่มี margin 25% ตั้งแต่ปีหน้า → PV สูงเกิน → solver
ต้องการ growth น้อยลง → verdict ใจดีเกินกับกลุ่มที่ตัดสินยากที่สุดพอดี

ตอนนี้ ramp เชิงเส้นจาก margin วันนี้ไปปลายทาง ซึ่งเป็นสิ่งที่ `young_dcf` ทำอยู่แล้ว —
สองโมเดลเลิกเถียงกันเรื่อง 10 ปีข้างหน้าของบริษัทเดียวกัน

### REV-26 · sector map หยาบ ๆ ไม่ใช่มุมมองรายอุตสาหกรรม

FMP มี 11 sector แบบ GICS ส่วน Damodaran มี ~90 อุตสาหกรรม การ map "healthcare"
ทั้งก้อนไปที่ Drugs (Pharmaceutical) 23.52% เท่ากับยกเพดานของมุมที่ทำกำไรดีที่สุด
ให้ทุก ticker ที่ไม่ได้ map ไว้ — รูปเดียวกับ "ค่าคงที่ใช้กับทุกคน" ที่ไล่จับมาทั้งรีวิว
ตอนนี้ hit จาก sector map ถูก cap ที่ค่าเฉลี่ยตลาด (14.92%) → กดลงได้ แต่แจกไม่ได้
ส่วน mapping ราย ticker ที่ระบุไว้ชัดไม่กระทบ

### REV-27 · `pick()` ตัดสินเสมอด้วยตัวเลขที่ใหญ่กว่า

```python
key = (le, abs(v))     # freshest, then LARGER |TTM|
```

freshness ถูกแล้ว (นั่นคือ fix เดิมสำหรับ AVGO ที่ย้าย tag) แต่ tie-break ด้วยขนาด
คือ "การชอบเลขใหญ่" ที่แต่งตัวเป็นกฎ — filer ที่แท็กทั้ง `Revenues` (ยอดรวมแบบเดิม)
และ `RevenueFromContractWithCustomerExcludingAssessedTax` (ตัวหลักตาม ASC 606)
จะได้ตัวที่ใหญ่กว่า ซึ่งอาจรวมรายได้อื่น → รายได้สูงเกิน → margin ทุกตัวต่ำเกิน และ
ใน reverse DCF ฐานรายได้ที่ใหญ่ขึ้นทำให้ implied CAGR ต่ำลง verdict ใจดีเกิน

list ในไฟล์เรียงตามลำดับความเฉพาะเจาะจงอยู่แล้ว → ใช้ลำดับใน list เป็น tie-break
และรายงานเมื่อ concept สองตัวคลุมงวดเดียวกันแต่ต่างกันเกิน 5% **จับได้ทันทีจาก fixture จริง:**

```
ORCL  SEC tag conflict - net income: used NetIncomeLoss (16.21B)
      but NetIncomeLossAvailableToCommonStockholdersBasic covers the same period at 11.32B
```

### REV-28 · bug ที่ผมสร้างเองใน REV-3 — และ solver ที่ผิดมาตลอด

REV-3 เปลี่ยนเพดาน reinvestment จาก 0.9 → 1.0 โดยให้เหตุผลว่า 1.0 คือ "เพดานที่ซื่อสัตย์
ของบริษัทที่เลี้ยงตัวเอง" **ผิด** — ที่ x = ROIC พอดี interim FCFF กลายเป็น **ศูนย์**
และคงศูนย์ตลอด แปลว่าเหนือจุดนั้นโมเดลเป็น terminal value ล้วน 100% FCFF ยังไม่ตอบสนอง
ต่อ growth เหมือนเดิม และ margin ramp ของ REV-24 ไม่มีอะไรให้ ramp เลย
**เพดานไม่ได้ลบความแบน มันแค่ย้ายที่**

```
ROIC 15%:   x=15% → reinv 1.00 → interim PV  0.000B
            x=20% → reinv 1.00 → interim PV  0.000B     ← แบนสนิท
            x=30% → reinv 1.00 → interim PV  0.000B
```

ความจริงคือ **ไม่มีเพดานที่ซื่อสัตย์** growth เกินกว่าที่ ROIC หาเงินได้ต้องจ่ายด้วย
**ทุนจากภายนอก** ซึ่งเป็นกระแสเงินสดออกจริง FCFF ต้องติดลบได้ (โมเดล young company
ของ Damodaran ก็เป็นแบบนั้น) → ปลดเพดานทิ้ง

**แต่การปลดเพดานเปิดโปงบั๊กที่ลึกกว่าและอยู่มานานกว่า:** พอ growth ถูกคิดเงินจริง
`pv_at` ไม่ได้เพิ่มขึ้นตลอด — มัน **unimodal** คือขึ้นตราบใดที่ผลตอบแทนจากการโตยังชนะ
ต้นทุน แล้วถึงจุดสูงสุด แล้วลง เพราะบิลค่า reinvestment แซงกำไร แต่ `implied()` ใช้
bisection บนช่วง `[-50%, +100%]` โดย**สมมติว่า monotonic** ผลคือบริษัทที่ spread บาง
(ROIC 10% vs WACC 9% ซึ่งคือประเด็นหลักของ S5 เรื่อง growth ไร้ค่าเมื่อไม่มี spread)
คืน "out of band" ทุกราคา เพราะ `pv_at(+100%)` ติดลบหนักสำหรับพวกมัน

แก้เป็น: หา**จุดสูงสุด**ก่อน แล้ว bisect เฉพาะ**ขาขึ้น** → ได้รากที่มีความหมาย คือ
*growth น้อยที่สุดที่ทำให้ราคาวันนี้สมเหตุสมผล* ถ้าจุดสูงสุดยังไม่ถึง EV แปลว่า
**ไม่มี growth ไหนอธิบายราคานี้ได้** ซึ่งเป็นคำตอบที่ต่างจาก "ต้องโตเกิน 100%/ปี"
คนละเรื่อง จึงแยกข้อความกัน

```
ROIC 10% vs WACC 9% (แทบไม่มี spread)      ROIC 30% (มี moat จริง)
  $12 → implied  -2.2%                       $ 12 → -4.9%
  $20 → ไม่มี growth ไหนอธิบายได้            $ 45 → 14.1%
  $45 → ไม่มี growth ไหนอธิบายได้            $200 → 35.7%  (Aggressive)
```

ตัวขวา monotonic เพิ่มตามราคา ตามที่ควรเป็น · ตัวซ้ายคือคำตอบที่ถูกต้องตาม S5:
บริษัทที่ ROIC ≈ WACC โตแล้วไม่สร้างมูลค่า จึงไม่มีทางอธิบายราคาแพงด้วยการเติบโต

**ผลรวมต่อ portfolio (7 เคสทดสอบ):** implied CAGR สูงขึ้นทุกตัวที่ยังแก้ได้
(NET_CASH 23.9→30.2%, SBC_HEAVY 32.3→37.9%, VALUE_DESTROYER 17.5→23.5%) เพราะตอนนี้
ราคาต้องจ่ายค่า growth จริง ๆ · และสองเคสที่ spread บาง (LEASE_HEAVY_RETAIL,
BREAKEVEN_YEAR) เปลี่ยนเป็น "ไม่มี growth ไหนอธิบายราคานี้ได้" ซึ่งตรงกว่าเดิม

**parity guard จับ drift ได้ทันที:** ตอนพอร์ต solver ใหม่เข้าแอปแล้วยังไม่ได้พอร์ตเข้า
สกิลล์ เทสต์ฟ้องที่ราคา $500 (สกิลล์ None / แอป 72.8%) — ตอนนี้ assert ว่าสองฝั่งต้อง
เห็นตรงกัน **รวมถึงตอนที่ราคาอธิบายไม่ได้** ข้ามช่วงราคา 4 จุด
