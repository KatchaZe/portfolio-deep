# แผนงาน: Momentum Section สำหรับ Portfolio Dashboard
*(อิงหลักการ QuantInsti / Nitesh Khandelwal — ใช้ข้อมูลฟรี)*

วันที่: 2026-06-25 · สำหรับ portfolio-app-v2

---

## 1. สรุปหลักการ Momentum (จาก QuantInsti)

แก่นคือ **"buy high, sell higher"** — เทรนด์ราคามักวิ่งต่อก่อนกลับตัว เราเลือกถือสินทรัพย์ที่ "แรงขึ้น" และระวังตัวที่ "แรงตก" มี 2 ชนิดหลัก:

| ชนิด | วัดอะไร | สูตรหลัก | ใช้ตอนไหน |
|---|---|---|---|
| **Time-Series (Absolute)** | ผลตอบแทนของหุ้นเทียบกับ "อดีตของตัวมันเอง" | ผลตอบแทนสะสมช่วง 3/6/12 เดือน > เกณฑ์ → แรง | ดูว่าหุ้นตัวนี้ยัง "ขาขึ้น" ไหม |
| **Cross-Sectional (Relative)** | จัดอันดับหุ้นเทียบกัน | rank ผลตอบแทน 3–12 เดือนในพอร์ต | ตัวไหนแรงสุด/อ่อนสุดในพอร์ต |

**เครื่องมือยืนยัน** ที่บทความระบุ: Moving Average crossover (Golden/Death cross), RSI, MACD, Stochastic, Bollinger, Volume, ATR, Ichimoku
**มาตรฐานวิชาการ:** momentum **12–1** = ผลตอบแทน 12 เดือนแต่ "ข้ามเดือนล่าสุด" (เพราะ 1 เดือนล่าสุดมักกลับตัวสั้นๆ — short-term reversal)
**ความเสี่ยงที่ต้องเตือน:** จังหวะเข้า/ออกผิด, ต้นทุนเทรดบ่อย, **trend reversal / momentum crash** (พังแรงในตลาดหมี)

---

## 2. ⚠️ ข้อค้นพบสำคัญ: ของเดิมในแอป "ไม่ใช่ momentum จริง"

`domain/indicators.py` ปัจจุบันรวม 3 สัญญาณเป็น `momentum_score`:

- **RSI** → โค้ดตั้ง `RSI < 30 = Bullish` ซึ่งเป็นมุม **mean-reversion (ซื้อตอน oversold)** — *ตรงข้าม* กับ momentum (momentum จะมอง RSI > 50 = แรงขาขึ้น)
- **DBBMV** (Bollinger ปรับด้วย vol) → เป็นสัญญาณ **เด้งกลับจากกรอบ = mean-reversion** เช่นกัน
- **MACD** → อันนี้เป็น trend/momentum จริง ✔ (มีตัวเดียว)

**สรุป:** score เดิม = mean-reversion 2 + trend 1 → มันคือมาตรวัด "จังหวะกลับตัวระยะสั้น" ไม่ใช่ momentum ตามหลัก QuantInsti
**ตอบคำถามเรื่องความน่าเชื่อถือ:** คำนวณ "ถูกต้อง" ในเชิงเลข แต่ "ไม่ตรงนิยาม" ของ momentum — ถ้าจะดู momentum ของหุ้นในพอร์ตจริงๆ ควรเปลี่ยนสูตร

---

## 3. สูตรที่เสนอ (faithful + ใช้ข้อมูลฟรี)

ให้ `P` = ราคาปิดปรับแล้ว (adjusted close) เรียงเก่า→ใหม่, 1 ปี trading ≈ 252 วัน

**A. Time-series momentum (ต่อหุ้น)**
```
ROC_3M  = P[-1]/P[-63]  - 1
ROC_6M  = P[-1]/P[-126] - 1
ROC_12M = P[-1]/P[-252] - 1
MOM_12_1 = P[-21]/P[-252] - 1      # มาตรฐานวิชาการ (ข้ามเดือนล่าสุด)
```
**B. Trend filter (ประตูขาขึ้น)**
```
SMA200   = average(P[-200:])
above200 = P[-1] > SMA200          # อยู่เหนือเส้น 200 วัน = ขาขึ้น
dist200  = P[-1]/SMA200 - 1
```
**C. Risk-adjusted (ลดความเสี่ยง crash)**
```
vol6m       = std(daily_returns[-126:]) * sqrt(252)
risk_adj_mom = ROC_6M / vol6m       # คล้าย Sharpe ของ momentum
```
**D. Cross-sectional rank (ทั้งพอร์ต)**
```
percentile rank ของ MOM_12_1 เทียบหุ้นทุกตัวในพอร์ต → Top / Mid / Bottom
```
**E. ป้ายสรุป (composite, โปร่งใส อ่านง่าย)**
นับคะแนนจาก 4 องค์ประกอบที่เห็นได้: MOM_12_1>0, ROC_6M>0, above200, RSI(14)>50
→ map เป็น **Strong / Positive / Neutral / Weak / Negative** (โชว์แต่ละองค์ประกอบด้วย)

**ตัวยืนยัน (เก็บไว้เป็น secondary):** MA crossover 50/200, MACD, RSI ใช้ในทิศ momentum (>50 = bullish)

---

## 4. แหล่งข้อมูลฟรี

**ลำดับ fallback ที่ใช้ (ทั้งหมด adjusted ยกเว้น Stooq):**

| ลำดับ | แหล่ง | สถานะในแอป | ข้อดี | ข้อควรระวัง |
|---|---|---|---|---|
| 1 | **Yahoo chart** | มีแล้ว (`sources/yahoo.py`) | **adjusted close** ครบ, ฟรี, ประวัติเต็ม | server IP บางทีถูกบล็อก |
| 2 | **FMP** `/historical-price-full` | มี source+key แล้ว (`sources/fmp.py`) — *ต้องเพิ่มฟังก์ชัน history* | คืน **adjClose** (split+dividend), **API-key ไม่โดน IP block** | free tier ~250 req/วัน → กิน quota เฉพาะตอน Yahoo ล่ม |
| 3 | **Stooq** | มีแล้ว (`sources/stooq.py`) | ไม่ต้อง key, ตอบจาก cloud ได้ | ปรับ **split** แต่มัก **ไม่ปรับ dividend** → ติดธงเตือน |

**ทำไมไม่ใช้ Google Finance:** ไม่มี public API จริง (ปิดตั้งแต่ 2012) ใช้ได้แค่ `GOOGLEFINANCE()` ใน Google Sheets, scrape เว็บเปราะ/ผิด ToS → ตัดทิ้ง
**เตือน dividend:** หุ้นปันผลสูง (เช่น ABBV) ถ้าตกไปถึง Stooq, 12M momentum จะต่ำกว่าจริงเล็กน้อย → โชว์ธงบอกว่าใช้ราคาไม่ปรับ dividend

---

## 5. ประเมินความน่าเชื่อถือ & ประสิทธิภาพ

**เชื่อถือได้ (✔)**
- คณิตศาสตร์ ROC/SMA/rank เป็นสูตรมาตรฐาน เป็น deterministic
- เบามาก: O(จำนวนหุ้น × วัน) ใส่ใน daily refresh เดิมได้สบาย
- เป็น dashboard เฝ้าดูพอร์ตปัจจุบัน → ไม่มีปัญหา look-ahead/survivorship

**เงื่อนไขที่ต้องทำให้ถูก (⚠️)**
1. **ต้องใช้ adjusted close** (ปรับ split+dividend) ไม่งั้นได้ momentum ผิดรอบปันผล/แตกพาร์
2. ต้องมีประวัติ **≥ 252 วัน** ถึงคำนวณ 12–1 ได้ (หุ้นใหม่ → โชว์เฉพาะช่วงที่มีข้อมูล)
3. คิดบน **ราคาปิดรายวัน** + ระบุ `as_of` ให้ชัด (อย่าผสม intraday)

**ขีดจำกัดที่ต้องพูดตรงๆ**
- สูตรนี้ "ตรงนิยาม + คำนวณถูก" แต่ **ไม่การันตีกำไร** — บทความ QuantInsti ก็เน้นว่า *backtest ไม่ใช่อนาคต* ถ้าจะเคลม edge ต้อง backtest แยก (อยู่นอก scope dashboard นี้)

---

## 6. แผนลงมือ (step-by-step)

1. **ยืนยันนิยาม momentum หลัก** ที่จะโชว์ (ดู §7 คำถาม)
2. **ดึงประวัติราคา ≥252 วัน (adjusted)** — ต่อยอด `stooq.py`/`yahoo.py` ให้คืน adjusted close
3. **สร้างโมดูลใหม่ `domain/momentum.py`** — ฟังก์ชัน pure: `roc()`, `mom_12_1()`, `sma()`, `vol()`, `score()` (เลิก/แทน logic mean-reversion เดิม)
4. **ต่อเข้า `pipeline/refresh.py`** — คำนวณต่อหุ้น + rank ระดับพอร์ต → เก็บลง `portfolio.json`
5. **แก้ frontend `index.html`** — เปลี่ยนช่อง momentum (วงกลม RSI/MACD/DBBMV) เป็น cell ใหม่: ROC_6M%, MOM_12_1%, trend ✓, badge rank, ป้าย composite (สีตามระดับ)
6. **เขียน unit test** ใน `tests/` (ใส่ array ราคาคงที่ เช็คค่า) — ตามแพทเทิร์นเทสต์เดิม
7. **Verify** — รันกับหุ้นจริงในพอร์ต + suม manual เทียบ 1–2 ตัวกับแหล่งภายนอก

> หมายเหตุ stale-facts: หลังเพิ่ม field ใหม่ ต้องกด **Run Fundamental/Price Refresh** ใหม่ ไม่งั้นช่องว่าง (เหมือนเคสคอลัมน์ Earnings)

---

## 7. ✅ การตัดสินใจที่ล็อกแล้ว (2026-06-25)
1. **ตัวหลัก = Composite ครบ** — รวม MOM_12_1, ROC_6M, เหนือ SMA200, RSI>50 → ป้าย Strong/Positive/Neutral/Weak/Negative พร้อมโชว์องค์ประกอบย่อย + cross-sectional rank
2. **เก็บ RSI/MACD/DBBMV เดิมไว้เป็น secondary** (ตัวยืนยัน ไม่ทิ้งข้อมูลเดิม)
3. **แหล่งราคา 3 ชั้น: Yahoo adjusted → FMP adjClose → Stooq** + ติดธงเตือนเมื่อใช้ราคาที่ไม่ปรับ dividend (FMP กิน quota เฉพาะตอน Yahoo ล่ม)

### Key steps ก่อนลงมือ (สรุปให้เข้าใจตรงกัน)
1. เตรียม price feed 3 ชั้น → คืน **adjusted close ≥252 วัน**: ใช้ `yahoo.py`/`stooq.py` เดิม + **เพิ่ม `fmp.fetch_history()`** (`/historical-price-full`, อ่าน `adjClose`) เป็นชั้นกลาง
2. โมดูลใหม่ `domain/momentum.py` (pure functions: roc, mom_12_1, sma, vol, composite score)
3. ต่อ `pipeline/refresh.py` → คำนวณต่อหุ้น + rank พอร์ต → เก็บ `portfolio.json`
4. แก้ `index.html` → ช่อง momentum ใหม่ (composite เด่น, RSI/MACD เป็น secondary)
5. unit test ใน `tests/` + verify กับหุ้นจริง 1–2 ตัว
6. หลังเพิ่ม field → กด **Price/Fundamental Refresh** ใหม่ (กัน stale-facts)

---

## 8. ✅ สิ่งที่ลงมือทำจริง (2026-06-25)

**ไฟล์ที่เพิ่ม/แก้:**
- `domain/momentum.py` (ใหม่) — pure: `roc, mom_12_1, sma, annualized_vol, rsi, compute, cross_sectional_rank`
- `sources/fmp.py` — เพิ่ม `fetch_history` + `parse_history` (adjClose)
- `sources/yahoo.py` — `fetch_chart` อ่าน `adjclose` (เดิมอ่าน raw close) → คืน `adj_closes`
- `pipeline/refresh.py` — `get_prices_long` (3-tier) + ฝัง v2 ใน `fetch_daily`/`analyze_row` + `cross_sectional_rank` ใน `portfolio_view`
- `app.py` — ส่ง `FMP_API_KEY` เข้า daily (FMP ช่วยเฉพาะตอน Yahoo บล็อก)
- `index.html` — `momCell()` โชว์ composite เป็นหลัก, RSI/MACD/DBBMV ไป tooltip, ⚠ เมื่อ split-only, "—" เมื่อไม่มีข้อมูล
- `tests/test_momentum.py` (ใหม่, อยู่ใน `run_tests.py`)

**ยืนยันหลักการที่กำหนด:**
1. ไม่ประมาณการเอง — `compute()` ใช้เฉพาะราคาที่ดึงได้, ค่าไหนข้อมูลไม่พอ = `None`, composite นับเฉพาะองค์ประกอบที่มีจริง
2. แหล่งสำรอง — `get_prices_long`: Yahoo→FMP→Stooq, เลือกแหล่งที่ครบ ≥250 แท่ง (ถ้าไม่ถึงใช้แหล่งที่ยาวสุด ≥60 → แสดง partial)
3. ครบหมดยังไม่ได้ → raise → จับ → `None` → UI "—"
4. เปิดที่ไหน/เมื่อไรก็ได้ — เก็บใน `portfolio.json` (`momentum[t].v2`), reopen render จาก disk, rank คำนวณตอน view

**สถานะความเสี่ยง:**
- ✅ **R1 แก้แล้ว** — `momentum.clean_series` ทิ้งเฉพาะแท่งเสีย (ราคา ≤0 / spike 1 วันที่เด้งกลับ) ก่อนคำนวณ, แนบ `quality` (dropped/bars) โชว์ใน tooltip — ไม่ fabricate
- ✅ **R3 แก้แล้ว** — `portfolio_view` คืน `momentum_meta` (as_of/age/stale) + ต่อแท่ว `stale` รายตัว; frontend มี banner เตือน + marker ⏳ ต่อหุ้น
- ✅ **R2 แก้แล้ว** — เก็บ `dividend_ps` (FMP `lastDiv`) ใน facts; `momentum.div_warn()` เตือน ⚠ เฉพาะหุ้นจ่ายปันผลที่ใช้ราคา split-only — หุ้นไม่จ่ายปันผล = แม่นเต็ม ไม่เตือน (ไม่ประมาณค่า)
- ✅ **R4 แก้แล้ว** — `pipeline/pricecache.py`: cache ราคา 2y ต่อ ticker; ใช้ซ้ำถ้าสดในวัน (ลด FMP quota) + ถ้าทุก source ล่มเสิร์ฟ cache เก่า (flagged) → momentum รอดช่วง outage

**บั๊กที่เจอระหว่าง audit + แก้:** `deep_v82.py` `_verdict` พิมพ์ `.str` → `.strip()` (เดิม crash verdict หุ้นปกติทุกตัว); FMP tier ใน `get_prices_long` ลืม cache (`return pay`→`finish(pay)`)

**Watchlist:** momentum composite + คอลัมน์ Rank แยก (Top/Mid/Bottom + percentile) แสดงเท่า portfolio — `fetch_watchlist` เรียก `cross_sectional_rank` จัดอันดับในกลุ่ม watchlist เอง

---

## 9. Push runbook (ทำตามลำดับ)

ไฟล์ที่เปลี่ยนทั้งหมด — แก้ไข 9: `app.py, config.py, domain/facts.py, domain/engine/deep_v82.py, index.html, pipeline/refresh.py, run_tests.py, sources/fmp.py, sources/yahoo.py` · ใหม่ 5: `domain/momentum.py, pipeline/pricecache.py, tests/test_momentum.py, tests/test_pricecache.py, MOMENTUM_PLAN.md`

```bash
cd "C:\Users\Katcha\Documents\Claude\Projects\Stock Screening\portfolio-app-v2"
del .git\index.lock                 # ลบ lock ค้าง (ไม่มีก็ข้าม)
py run_tests.py                     # ต้องขึ้น ALL TEST SUITES PASSED OK
git status                          # ดูไฟล์ที่จะ commit (ควรเห็น 9 แก้ + 5 ใหม่)
git add -A                          # stage ทั้งหมด (กันตกไฟล์; cache/json ถูก gitignore อยู่แล้ว)
git commit -F COMMIT_MSG.txt        # หรือใช้ -m "..." (ข้อความเต็มอยู่ในแชต)
git push
```

**หลัง push (ถ้า deploy บน Render/cloud):**
1. ตั้ง env `FMP_API_KEY` บน host (ให้ FMP เป็น fallback adjusted ก่อน Stooq + เก็บ `dividend_ps` ของ R2)
2. รอ redeploy แล้วเข้าแอป กด **Run Fundamental Refresh** (เติม `dividend_ps` → R2 ทำงาน) → จากนั้น **Run Daily (price/momentum)** (เติม momentum v2)
3. หุ้นเก่ายังขึ้น "—" จนกว่าจะ refresh รอบแรก (stale-facts gotcha)
