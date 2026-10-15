# Senior Full-Stack Code Review — portfolio-app-v2 (build 2026-07-02a)

ผู้ตรวจ: Claude (senior full-stack review) · วันที่ 2026-07-02
ขอบเขต: ทุกไฟล์ใน `portfolio-app-v2` (app, store, pipeline, domain, sources, frontend, deploy) + สแกน `ifa-stock-analysis-v8/scripts`

> **UPDATE (build 2026-07-02b):** แก้แล้ว 4 ข้อ — ✅ C2 (drive_pull atomic), ✅ C1 (Drive push ย้ายไป background worker นอก LOCK + client timeout 30s), ✅ C3 (cookie `secure=True`), ✅ H1 (SEC stale-cache fallback + flag) พร้อมเทสใหม่ `tests/test_sec_stale_cache.py` และอัปเดต `tests/test_gdrive.py` (wait_push) — test suite ทั้งหมดผ่าน
>
> **UPDATE (build 2026-07-03a):** bug จาก production 2 ตัว — ✅ Yahoo `surprisePercent` เป็นเศษส่วน (0.041=4.1%) ไม่ใช่ % ทำให้ beat จริงถูกเกรดเป็น meet → parser คำนวณ % จาก act/est เองเป็นหลัก; ✅ FMP free tier: `limit` เกิน 5 ทำให้ /earnings และ /analyst-estimates ตอบ 402 (สาเหตุ EPS/Rev = n/a บน Render ที่ Yahoo ถูกบล็อก) → clamp limit≤5 + สลับ price-history ให้ stable มาก่อน legacy (403 สำหรับบัญชีหลัง ส.ค. 2025) + เทสใหม่ `tests/test_earnings` (fraction regression), `tests/test_fmp_freetier.py` + เครื่องมือ `diagnose_sources.py`, `diagnose_fmp.py`
>
> **UPDATE (build 2026-07-02d):** ปิดครบทุกข้อที่เหลือ — ✅ P1 (parallel fetch: fundamentals/watchlist/daily ผ่าน ThreadPool 4 workers + quota partition ล่วงหน้า), ✅ P2 (Yahoo session/crumb cache ต่อ process), ✅ P3 (margin_track คำนวณครั้งเดียว/row), ✅ P4 (ลบ dead code ตาราง portfolio+watchlist), ✅ P5 (FMP shared Session + fetch_profile รวมศูนย์), ✅ H4 (reset Drive file-id เมื่อ 404), ✅ H5 (โน้ต single-worker ใน render.yaml/app.py), ✅ minors (frontend try/catch, esc tooltip, clean_series เส้นสั้น, QUOTA_CAP เข้า config, pin google libs) พร้อมเทสใหม่ `tests/test_parallel_fetch.py` — test suite เต็มผ่านทั้งหมด
>
> **UPDATE (build 2026-07-02c):** ✅ H2 (quota cost/ticker = 5 ตามจำนวน FMP call จริง), ✅ H3 (price cache short-circuit เฉพาะเมื่อยาว ≥ full_bars; partial cache ไม่บล็อกซีรีส์ยาวอีก แต่ก็ไม่เผา FMP ซ้ำในวันเดียวกัน — free tier เท่านั้นที่ retry; เส้น 5y ใน fetch_daily ขอ 780 แท่ง ทำให้ S9 reversal (756 แท่ง) ทำงานจริง) พร้อมเทสใหม่ `tests/test_price_fullbars.py` (5 เคส) — test suite ทั้งหมดผ่าน

## ผลการ Verify ก่อนรีวิว

- รัน test suite เต็ม (30 ชุด, offline): **ผ่านทั้งหมด** ✅
- รันเทสของโค้ด v8.3 ที่แก้ล่าสุด (advice, gdrive data-loss guard, earn_status): **ผ่านทั้งหมด** ✅
- หมายเหตุ: sandbox mount เห็นไฟล์ที่เพิ่งแก้เป็นเวอร์ชันเก่า/ขาดท้ายไฟล์ จึงตรวจจากไฟล์จริงบนเครื่องโดยตรง (ตรงกับ memory เรื่อง stale mount)

## จุดแข็งที่ควรคงไว้

Atomic write + push-guard กัน data loss (มีเทสครอบ), quota guard หลายชั้น, engine เป็น pure function แยกจาก I/O ชัดเจน, ทุกตัวเลขมี provenance/flags, ทุก data source มี fallback tier, และ error ใน advice/verdict ถูก contain ไม่ให้ล้ม row ทั้งแถว — สถาปัตยกรรมโดยรวมสะอาดกว่าค่าเฉลี่ยของแอปขนาดนี้มาก

---

## 🔴 CRITICAL — ควรแก้ก่อน (reliability จริง ๆ)

### C1. Network I/O ค้างอยู่ใต้ `store.LOCK` (drive_push ใน save())
`store.save()` เรียก `gdrive_store.drive_push()` (อัปโหลด HTTPS ไป Google Drive) ทั้งที่ทุก endpoint ที่แก้ข้อมูลถือ `st.LOCK` อยู่ — ขัดกับ design note ใน app.py ที่บอกว่า "lock is held only for a fast load→merge→save" และ googleapiclient ไม่ได้ตั้ง timeout ชัดเจน ถ้า Drive ช้า/ค้าง ทุก endpoint ที่เขียนข้อมูลจะค้างตาม (UI freeze ทั้งแอป)
**แก้:** ย้าย `drive_push` ออกนอก lock (เขียน local + os.replace ใต้ lock, ปล่อย lock แล้วค่อย push) หรือ push ผ่าน background thread + debounce และตั้ง timeout ให้ Drive client

### C2. `drive_pull` เขียนไฟล์ local แบบ non-atomic
`drive_pull()` เขียนทับ `portfolio.json` ด้วย `open(path,"wb").write(data)` ตรง ๆ ขณะที่ reader อ่านแบบ lock-free (by design) — มีหน้าต่างเล็ก ๆ ที่ reader เจอไฟล์เขียนครึ่งเดียว → `JSONDecodeError` → 500
**แก้:** ใช้ pattern เดียวกับ `save()`: เขียน `.tmp` แล้ว `os.replace()`

### C3. Token hygiene (APP_TOKEN)
Token รับผ่าน query string (`?token=`) → ติดใน browser history / server log และ cookie เก็บ token ดิบโดยไม่ตั้ง `secure=True` (มี httponly แล้ว)
**แก้:** เพิ่ม `secure=True` (Render เป็น HTTPS), และหลัง set cookie แล้วแนะนำผู้ใช้เปิด URL เปล่า (มีอยู่แล้วใน doc) — งานเล็ก ผลคุ้ม

---

## 🟠 HIGH — เพิ่ม reliability

### H1. SEC ไม่มี stale-cache fallback
`sec_edgar.fetch_companyfacts` ถ้า cache หมดอายุ + network ล้ม จะ raise ทันที ทั้งที่มี cache เก่าอยู่บนดิสก์ — ต่างจาก pricecache ที่มี `read_any()` เสิร์ฟของเก่าพร้อม flag
**แก้:** network ล้ม → เสิร์ฟ cache หมดอายุ + append flag "SEC data stale (served from cache)"

### H2. ประมาณการ quota ต่อ ticker ต่ำไป (cost=4 แต่ใช้ได้ถึง 5)
`analyze()` ใช้ FMP ได้สูงสุด 5 call (profile + quote-fallback + earnings + estimates + quarterly-est) แต่ `fetch_fundamentals`/`fetch_watchlist` pre-check ด้วย `cost = 4` → ทะลุ cap ได้เล็กน้อยเมื่อใกล้เต็ม
**แก้:** ใช้ 5 หรือส่ง budget เข้าไปเช็คแบบ dynamic

### H3. Price cache ไม่แยกตาม range/ความยาว
`get_prices_long(..., rng="5y")` โดน short-circuit ด้วย cache ที่มีแค่ ≥60 แท่ง (min_bars) — series 2y ที่ cache ไว้จะบล็อกการดึง 5y ทั้งวัน ผลคือ `reversal_flag` (ต้องการ 756 แท่ง) เงียบหายและ 12-1 อาจ partial โดยไม่รู้ตัว
**แก้:** รับ cache สดเฉพาะเมื่อ `len(closes) >= full_bars` (ไม่งั้นลอง network ก่อน แล้วค่อย fallback cache) หรือใส่ range ใน cache key

### H4. `_find_file_id` cache ตลอดชีพ process
ถ้าไฟล์ใน Drive ถูกลบ/ย้าย id เดิมจะ 404 ทุกครั้งจนกว่าจะ restart
**แก้:** เมื่อ update() 404 ให้ reset `_file_id = None` แล้ว retry create หนึ่งครั้ง

### H5. ระบุ single-worker ให้ชัดใน deploy
`threading.RLock` + in-process Drive state ปลอดภัยเฉพาะ 1 worker (uvicorn default) — ถ้าวันหน้าเพิ่ม `--workers 2` ทุก guard พังเงียบ ๆ
**แก้:** คอมเมนต์ใน Procfile/render.yaml ว่า "ห้ามเพิ่ม workers" (หรือ assert ใน startup)

---

## 🟡 PERFORMANCE — คุ้มที่สุดต่อแรงที่ลง

### P1. Pipeline ดึงข้อมูลแบบ serial (จุดคอขวดหลัก)
`fetch_fundamentals` / `fetch_daily` / `fetch_watchlist` วนทีละ ticker; แต่ละตัว = SEC หลาย MB + FMP 4-5 call + Yahoo (retry สูงสุด 3 รอบ × 2 host) → 10 ตัว = หลายนาที
**แก้:** `ThreadPoolExecutor(max_workers=4-6)` — โครงรองรับอยู่แล้ว: SEC throttle เป็น thread-safe (`_sec_lock`), engine เป็น pure, การ commit แยกเฟสอยู่แล้ว เหลือแค่รวมผล/นับ calls ตอนจบ คาดว่าเร็วขึ้น ~4-6 เท่า

### P2. Yahoo session ไม่ reuse
`fetch_consensus` สร้าง Session ใหม่ + warm-up 2 URL + ขอ crumb ใหม่ทุก attempt (สูงสุด 3 ครั้ง/ticker)
**แก้:** cache (session, crumb) ระดับ process, invalidate เมื่อ request ล้ม

### P3. งานซ้ำใน portfolio_view / analyze_row
`margin_track.build()` ถูกเรียก 2 ครั้งต่อ row (ครั้งแรกเพื่อ `margin_trend`, อีกครั้งใน `_earn_status`) — คำนวณครั้งเดียวแล้ว reuse

### P4. Dead code ใน frontend
`renderPortfolio()` สร้าง `rows` + `totRow` (HTML ตาราง) เต็ม ๆ แล้วไม่ได้ใช้ (การ์ด layout แทนที่ไปแล้ว) — ลบทิ้งได้เลย ลดงาน render ต่อโหลด

### P5. HTTP connection reuse ฝั่ง FMP
ทุก fetch ใช้ `requests.get` เดี่ยว ๆ — ใช้ `requests.Session` ร่วมจะลด TLS handshake ต่อ call (minor)

---

## 🔵 MINOR / hygiene

1. `refresh.analyze()` และ `refresh.allocation()` มี inline `import requests` + ยิง FMP profile ตรง ๆ ซ้ำสองที่ (timeout 15 vs 12, ไม่มี legacy fallback) → ควรรวมเป็น `fmp.fetch_profile()`
2. Frontend: `load()/loadWatchlist()/loadAllocation()/loadScreen()` ไม่มี try/catch — network ล้ม = จอค้างเงียบ (loadRisk ทำถูกแล้ว) → ใส่ error banner แบบเดียวกัน
3. Tooltip ใน `_circ()` ไม่ผ่าน `esc()` (ข้อมูล quarter/grade มาจาก API ภายนอก) — ความเสี่ยง XSS ต่ำแต่ควร escape ให้สม่ำเสมอ; `renderWatchNames` interpolate ticker ดิบ (ถูก clean ฝั่ง server แล้ว จึงพอรับได้)
4. `requirements.txt`: google libs ใช้ `>=` — pin version เพื่อ deploy ซ้ำได้ผลเดิม
5. `fmp_usage` cutoff เก็บจริง 8 วัน (`>= cutoff` กับ delta 7 วัน) — cosmetic
6. ERP / MARKET_PE เป็น manual constant ซ้ำกัน 3 ที่ (config.py, skill scripts, SKILL.md) — มี staleness flag แล้วดี แต่ควรหมายเหตุว่าที่ไหนคือ source of truth (แนะนำ config.py)
7. `get_prices()` (เส้นสั้น 3mo) ไม่ผ่าน `momentum.clean_series()` เหมือนเส้นยาว — spike bar หลุดเข้า RSI/MACD ได้
8. `QUOTA_CAP = 250` ซ้ำระหว่าง app.py กับ default ใน pipeline — ย้ายเข้า config.py
9. `ifa-stock-analysis-v8/scripts/*`: เป็น CLI pure calculator สะอาดดี ไม่มีประเด็น นอกจากข้อ 6 (ERP ซ้ำ)

---

## ลำดับที่แนะนำ (แรงน้อย → ผลมาก)

| ลำดับ | งาน | แรง | ผล |
|---|---|---|---|
| 1 | C2: drive_pull เขียนแบบ atomic | ~5 บรรทัด | ตัด 500 sporadic |
| 2 | C1: ย้าย drive_push ออกนอก LOCK + timeout | ~20 บรรทัด | ตัด server freeze ทั้งกลุ่ม |
| 3 | H2/H3: quota cost=5 + cache เช็ค full_bars | ~10 บรรทัด | ข้อมูล momentum ครบ + ไม่ทะลุ quota |
| 4 | C3: cookie secure=True | 1 บรรทัด | token hygiene |
| 5 | H1: SEC stale-cache fallback | ~10 บรรทัด | refresh รอดตอน SEC ล่ม |
| 6 | P1: parallel fetch (ThreadPool) | ~30 บรรทัด | refresh เร็วขึ้น 4-6 เท่า |
| 7 | P4/P3: ลบ dead code + dedupe margin_track | ลบโค้ด | ความสะอาด |

ข้อ 1-5 ทำได้โดยไม่แตะ design; ข้อ 6 ควรทำหลังมีเทส concurrency เล็ก ๆ ครอบ (นับ fmp_calls ให้ตรงเมื่อ parallel)
