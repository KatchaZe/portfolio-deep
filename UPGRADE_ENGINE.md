# คู่มือ: อัปเกรด DEEP engine เป็นเวอร์ชันใหม่ (เช่น 7.4, 7.5)

คู่มือนี้สอนวิธีนำ **ifa-analysis skill เวอร์ชันใหม่** มาอัปเดตเข้ากับ dashboard นี้
แบบทำเองทีละขั้น — ออกแบบมาให้คนเขียนโค้ดมือใหม่ทำตามได้

---

## หลักการสำคัญ (อ่านก่อน 1 นาที)

dashboard นี้ออกแบบให้ "math ของ DEEP" ถูกแยกออกมาอยู่หลัง **contract** (สัญญา)
ตัวเดียว → เวลาเปลี่ยนเวอร์ชัน framework **คุณแก้แค่ไฟล์ engine ไฟล์เดียว** ส่วนอื่น
(การดึงข้อมูล, store, API, หน้าจอ) ไม่ต้องแตะเลย

```
ข้อมูลเข้า  ──►  [ ENGINE = math ของ DEEP ]  ──►  ผลลัพธ์ออก
FinancialFacts        (deep_v73.py / v74)          Valuation
   ▲ ส่วนนี้คงเดิม          ▲ แก้แค่ตรงนี้            ▲ ส่วนนี้คงเดิม
```

> กฎเหล็ก: **engine ต้องรับ `FinancialFacts` เข้า และคืน `Valuation` ออก เท่านั้น**
> ตราบใดที่ยังทำตามสัญญานี้ หน้าจอกับข้อมูลจะทำงานได้เหมือนเดิม

ไฟล์ที่เกี่ยวกับ engine ทั้งหมดอยู่ใน `domain/engine/`:

| ไฟล์ | หน้าที่ | ต้องแตะไหม |
|---|---|---|
| `contract.py` | นิยาม `Valuation` ( output) + `DeepEngine` (แม่แบบ) | ❌ ไม่แตะ (ดูเฉยๆ) |
| `deep_v73.py` | math เวอร์ชัน 7.3 ปัจจุบัน | ❌ เก็บไว้ (ของเก่า/rollback) |
| `deep_v74.py` | **ไฟล์ใหม่ที่คุณจะสร้าง** | ✅ สร้างใหม่ |
| `__init__.py` | ทะเบียน engine (registry) | ✅ เพิ่ม 2 บรรทัด |
| `../../config.py` | บรรทัด `DEEP_VERSION` | ✅ เปลี่ยน 1 บรรทัด |

---

## สิ่งที่ engine ต้องรับเข้า / คืนออก (สัญญา)

**รับเข้า:** `evaluate(self, facts, rf=0.045)`
- `facts` = `FinancialFacts` (ดูฟิลด์ทั้งหมดใน `domain/facts.py`) เช่น
  `facts.revenue`, `facts.operating_income`, `facts.net_income`, `facts.shares_diluted`,
  `facts.total_debt`, `facts.cash`, `facts.equity`, `facts.beta`, `facts.forward_eps`,
  `facts.growth_lt`, `facts.revenue_annuals`, `facts.tax_rate` (property), …
- `rf` = อัตราผลตอบแทนพันธบัตร 10 ปี (decimal เช่น 0.045)

**คืนออก:** ต้องเป็น object `Valuation` ที่ใส่ฟิลด์เหล่านี้ให้ครบ (หน้าจอใช้ทุกตัว):

| ฟิลด์ | คือ | ตัวอย่าง |
|---|---|---|
| `version` | เลขเวอร์ชัน | `"7.4"` |
| `D`, `E_exec`, `E_econ`, `P` | คะแนนย่อย 0–5 | `4.5` |
| `composite` | คะแนนรวมถ่วงน้ำหนัก | `4.28` |
| `stars` | ดาว | `"★★★★½"` |
| `recommendation` | BUY / HOLD / Accumulate / SELL | `"BUY"` |
| `signal` | BUY / HOLD / SELL (ใช้กับ daily action) | `"BUY"` |
| `anchor_method` | วิธีตั้ง fair value | `"Justified PEG"` |
| `anchor_value` | fair value ต่อหุ้น (None ได้ถ้า pre-profit) | `223.65` |
| `range_low`, `range_high` | ช่วง fair value | `223.65`, `290.04` |
| `fv_peg`, `fv_fvp` | fair value แต่ละวิธี | `103.58` |
| `reverse_dcf` | dict ผล reverse DCF | `{...}` |
| `key_metrics` | dict: `wacc_pct, roic_pct, spread_pct, growth_pct, beta` | `{...}` |
| `verdict` | ข้อความสรุป 1 บรรทัด | `"BUY ★★★★½ — ..."` |
| `flags` | list คำเตือน | `[]` |

> ถ้าเวอร์ชันใหม่มีตัวเลขใหม่ที่อยากโชว์ ให้ **เพิ่มฟิลด์ใหม่** ใน `contract.py`
> (เพิ่มอย่างเดียว ห้ามลบ/เปลี่ยนความหมายของเดิม) แล้วค่อยไปโชว์ใน `index.html`

---

## ขั้นตอนทีละสเต็ป (อัปเกรด 7.3 → 7.4)

### สเต็ป 0 — เทียบว่า "อะไรเปลี่ยน" ในสกิลใหม่
เปิดสกิล ifa-analysis เวอร์ชันใหม่ แล้วจดว่ามีอะไรต่างจาก 7.3 บ้าง โดยปกติจะเป็น
อย่างใดอย่างหนึ่ง (หรือหลายอย่าง):
- **ค่าคงที่เปลี่ยน** เช่น ERP จาก 4.75% → 5.0%, growth cap, terminal ROIC
- **น้ำหนักคะแนนเปลี่ยน** เช่น Demand/Execution/Economics/Price weights
- **สูตรเปลี่ยน** เช่น วิธีคิด WACC, PEG, Future Value Projection, Reverse DCF
- **เพิ่มวิธี/เกณฑ์ใหม่** เช่น เพิ่ม anchor method, เปลี่ยนเงื่อนไขเลือก anchor

> เคล็ดลับมือใหม่: ทำเป็น checklist "ของเก่า → ของใหม่" ก่อนเริ่มแก้โค้ด จะไม่หลง

### สเต็ป 1 — ก็อปไฟล์ engine เดิมเป็นไฟล์ใหม่
ใน `domain/engine/` ก็อป `deep_v73.py` → ตั้งชื่อ `deep_v74.py`

PowerShell:
```powershell
cd "C:\Users\Katcha\Documents\Claude\Projects\Stock Screening\portfolio-app-v2"
Copy-Item domain\engine\deep_v73.py domain\engine\deep_v74.py
```

### สเต็ป 2 — เปลี่ยนชื่อ class + เลขเวอร์ชัน ในไฟล์ใหม่
เปิด `domain/engine/deep_v74.py` แก้ 2 จุด:
```python
class DeepV74Engine(DeepEngine):     # เดิม: class DeepV73Engine
    version = "7.4"                   # เดิม: version = "7.3"
```

### สเต็ป 3 — แก้ math ตามสกิลใหม่
แก้เฉพาะส่วนที่สเต็ป 0 จดไว้ ตัวอย่างที่เจอบ่อย:

ถ้าแค่ **ค่าคงที่เปลี่ยน** — แก้ตัวแปรด้านบนไฟล์ เช่น
```python
ERP = 0.050          # เดิม 0.0475
GROWTH_CAP = 0.28    # เดิม 0.30
```

ถ้า **น้ำหนักคะแนนเปลี่ยน** — แก้ dict `WEIGHTS`
```python
WEIGHTS = {"D": 0.15, "E_exec": 0.25, "E_econ": 0.30, "P": 0.30}
```

ถ้า **สูตรเปลี่ยน** — แก้ในฟังก์ชันที่เกี่ยวข้อง (เช่น `wacc()`, `justified_peg()`,
`future_value_projection()`, `reverse_dcf()`) ให้ตรงกับสูตรในสกิล

> ห้ามลืม: ตอนจบ method `evaluate()` ยังต้อง `return Valuation(...)` ที่ใส่ฟิลด์ครบ
> (ดูตารางข้างบน) ถ้าเพิ่มตัวเลขใหม่ ให้เพิ่มฟิลด์ใน `contract.py` ก่อน

### สเต็ป 4 — ลงทะเบียน engine ใหม่ (registry)
เปิด `domain/engine/__init__.py` แล้ว **เอา comment ออก / เพิ่ม 2 บรรทัด**:
```python
from .deep_v73 import DeepV73Engine
from .deep_v74 import DeepV74Engine     # <-- เพิ่มบรรทัดนี้

register(DeepV73Engine())
register(DeepV74Engine())               # <-- เพิ่มบรรทัดนี้
```
(เก็บ v7.3 ไว้ด้วย เพื่อสลับกลับ/เทียบได้)

### สเต็ป 5 — สลับเวอร์ชันที่ใช้งาน (1 บรรทัด)
เปิด `config.py` แก้:
```python
DEEP_VERSION = "7.4"     # เดิม "7.3"
```
เท่านี้ทั้งระบบจะใช้ engine 7.4 อัตโนมัติ

### สเต็ป 6 — เพิ่มเทสต์ (กันพลาด)
เปิด `tests/test_engine.py` แล้วเพิ่มเช็คเวอร์ชันใหม่ (ก็อปบล็อกเดิม เปลี่ยนเวอร์ชัน)
อย่างน้อยให้เทสต์ว่า engine 7.4 รันบน fixtures ได้และคืน `Valuation` ครบฟิลด์
ไม่พัง — ดูตัวอย่างรูปแบบใน `tests/test_engine.py` ที่มีอยู่

### สเต็ป 7 — รันเทสต์ + ดูจริงบนเครื่อง
```powershell
python run_tests.py
uvicorn app:app --port 8000
```
เปิด http://localhost:8000 → กด **Run Fundamental Refresh** → ตรวจว่า:
- มุมหัวตารางขึ้น `v7.4`
- DEEP★, anchor, verdict ดูสมเหตุสมผล (ลองหุ้นที่คุ้น เช่น NVDA/MSFT)
- ไม่มีช่องว่าง/พัง

### สเต็ป 8 — commit + deploy
```powershell
git add domain/engine/deep_v74.py domain/engine/__init__.py config.py tests/test_engine.py
git commit -m "feat: DEEP engine v7.4"
git push
```
Render จะ auto-deploy (ตามที่ตั้ง On Commit) แล้วหน้า live จะใช้ 7.4

---

## การสลับกลับ (rollback) — ทำได้ใน 5 วินาที
ถ้า 7.4 มีปัญหา แค่กลับ `config.py` เป็น `DEEP_VERSION = "7.3"` → commit → push
ของเก่ายังอยู่ครบ ไม่ต้องลบอะไร

## อยากเทียบ 7.3 vs 7.4 ก่อนตัดสินใจ
เพราะลงทะเบียนทั้งคู่ไว้แล้ว คุณเรียกเทียบใน Python ได้:
```python
from domain.engine import get_engine
v73 = get_engine("7.3").evaluate(facts, rf)
v74 = get_engine("7.4").evaluate(facts, rf)
print(v73.composite, v74.composite, v73.anchor_value, v74.anchor_value)
```

---

## กรณีพิเศษ: สกิลใหม่ต้องใช้ "ข้อมูลใหม่" ที่ของเดิมไม่มี

ถ้า 7.4 ต้องใช้ตัวเลขที่ตอนนี้ยังไม่ได้ดึง (เช่น free cash flow margin, R&D, backlog)
แค่แก้ engine ไม่พอ — ต้องเติมข้อมูลด้วย ทำตามลำดับนี้:

1. **`domain/facts.py`** — เพิ่มฟิลด์ใหม่ใน `FinancialFacts` (เช่น `rnd_expense`)
2. **`sources/sec_edgar.py`** (หรือ `fmp.py`/`yahoo.py`) — ดึงค่านั้นมา
3. **`pipeline/normalize.py`** — ใส่ค่าลง facts (`ff.set("rnd_expense", ..., "sec")`)
4. **`pipeline/validate.py`** — (ถ้าจำเป็น) เพิ่ม sanity check
5. แล้วค่อยใช้ `facts.rnd_expense` ใน `deep_v74.py`

> วิธีรู้ว่าเข้ากรณีนี้ไหม: ถ้าสูตรใหม่ในสกิลอ้างถึงตัวเลขที่ **ไม่มีใน `domain/facts.py`**
> แปลว่าต้องเติมข้อมูลก่อน (สเต็ป 1–4 ข้างบน) ถ้าใช้แต่ตัวเลขเดิม ก็แก้แค่ engine พอ

---

## เช็กลิสต์สั้น (ปริ้นท์ไว้ข้างจอ)

- [ ] จดของเก่า→ของใหม่จากสกิล (สเต็ป 0)
- [ ] ก็อป `deep_v73.py` → `deep_v74.py` (สเต็ป 1)
- [ ] เปลี่ยน `class DeepV74Engine` + `version = "7.4"` (สเต็ป 2)
- [ ] แก้ math ตามสกิล (สเต็ป 3)
- [ ] ถ้ามีตัวเลขใหม่จะโชว์ → เพิ่มฟิลด์ใน `contract.py`
- [ ] ถ้าต้องใช้ข้อมูลใหม่ → แก้ facts + source + normalize ก่อน
- [ ] register ใน `__init__.py` (สเต็ป 4)
- [ ] `DEEP_VERSION = "7.4"` ใน `config.py` (สเต็ป 5)
- [ ] เพิ่ม/อัปเดตเทสต์ (สเต็ป 6)
- [ ] `python run_tests.py` ผ่าน + ดูบน localhost (สเต็ป 7)
- [ ] commit + push (สเต็ป 8)
- [ ] อัปเดตเอกสาร: `config.py` comment, `DESIGN.md`, `README.md`, `architecture.mermaid`

---

## ตัวอย่างจริง (เดโม่: สร้าง v7.4 จำลอง เปลี่ยน ERP 4.75% → 5.50%)

นี่คือผลจากการไล่ทำตามคู่มือนี้จริงทุกสเต็ป (บนสำเนาทดสอบ) เพื่อยืนยันว่ากลไกทำงาน:

```text
# สเต็ป 1-3
cp domain/engine/deep_v73.py domain/engine/deep_v74.py
#  - class DeepV73Engine  -> class DeepV74Engine
#  - version = "7.3"      -> version = "7.4"
#  - ERP = 0.0475         -> ERP = 0.0550

# สเต็ป 4 (__init__.py)
from .deep_v74 import DeepV74Engine
register(DeepV74Engine())

>>> available_versions()
['7.3', '7.4']
```

ผลเทียบ 7.3 vs 7.4 (WACC = Rf + β×ERP จึงเพิ่มทุกตัวเมื่อ ERP สูงขึ้น):

| หุ้น | WACC 7.3 → 7.4 | composite 7.3 → 7.4 | anchor FV 7.3 → 7.4 |
|---|---|---|---|
| MSFT | 9.69% → 10.51% | 2.87 → 2.87 | $335.99 → $335.99 |
| NVO  | 6.16% → 6.42%  | 3.94 → 3.94 | $53.06 → $53.06 |
| ORCL | 11.83% → 12.99% | 2.71 → **2.41** | $38.77 → **$37.51** |

อ่านผล: ORCL เห็นชัดสุด — WACC สูงขึ้น → discount แรงขึ้น → fair value ลด → คะแนนลด
(MSFT/NVO ใช้ anchor แบบ Justified PEG ที่ไม่อิง WACC โดยตรง เลยขยับน้อยจนปัดเศษกลืน —
เป็นพฤติกรรมที่ถูกต้องตามสูตร ไม่ใช่บั๊ก)

```text
# สเต็ป 5: เปิดใช้งานจริง
DEEP_VERSION = "7.4"   ->  get_engine() คืน 7.4 ทั้งระบบ
# rollback
DEEP_VERSION = "7.3"   ->  get_engine() คืน 7.3 (ของเก่าไม่หาย)
```

> หมายเหตุ: เดโม่นี้เปลี่ยนแค่ค่าคงที่ตัวเดียวให้เห็นภาพ ของจริงเวลาอัปเกรดให้แก้ math
> ตามที่สกิล ifa-analysis เวอร์ชันใหม่กำหนด (สูตร/น้ำหนัก/เกณฑ์) — ขั้นตอนเหมือนกันเป๊ะ
