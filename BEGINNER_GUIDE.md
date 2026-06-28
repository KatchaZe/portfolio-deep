# 📘 BEGINNER GUIDE — Portfolio DEEP (คู่มือสำหรับมือใหม่)

> เป้าหมาย: ให้คุณ **รันแอป แก้ไข และอัปเกรด DEEP engine ได้ด้วยตัวเอง** โดยไม่ต้องพึ่งใคร
> Goal: run, modify, and upgrade the app **on your own** — no outside help needed.
>
> คู่มือนี้ใช้ภาษาไทยอธิบาย + โค้ด/คำสั่งเป็นภาษาอังกฤษ (bilingual by design).

---

## 🗺️ 0. ภาพรวมระบบใน 1 นาที (How the app is wired)

ข้อมูลไหลทางเดียวเสมอ — จำลำดับนี้ไว้ จะแก้ตรงไหนก็หาไฟล์เจอ:

```
 sources/      pipeline/                 domain/                 store.py     app.py     index.html
 (ดึงข้อมูล) → (รวม+ตรวจ→FinancialFacts) → (คำนวณ DEEP→Valuation) → (เซฟ JSON) → (API)  → (หน้าจอ)
  fetch          normalize/validate          engine = "สมอง"         disk        HTTP      browser
```

| ชั้น | ไฟล์ | หน้าที่ | ภาษามนุษย์ |
|---|---|---|---|
| sources | `sources/sec_edgar.py` (หลัก) `yahoo.py` `fmp.py` `stooq.py` `finnhub.py` `alphavantage.py` `gdrive_store.py` | ดึงข้อมูลดิบจากเน็ต (SEC=หลัก, ที่เหลือเสริม/สำรอง) | "ไปเอาตัวเลขมา" |
| pipeline | `normalize.py` `validate.py` `consensus.py` `rev_track.py` `margin_track.py` `surprise_backfill.py` `pricecache.py` `risk_prices.py` `refresh.py` | รวมหลายแหล่ง + ตรวจคุณภาพ + orchestrate | "ทำความสะอาด + สั่งงาน" |
| domain | `domain/engine/deep_v82.py` (**active**) · `deep_v73.py` (rollback) · `domain/momentum.py` (สัญญาณหลัก) · `domain/engine/risk.py` (Risk Desk) · `indicators.py` (RSI/MACD เสริม) | **คณิตศาสตร์ DEEP + momentum + risk** | "สมองที่ให้คะแนน" |
| store | `store.py` | เซฟลงไฟล์ `data/portfolio.json` (+ mirror ขึ้น Google Drive) | "ความจำ" |
| api | `app.py` (FastAPI) | รับคำสั่งจากหน้าจอ (รวม `/api/risk`) | "พนักงานรับเรื่อง" |
| ui | `index.html` | หน้าเว็บ 3 แท็บ (Portfolio · Watchlist · Allocation=Risk Desk) | "หน้าร้าน" |

> **เวอร์ชันปัจจุบัน:** engine ที่ใช้งานคือ **DEEP v8.2** (`config.py → DEEP_VERSION="8.2"`); `deep_v73.py` เก็บไว้เพื่อ rollback. สัญญาณ momentum หลักมาจาก `domain/momentum.py` (QuantInsti: MOM_12_1/ROC/SMA200/RSI) — ส่วน `indicators.py` (RSI<30/DBBMV) เป็นแค่ตัวยืนยันรอง.

**กฎเหล็ก 1 ข้อ:** `domain/` ห้ามแตะเน็ตเด็ดขาด (เป็น pure math) → ทำให้ test ได้ง่ายและไม่พัง

---

## ▶️ 1. รันแอปบนเครื่องตัวเอง (Run locally)

ต้องมี **Python 3.9 ขึ้นไป** (เช็คด้วย `python --version`)

### Windows (PowerShell)
```powershell
cd portfolio-deep
pip install -r requirements.txt
$env:FMP_API_KEY="your_key"      # ไม่ใส่ก็ได้ (ถ้าไม่ใส่ จะดึง sector/beta จาก Yahoo แทน)
uvicorn app:app --port 8000
```

### macOS / Linux
```bash
cd portfolio-deep
pip install -r requirements.txt
export FMP_API_KEY=your_key       # optional
uvicorn app:app --port 8000
```

เปิดเบราว์เซอร์ → **http://localhost:8000** เสร็จแล้ว ✅

> 💡 ปิดแอป: กด `Ctrl + C` ในหน้าต่าง terminal

---

## 🧪 2. เช็คว่าโค้ดยังไม่พัง (Run the tests) — ทำทุกครั้งหลังแก้

```bash
python run_tests.py
```
ต้องเห็น **`ALL TEST SUITES PASSED OK`** ตอนจบ ถ้าเห็น `FAILED:` แปลว่ามีบางอย่างพัง
→ อ่านบรรทัดที่ error บอก แล้วย้อนไปดูไฟล์ที่เพิ่งแก้

มี **20 ชุดเทสต์** (ดูรายชื่อเต็มใน `run_tests.py`) แต่ละชุดล็อก "ตัวเลขที่ถูกต้อง" ของหุ้นจริง
(AVGO/ABBV/ORCL/NVO/MSFT) ไว้ ดังนั้นถ้าเผลอทำสูตรเพี้ยน เทสต์จะจับได้ก่อนที่จะ deploy

| ชุดเทสต์ | ตรวจอะไร |
|---|---|
| `test_extract` | SEC อ่านตัวเลขถูกไหม (รายได้/กำไร) |
| `test_engine` / `test_engine_v82` | DEEP v7.3 / **v8.2** คำนวณคะแนน/มูลค่าถูกไหม |
| `test_momentum` | momentum composite (MOM_12_1/ROC/SMA200/RSI) ถูกไหม |
| `test_risk` | Risk engine: weights→1, RC→σp, %RC→100%, DR≥1, score 0–100 |
| `test_no_regression` | `/api/risk` ไม่ไปแก้ `portfolio.json` (feature แยกขาด) |
| `test_app_fixes` | บั๊ก freeze/crash ที่แก้ไปแล้ว กลับมาไหม |
| (อื่นๆ) | earnings, rev/margin track, consensus blend, FMP/SEC parse, stooq, pricecache, gdrive, hardening |

---

## ✏️ 3. การแก้ที่พบบ่อย (Common edits) — ทำตามทีละข้อ

> **ขั้นตอนมาตรฐานทุกครั้ง:** (1) แก้ไฟล์ → (2) `python run_tests.py` → (3) ถ้าผ่าน รันแอปดูผล

### 3.1 เปลี่ยน Equity Risk Premium (ERP)
ตั้งแต่ v8.2 **ERP อยู่ที่เดียวคือ `config.py`** (engine `deep_v82.py` + `validate.py` อ่านค่านี้ค่าเดียวกัน
ผ่าน `config.ERP` — ไม่ต้องแก้หลายไฟล์อีกแล้ว) ค่าปัจจุบัน = **4.23%** (Damodaran implied, ม.ค. 2026):
```python
# config.py — แก้ที่เดียว
ERP = 0.0423          # ← เปลี่ยนค่า ERP ตรงนี้ที่เดียว
ERP_AS_OF = "2026-01" # ← อัปเดตเดือน/ปีที่รีเฟรชด้วย ทุกครั้งที่เปลี่ยน ERP
ERP_STALE_MONTHS = 3  # ← แอปจะขึ้น ⚑ เตือนเมื่อ ERP เก่ากว่า 3 เดือน
```
> 💡 `deep_v73.py` (engine rollback) ยัง hard-code `ERP = 0.0475` ของมันเอง — ปกติไม่ต้องแตะ
> ตราบใดที่ยังใช้ v8.2 อยู่ (ถ้า rollback กลับไป 7.3 ค่อยแก้ในไฟล์นั้น)

### 3.2 เพิ่มหุ้นใหม่เข้า "ตารางอ้างอิง CIK" (ทำให้ SEC เร็วขึ้น)
ถ้าหุนที่ดูบ่อยยังต้องไปค้น CIK ทุกครั้ง ให้ pin ไว้:
```python
# config.py  → dict ชื่อ CIKS
CIKS = {
    "NVDA": "0001045810", ...
    "SOFI": "0001818874",      # ← เพิ่มบรรทัดแบบนี้ (เลข CIK หาได้ที่ sec.gov/cgi-bin/browse-edgar)
}
```
> ไม่เพิ่มก็ได้ — แอปจะไปดึงตาราง CIK เต็มจาก SEC อัตโนมัติ (แค่ครั้งแรกช้านิดเดียว)

### 3.3 ปรับ "อัตรากำไรปลายทาง" ของหุ้นเฉพาะตัว (Reverse DCF แม่นขึ้น)
ใน v8.2 หุ้นที่ **มีกำไรอยู่แล้ว** จะ **anchor กับ operating margin ปัจจุบันของตัวเอง** (จาก SEC, clamp 5–40%)
โดยอัตโนมัติ — ไม่ต้องตั้งค่ามือ. ตาราง `TERMINAL_MARGIN` ตอนนี้เป็น **fallback สำหรับหุ้นที่ยังขาดทุน
(pre-profit) เท่านั้น** (margin ปัจจุบัน ≤ 0); หุ้น pre-profit นอกตารางจะใช้ค่า default 25% แล้วขึ้น ⚑ เตือน:
```python
# domain/engine/deep_v82.py  → dict ชื่อ TERMINAL_MARGIN  (ใช้เฉพาะหุ้น pre-profit)
TERMINAL_MARGIN = {
    "NVDA": 0.35, "MSFT": 0.40, ...
    "SOFI": 0.20,      # ← เพิ่ม op margin ปลายทางที่สมเหตุสมผล (กรณีหุ้นนั้นยังขาดทุน)
}
```

### 3.4 ปรับเกณฑ์ให้คะแนน Demand (ตัวอย่างการแก้ scoring)
ใน v8.2 การให้คะแนนเป็น **rubric แบบมีโครงสร้าง** (base band ตาม growth + ปรับด้วย organic/peer/durability)
อยู่ในฟังก์ชัน `_r_demand(...)` ไม่ใช่ `_demand(g)` เดิมของ v7.3 แล้ว:
```python
# domain/engine/deep_v82.py  → ฟังก์ชัน _r_demand(g, peer_median, acq_intensity, fade_ratio, notes)
base = _band(g, [(0.35, 5.0), (0.25, 4.0), (0.15, 3.0), (0.08, 2.0), (0.0, 1.0), (-1e9, 0.0)])
#                  ↑ แก้ band เกณฑ์ growth/คะแนนตรงนี้ได้เลย (แล้วค่อยปรับ peer/durability ด้านล่าง)
```
> แก้เสร็จ **อย่าลืมรัน test** — ถ้าคะแนนหุ้นใน fixture เปลี่ยนเยอะจน assert ไม่ผ่าน
> ให้ไปอัปเดตค่าที่คาดหวังใน `tests/test_engine_v82.py` ให้ตรงกับสูตรใหม่

### 3.5 เปลี่ยนค่าน้ำหนัก D/E/E/P
```python
# domain/engine/deep_v82.py  (ค่าเดียวกับ v7.3)
WEIGHTS = {"D": 0.20, "E_exec": 0.20, "E_econ": 0.30, "P": 0.30}   # รวมต้อง = 1.0
```

### 3.6 ตั้งรหัสล็อกแอป (ตอน deploy สาธารณะ)
```bash
export APP_TOKEN=ตั้งรหัสอะไรก็ได้ที่เดายาก
```
แล้วเปิดครั้งแรกที่ `http://your-app/?token=รหัสนั้น` → ระบบจะจำด้วย cookie 30 วัน
ไม่ตั้ง `APP_TOKEN` = ไม่มีล็อก (เหมาะกับใช้คนเดียวบนเครื่องตัวเอง)

---

## 🚀 4. อัปเกรด DEEP engine เป็นเวอร์ชันใหม่ (เช่น v8.3)

> ปัจจุบัน active = **v8.2** (`deep_v82.py`) และยังเก็บ **v7.3** ไว้ rollback.
> รายละเอียดเต็มอยู่ใน **`UPGRADE_ENGINE.md`** + บทเรียนจากการอัปจริง v7.3→v8.2 ใน
> **`UPGRADE_ENGINE_REVIEW.md`** — สรุปสั้นๆ 4 ขั้น:

1. **คัดลอกไฟล์ active เป็นไฟล์ใหม่:** `domain/engine/deep_v82.py` → `deep_v83.py`
2. **เปลี่ยนชื่อ class + version:**
   ```python
   class DeepV83Engine(DeepEngine):
       version = "8.3"
       def evaluate(self, facts, rf=0.045):
           ...  # แก้ math ที่ต้องการตรงนี้
   ```
3. **ลงทะเบียน engine ใหม่:**
   ```python
   # domain/engine/__init__.py — เพิ่ม 2 บรรทัด
   from .deep_v83 import DeepV83Engine
   register(DeepV83Engine())
   ```
4. **สลับเวอร์ชันที่ใช้งาน:**
   ```python
   # config.py
   DEEP_VERSION = "8.3"      # ← เปลี่ยนบรรทัดเดียว
   ```

**กฎเหล็ก:** engine ต้อง **รับ `FinancialFacts` เข้า → คืน `Valuation` ออก** เท่านั้น
ตราบใดที่ทำตามสัญญานี้ (ดู `domain/engine/contract.py`) **หน้าจอ/ข้อมูล/store ไม่ต้องแตะเลย**
ของเก่ายังอยู่ → ถ้า v8.3 มีปัญหา แค่เปลี่ยน `DEEP_VERSION` กลับเป็น `"8.2"` (หรือ `"7.3"`) ก็ rollback ทันที

> 💡 อยากให้ DEEP skill เวอร์ชันใหม่ของคุณ (ใน Claude) ออกมาเป็น engine ไฟล์นี้:
> เปิด skill `ifa-stock-analysis` (v8.2) ดูสคริปต์ใน `scripts/` (`wacc.py`, `roic.py`, `rd_capitalize.py`,
> `justified_peg.py`, `reverse_dcf_terminal.py`, `young_company_dcf.py`, `earnings_quality.py`,
> `deep_subscores.py`) — สูตรใน `deep_v82.py` คือ "port" ของสคริปต์เหล่านั้นมาเป็น engine
> เวลาอัปเดต skill → แก้สคริปต์ก่อน → แล้วลอกสูตรมาใส่ `deep_v83.py`

---

## 🆘 5. แก้ปัญหาที่เจอบ่อย (Troubleshooting)

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| `ModuleNotFoundError: fastapi` | ยังไม่ลง dependencies | `pip install -r requirements.txt` |
| หน้าเว็บขึ้น 🔒 unauthorized | ตั้ง `APP_TOKEN` ไว้ | เปิด `/?token=รหัสที่ตั้ง` ครั้งเดียว |
| หุ้นขึ้น ⚑ "yahoo unavailable" | Yahoo บล็อก IP (เจอบ่อยบน Render/cloud) | รันบนเครื่องตัวเอง หรือยอมรับว่า fwd EPS/momentum อาจไม่ครบ |
| ⚑ "Rf fallback" | ดึง 10Y Treasury ไม่ได้ → ใช้ 4.3% | ชั่วคราว รอเน็ต/Yahoo กลับมา แล้ว refresh ใหม่ |
| ⚑ "terminal margin default" | หุ้นไม่อยู่ใน `TERMINAL_MARGIN` | ใส่ค่า margin จริง (ดู 3.3) — หรือปล่อยไว้ (verdict แค่ประมาณ) |
| ตัวเลขหุ้นดูผิดเพี้ยน | tag SEC เปลี่ยน/ข้อมูลแปลก | รัน `python verify.py` ดูว่าตัวไหน CHECK! |
| test `FAILED` หลังแก้สูตร | ค่าคาดหวังใน test ไม่ตรงสูตรใหม่ | อัปเดตตัวเลขใน `tests/test_engine.py` ให้ตรง (ตั้งใจให้สูตรใหม่ถูก) |
| portfolio หาย หลัง redeploy บน Render | ยังไม่ได้เปิด Google Drive persistence | ตั้งค่า OAuth ตาม `GOOGLE_DRIVE_OAUTH_SETUP.md` (ฟรี — ข้อมูล sync ขึ้น Drive อัตโนมัติ ไม่หายอีก) |
| แอปค้างตอน refresh เยอะๆ | (แก้แล้วในเวอร์ชันนี้) network ทำนอก lock | ถ้ายังค้าง = แก้โค้ดผิด ดู `app.py::_fetch_and_commit` |

---

## ✅ 6. Checklist ก่อน commit / deploy ทุกครั้ง

- [ ] `python run_tests.py` → เห็น `ALL TEST SUITES PASSED ✅`
- [ ] รันแอป `uvicorn app:app --port 8000` แล้วเปิดดูจริง ไม่มี error ใน terminal
- [ ] ลองกดทุกปุ่ม: Add holding / Refresh / Daily / Watchlist run / Allocation → Run risk analysis / What-if
- [ ] ถ้า deploy สาธารณะ: ตั้ง `APP_TOKEN` แล้ว (ห้าม commit ค่า token ลง git!)
- [ ] ไม่มีข้อมูลส่วนตัว (email/key) อยู่ในโค้ดที่จะ push (เช็ค `config.py`)

---

*เอกสารเกี่ยวข้อง: `README.md` (ภาพรวม) · `DESIGN.md` (สถาปัตยกรรมเต็ม) · `UPGRADE_ENGINE.md` (อัปเกรด engine ละเอียด) · `REVIEW.md` (ประวัติการแก้บั๊ก)*
