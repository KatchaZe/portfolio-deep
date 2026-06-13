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
| sources | `sources/sec_edgar.py` `yahoo.py` `fmp.py` | ดึงข้อมูลดิบจากเน็ต | "ไปเอาตัวเลขมา" |
| pipeline | `pipeline/normalize.py` `validate.py` | รวม 3 แหล่ง + ตรวจคุณภาพ | "ทำความสะอาดข้อมูล" |
| domain | `domain/engine/deep_v73.py` | **คณิตศาสตร์ DEEP ทั้งหมด** | "สมองที่ให้คะแนน" |
| store | `store.py` | เซฟลงไฟล์ `data/portfolio.json` | "ความจำ" |
| api | `app.py` | รับคำสั่งจากหน้าจอ | "พนักงานรับเรื่อง" |
| ui | `index.html` | หน้าเว็บ 3 แท็บ | "หน้าร้าน" |

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
ต้องเห็น **`ALL TEST SUITES PASSED ✅`** ตอนจบ ถ้าเห็น `FAILED:` แปลว่ามีบางอย่างพัง
→ อ่านบรรทัดที่ error บอก แล้วย้อนไปดูไฟล์ที่เพิ่งแก้

มี 7 ชุดเทสต์ แต่ละชุดล็อก "ตัวเลขที่ถูกต้อง" ของหุ้นจริง (AVGO/ABBV/ORCL/NVO/MSFT)
ไว้ ดังนั้นถ้าเผลอทำสูตรเพี้ยน เทสต์จะจับได้ก่อนที่จะ deploy

| ชุดเทสต์ | ตรวจอะไร |
|---|---|
| `test_extract` | SEC อ่านตัวเลขถูกไหม (รายได้/กำไร) |
| `test_engine` | DEEP คำนวณคะแนน/มูลค่าถูกไหม |
| `test_app_fixes` | บั๊ก freeze/crash ที่แก้ไปแล้ว กลับมาไหม |
| (อื่นๆ) | earnings, revenue track, FMP parse, hardening |

---

## ✏️ 3. การแก้ที่พบบ่อย (Common edits) — ทำตามทีละข้อ

> **ขั้นตอนมาตรฐานทุกครั้ง:** (1) แก้ไฟล์ → (2) `python run_tests.py` → (3) ถ้าผ่าน รันแอปดูผล

### 3.1 เปลี่ยน Equity Risk Premium (ERP)
ERP ล็อกไว้ที่ **4.75%** (ตาม Damodaran) อยู่ **2 ที่ ต้องแก้ให้ตรงกัน**:
```python
# domain/engine/deep_v73.py  (บนสุด)
ERP = 0.0475          # ← เปลี่ยนตรงนี้

# pipeline/validate.py       (บนสุด)
ERP = 0.0475          # ← และตรงนี้ด้วย ให้ค่าเท่ากัน
```

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
หุ้นนอกตารางนี้จะใช้ค่า default 25% ซึ่ง **ผิดสำหรับธนาคาร/fintech** (เช่น SOFI, HOOD)
→ ใส่ค่าจริงเพื่อให้ verdict แม่นขึ้น (แอปจะขึ้น ⚑ เตือนถ้าหุ้นยังไม่อยู่ในตาราง):
```python
# domain/engine/deep_v73.py  → dict ชื่อ TERMINAL_MARGIN
TERMINAL_MARGIN = {
    "NVDA": 0.35, "MSFT": 0.40, ...
    "SOFI": 0.20,      # ← เพิ่ม net margin ปลายทางที่สมเหตุสมผล
}
```

### 3.4 ปรับเกณฑ์ให้คะแนน Demand (ตัวอย่างการแก้ scoring)
```python
# domain/engine/deep_v73.py  → ฟังก์ชัน _demand(g)
def _demand(g):
    if g is None: return None
    return 4.5 if g > 0.40 else 3.5 if g > 0.20 else 3.0 if g > 0.10 else 2.5 if g > 0 else 1.5
    #          ↑ แก้ตัวเลขเกณฑ์/คะแนนตรงนี้ได้เลย
```
> แก้เสร็จ **อย่าลืมรัน test** — ถ้าคะแนนหุ้นใน fixture เปลี่ยนเยอะจน assert ไม่ผ่าน
> ให้ไปอัปเดตค่าที่คาดหวังใน `tests/test_engine.py` ให้ตรงกับสูตรใหม่

### 3.5 เปลี่ยนค่าน้ำหนัก D/E/E/P
```python
# domain/engine/deep_v73.py
WEIGHTS = {"D": 0.20, "E_exec": 0.20, "E_econ": 0.30, "P": 0.30}   # รวมต้อง = 1.0
```

### 3.6 ตั้งรหัสล็อกแอป (ตอน deploy สาธารณะ)
```bash
export APP_TOKEN=ตั้งรหัสอะไรก็ได้ที่เดายาก
```
แล้วเปิดครั้งแรกที่ `http://your-app/?token=รหัสนั้น` → ระบบจะจำด้วย cookie 30 วัน
ไม่ตั้ง `APP_TOKEN` = ไม่มีล็อก (เหมาะกับใช้คนเดียวบนเครื่องตัวเอง)

---

## 🚀 4. อัปเกรด DEEP engine เป็นเวอร์ชันใหม่ (เช่น v7.4)

> รายละเอียดเต็มอยู่ใน **`UPGRADE_ENGINE.md`** — สรุปสั้นๆ 4 ขั้น:

1. **คัดลอกไฟล์เก่าเป็นไฟล์ใหม่:** `domain/engine/deep_v73.py` → `deep_v74.py`
2. **เปลี่ยนชื่อ class + version:**
   ```python
   class DeepV74Engine(DeepEngine):
       version = "7.4"
       def evaluate(self, facts, rf=0.045):
           ...  # แก้ math ที่ต้องการตรงนี้
   ```
3. **ลงทะเบียน engine ใหม่:**
   ```python
   # domain/engine/__init__.py — เพิ่ม 2 บรรทัด
   from .deep_v74 import DeepV74Engine
   register(DeepV74Engine())
   ```
4. **สลับเวอร์ชันที่ใช้งาน:**
   ```python
   # config.py
   DEEP_VERSION = "7.4"      # ← เปลี่ยนบรรทัดเดียว
   ```

**กฎเหล็ก:** engine ต้อง **รับ `FinancialFacts` เข้า → คืน `Valuation` ออก** เท่านั้น
ตราบใดที่ทำตามสัญญานี้ (ดู `domain/engine/contract.py`) **หน้าจอ/ข้อมูล/store ไม่ต้องแตะเลย**
ของเก่า v7.3 ยังอยู่ → ถ้า v7.4 มีปัญหา แค่เปลี่ยน `DEEP_VERSION` กลับเป็น `"7.3"` ก็ rollback ทันที

> 💡 อยากให้ DEEP skill เวอร์ชันใหม่ของคุณ (ใน Claude) ออกมาเป็น engine ไฟล์นี้:
> เปิด skill `ifa-stock-analysis` ดูสคริปต์ใน `scripts/` (roic.py, wacc.py, justified_peg.py,
> reverse_dcf_terminal.py) — สูตรใน `deep_v73.py` คือ "port" ของสคริปต์เหล่านั้นมาเป็น engine
> เวลาอัปเดต skill → แก้สคริปต์ก่อน → แล้วลอกสูตรมาใส่ `deep_v74.py`

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
| portfolio หาย หลัง redeploy บน Render | free tier ลบ disk ทุก deploy | ใช้ Render Disk (paid) mount ที่ `./data` หรือรันเครื่องตัวเอง |
| แอปค้างตอน refresh เยอะๆ | (แก้แล้วในเวอร์ชันนี้) network ทำนอก lock | ถ้ายังค้าง = แก้โค้ดผิด ดู `app.py::_fetch_and_commit` |

---

## ✅ 6. Checklist ก่อน commit / deploy ทุกครั้ง

- [ ] `python run_tests.py` → เห็น `ALL TEST SUITES PASSED ✅`
- [ ] รันแอป `uvicorn app:app --port 8000` แล้วเปิดดูจริง ไม่มี error ใน terminal
- [ ] ลองกดทุกปุ่ม: Add holding / Refresh / Daily / Watchlist run / What-if
- [ ] ถ้า deploy สาธารณะ: ตั้ง `APP_TOKEN` แล้ว (ห้าม commit ค่า token ลง git!)
- [ ] ไม่มีข้อมูลส่วนตัว (email/key) อยู่ในโค้ดที่จะ push (เช็ค `config.py`)

---

*เอกสารเกี่ยวข้อง: `README.md` (ภาพรวม) · `DESIGN.md` (สถาปัตยกรรมเต็ม) · `UPGRADE_ENGINE.md` (อัปเกรด engine ละเอียด) · `REVIEW.md` (ประวัติการแก้บั๊ก)*
