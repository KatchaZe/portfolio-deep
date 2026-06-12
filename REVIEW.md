# Senior Full-Stack Review — Portfolio DEEP v7.3 (v2)

มุมมอง: ประเมินเสมือนเป็น **product ของบริษัทระดับ global ที่มีผู้ใช้จำนวนมาก มูลค่าสูง**
(ไม่ใช่เครื่องมือส่วนตัว) เพื่อหาจุดที่ต้องแก้/ปรับปรุง/optimize โดย **ฟังก์ชันเดิมไม่เสียหาย**

> สรุปผู้บริหาร: คุณภาพ **โดเมน (DEEP math) + การดึงข้อมูล + การแยกชั้น (engine contract)
> อยู่ในระดับดีมาก** มี regression test ครอบของจริง สถาปัตยกรรมสะอาด เปลี่ยนเวอร์ชันได้จริง
> **ช่องว่างหลักไม่ได้อยู่ที่ correctness แต่อยู่ที่ "production hardening สำหรับหลายผู้ใช้"**:
> ไม่มี auth, มี race condition บน store, เขียนไฟล์ไม่ atomic, ไม่มี logging/CI/rate-limit
> ซึ่งจำเป็นถ้าจะรองรับคนจำนวนมาก แต่ยอมรับได้ถ้าใช้ส่วนตัว/เครื่องเดียว

ระดับความรุนแรง: 🔴 Critical (บล็อกการใช้หลายผู้ใช้) · 🟠 High · 🟡 Medium · ⚪ Low

---

## ✅ แก้แล้ว (รอบ hardening — single-user) — มีเทสต์ครอบใน `tests/test_hardening.py`
- **C2** — `store.save()` เป็น atomic (temp + `os.fsync` + `os.replace`) + `store.LOCK` (RLock)
  ครอบทุก mutating endpoint ใน `app.py` (ผ่าน `_run` + watchlist add/remove) → กัน lost-update/ไฟล์พัง
- **H1** — แทน `except: pass` ที่ runtime ด้วย `logging.warning(...)` (refresh/sec_edgar) + ตั้ง logging config
- **H2** — SEC `companyfacts` cache ลงดิสก์ (TTL 12 ชม.) + throttle (`SEC_MIN_INTERVAL`) ; cache CIK map (30 วัน)
- **H4 (บางส่วน)** — เพิ่ม `/healthz` + structured logging
- **M4** — `store.clean_ticker()` กรอง ticker (เคส "ืNVDA") ใช้ทุกจุด add/remove
- **L2/L3/L4** — ลบ import ซ้ำใน `allocation()`, ลบ `//1` no-op, escape HTML (`esc()`) ใน `index.html`

> ยังเหลือ (ต้องตัดสินใจระดับ product): **C1** per-user, **C3** auth, **H3** CI, ย้าย store → DB
> ดูรายละเอียดด้านล่าง

---

## 🔴 Critical — ต้องแก้ก่อนเปิดให้คนจำนวนมากใช้

### C1. ไม่มีการแยกข้อมูลรายผู้ใช้ (single shared store)
- **ปัญหา:** ทั้งระบบใช้ `data/portfolio.json` ก้อนเดียว ไม่มีแนวคิด "user" เลย → ผู้ใช้ทุกคน
  เห็นและแก้ portfolio เดียวกัน (holdings/watchlist ปนกันหมด)
- **ไฟล์:** `store.py`, `app.py` (ทุก endpoint โหลด store เดียว)
- **ผลกระทบ:** เปิดสาธารณะไม่ได้เลย — ข้อมูลการเงินผู้ใช้รั่วข้ามกัน
- **แก้:** เพิ่ม identity (auth) + scope ข้อมูลด้วย `user_id` ทุก query; ย้ายไป DB ที่มี row per user

### C2. Race condition + เขียนไฟล์ไม่ atomic บน store
- **ปัญหา:** ทุก endpoint ทำ `load() -> แก้ -> save()` พร้อม `ThreadPoolExecutor(max_workers=4)`
  โดย **ไม่มี lock** → 2 request พร้อมกัน (เช่น add holding + run daily) เขียนทับกัน = **lost update**
  และ `save()` เขียนทับไฟล์ตรงๆ (`open(w)+json.dump`) → ถ้า crash กลางคัน **ไฟล์พังทั้งก้อน**
- **ไฟล์:** `store.py` (`save`), `app.py` (`_run` + ทุก job)
- **แก้ (ทำได้ทันทีแม้ยังเป็น single-user):**
  - atomic write: เขียน temp แล้ว `os.replace(tmp, PATH)` (atomic บนระบบไฟล์เดียวกัน)
  - ครอบ load→mutate→save ด้วย `threading.Lock` (ระดับ process) หรือ file lock
  - ระยะยาว: ใช้ DB ที่มี transaction แทน JSON
- **หมายเหตุ:** ข้อนี้ควรแก้**แม้ใช้คนเดียว** เพราะ refresh+daily ยิงพร้อมกันได้

### C3. ไม่มี Authentication / Authorization บน endpoint ใดเลย
- **ปัญหา:** URL สาธารณะ (Render) → ใครก็ยิง `GET /api/portfolio` (อ่านพอร์ต) หรือ
  `POST /api/refresh` (เผา FMP quota + ถล่ม SEC/Yahoo จน IP โดนแบน) ได้
- **ไฟล์:** `app.py` (ทุก route)
- **แก้:** ใส่ auth อย่างน้อยระดับ token/login + rate limiting; แยกสิทธิ์ตามผู้ใช้

---

## 🟠 High — ควรแก้เร็ว

### H1. Error ถูกกลืนเงียบ 20 จุด (`except: ...pass`)
- **ปัญหา:** มี `except Exception: pass`/คล้ายกัน **20 จุดใน 8 ไฟล์** (refresh 6, yahoo 7, ฯลฯ)
  → เวลา fetch ล้มเหลว ระบบคืน null/แดงเงียบๆ **debug บน production ไม่ได้** ไม่รู้ว่าพังเพราะอะไร
- **ไฟล์:** `pipeline/refresh.py`, `sources/yahoo.py`, `sources/sec_edgar.py`, `pipeline/normalize.py` ฯลฯ
- **แก้:** ใส่ `logging` (อย่างน้อย `log.warning(exc)`) ในทุก except; เก็บ error ต่อ ticker
  ส่งกลับใน response (มี `errors[]` อยู่บางจุดแล้ว — ทำให้ครบและ log ด้วย)

### H2. SEC fair-access: ไม่มี throttle/backoff ตอน refresh หลายตัว
- **ปัญหา:** `refresh_fundamentals` วน ticker ยิง SEC ติดๆ ไม่มีหน่วงเวลา; SEC ขอ ≤10 req/s +
  UA อธิบายตัวตน → ถ้าถือหุ้นเยอะ refresh ทีเดียวเสี่ยง 403/โดนแบน IP
- **ไฟล์:** `pipeline/refresh.py`, `sources/sec_edgar.py`
- **แก้:** throttle (เช่น เว้น ~0.2s/req) + retry-backoff; **cache `companyfacts` ลงดิสก์แบบมี TTL**
  (งบเปลี่ยนแค่รายไตรมาส ไม่ต้องดึงซ้ำทุก refresh) — ลดทั้งเวลาและความเสี่ยงโดนแบน

### H3. ไม่มี CI (รันเทสต์อัตโนมัติตอน push)
- **ปัญหา:** มีชุดเทสต์ดี (`run_tests.py`) แต่ไม่มี GitHub Actions → regression หลุดขึ้น production ได้
- **แก้:** เพิ่ม `.github/workflows/ci.yml` รัน `python run_tests.py` ทุก push/PR

### H4. ไม่มี logging / observability / healthcheck
- **ปัญหา:** ไม่มี structured log, ไม่มี `/healthz`, ไม่มี error tracking → มองไม่เห็นสุขภาพระบบบน prod
- **แก้:** เพิ่ม `logging` config, endpoint `/healthz`, (ถ้าจริงจัง) Sentry/metrics

---

## 🟡 Medium — ปรับปรุงคุณภาพ/ความทน

### M1. Yahoo: สร้าง session + crumb ใหม่ทุกครั้งที่เรียก
- `fetch_consensus` เรียก `_session()` (ยิง 3 request เปิด session) **ทุกตัวทุกครั้ง** → ช้า + เสี่ยง 429
- **แก้:** cache session/crumb ต่อ process (มี TTL) ใช้ซ้ำ; เพิ่ม retry-backoff
- **ไฟล์:** `sources/yahoo.py`

### M2. resolve_cik โหลดไฟล์ใหญ่ทุก cold start / ทุก worker
- ดาวน์โหลด `company_tickers.json` ทั้งก้อน เก็บใน global ต่อ process → 4 workers = โหลด 4 รอบ ไม่ cache ดิสก์
- **แก้:** cache ลงดิสก์ + TTL (รายเดือนพอ)
- **ไฟล์:** `pipeline/refresh.py`

### M3. Concurrency model จำกัด throughput
- งาน blocking ถูกรันผ่าน `ThreadPoolExecutor(4).result()` ใน sync route → แต่ละ request กิน worker
  ตลอดช่วง IO ภายนอก; refresh ช้าๆ ไม่กี่ตัวทำให้ pool ตัน
- **แก้ (ระยะยาว):** ใช้ `httpx` async + คิวงาน background สำหรับ refresh; แยก read (เร็ว) ออกจาก refresh (ช้า)
- **ไฟล์:** `app.py`

### M4. Input validation ของ ticker หลวม
- เคสจริง `"ืNVDA"` (สระไทยติดหน้า) หลุดเข้าระบบ กลายเป็น key ใน store
- **แก้:** validate `^[A-Z][A-Z.\-]{0,6}$` ที่ server ปฏิเสธตั้งแต่ต้น + strip อักขระไม่ใช่ละติน
- **ไฟล์:** `app.py` (holding/watchlist endpoints), `store.py`

### M5. WACC ใช้แค่ cost of equity (ไม่รวมหนี้)
- `wacc = Rf + β×ERP` ไม่รวม after-tax cost of debt / โครงสร้างทุน → บริษัทมีหนี้มากจะได้ WACC สูงเกินจริง
- **แก้:** ถ้าสกิล ifa-analysis กำหนด WACC แบบ blended ให้ทำตาม (ผ่าน engine version ใหม่)
  **ตรวจกับสกิลก่อนแก้** — อาจเป็น simplification ที่ตั้งใจ
- **ไฟล์:** `domain/engine/deep_v73.py`

### M6. ขาด API-level test + concurrency test
- เทสต์ครอบ logic ดี แต่ไม่มี FastAPI `TestClient` ยิง endpoint, ไม่มีเทสต์ race ของ store, ไม่มีเทสต์ FX ใน normalize
- **แก้:** เพิ่ม smoke test endpoint + เทสต์ atomic save/lock

---

## ⚪ Low — เก็บกวาด/ความสวยงาม

- **L1** `index.html` ฉีด `x.company`/`x.verdict` ผ่าน template literal โดยไม่ escape HTML → ความเสี่ยง
  injection ต่ำ (ข้อมูลจากแหล่งน่าเชื่อ) แต่ควรมี `escapeHTML()` กันชื่อบริษัทที่มี `&`/`<`
- **L2** `pipeline/refresh.py` `allocation()` มี `from sources import fmp` ซ้ำ (import บนหัวไฟล์แล้ว)
- **L3** `_quota` ใช้ `(QUOTA_CAP - used) // 1` (หาร 1 = no-op) — ตัดทิ้งได้
- **L4** ค่าคงที่/threshold กระจาย (±2% earnings, freshness 540 วัน, weights) — ส่วนใหญ่ documented
  ดี; พิจารณารวม tunables ไว้ที่ `config.py` จุดเดียว
- **L5** `index.html` เป็นไฟล์เดียว HTML+CSS+JS — โอเคที่ขนาดนี้ แต่ถ้าทีมโตควรแยกไฟล์ + build step
- **L6** ROIC ใช้ invested capital ณ สิ้นงวด (ไม่ใช่ค่าเฉลี่ย) — แกว่งได้ถ้าทุนเปลี่ยนเร็ว (ผลน้อย)

---

## สิ่งที่ทำได้ "ดีอยู่แล้ว" (ไม่ต้องแตะ)

- แยกชั้นชัด (sources → pipeline → domain → store → api → ui); domain ไม่แตะ network
- **engine versioning ผ่าน contract** — พิสูจน์แล้วว่าสลับ 7.3/7.4 ได้จริง (ดู `UPGRADE_ENGINE.md`)
- regression fixtures ล็อกค่าจริง (AVGO $25B, ORCL, NVO DKK→USD) — กัน data bug กลับมา
- provenance + confidence tier ต่อ field; การ resolve forward-EPS ที่กันค่า unsplit
- earnings/rev track เป็น display/confidence แยกจาก DEEP math (รักษา v7.3 บริสุทธิ์)
- config ผ่าน env (ไม่ hard-code key); `.gitignore` ครอบ `.env` + `data/portfolio.json`

---

## ลำดับแนะนำ (roadmap)

**ถ้าใช้ส่วนตัว/เครื่องเดียว (สถานะปัจจุบัน):** แก้ C2 (atomic save + lock), H1 (logging),
H2 (throttle+cache SEC) ก็เพียงพอและปลอดภัยขึ้นมาก — งานไม่กี่ชั่วโมง ไม่กระทบฟังก์ชัน

**ถ้าจะเป็น product หลายผู้ใช้จริง:** ต้องทำ C1 (per-user) + C3 (auth) + ย้าย store เป็น DB
(Postgres) + H3 (CI) + H4 (observability) — เป็นงานระดับเปลี่ยน foundation (วัน–สัปดาห์)
แต่ตัว **engine/domain/data layer ที่มีอยู่นำไปใช้ต่อได้เลย** เพราะแยกชั้นไว้ดีแล้ว

> ทั้งหมดนี้ปรับได้โดย **ไม่กระทบฟังก์ชันเดิม** (เป็น hardening ชั้นนอก + เปลี่ยน storage backend
> หลัง interface เดิม) ยกเว้น C1/C3 ที่เพิ่ม "ชั้น user" ซึ่งเป็นการ "เพิ่ม" ไม่ใช่ "แก้ของเดิม"
