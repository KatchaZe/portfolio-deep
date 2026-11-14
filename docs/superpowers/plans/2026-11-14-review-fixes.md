# แผนแก้จากรีวิว 2026-11-11 (A1–A4, B1, B2, B6, FV fixes #1–#3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** แก้ข้อบกพร่องที่รีวิวพบ ให้ข้อมูลพอร์ตไม่หาย, แอปไม่ล่มจาก input ผิด, FV ของบริษัทที่โมเดลไม่ถนัดไม่ถูกใช้สั่งขาย และหน้า Risk Desk เหลือคำแนะนำชุดเดียว

**Architecture:** ทุกการแก้วางในโมดูลที่เป็นเจ้าของเรื่องนั้นอยู่แล้ว ไม่สร้างโมดูลใหม่ — data guard อยู่ใน `store.py` / `sources/gdrive_store.py`, ตัวเลข FV อยู่ใน `domain/engine/*`, สถานะ "ไม่จัดอันดับ" เกิดที่จุดเดียวคือ `deep_v82._overall` แล้วไหลไปยัง consumer เดิม (advice, targets, rebalance, card) ซึ่งแต่ละตัวต้องรู้จักค่าใหม่นี้

**Tech Stack:** Python 3 / FastAPI, vanilla JS, tests ด้วย `python run_tests.py` (glob `tests/test_*.py`)

**Spec:** Project doc `claude/review-2026-11-11-full-stack-and-damodaran.md` (หัวข้อ A1–A4, B1, B2, B6, 7, 8) + การตัดสินใจของผู้ใช้ 2026-11-14 (ด้านล่าง)

## การตัดสินใจที่ล็อกแล้ว (2026-11-14)

| เรื่อง | ตัดสินใจ |
|---|---|
| B6 | Targets เป็นแหล่งเดียว; Rebalance แปลง Targets เป็นจำนวนหุ้น/เงิน; DEEP rating ยังเป็น rating หลัก (ตามที่ตกลง 2026-11-06) |
| B1 | "ไม่จัดอันดับ" เมื่อ: uncertainty = Extreme, หรือ FV ถูกดันขึ้นด้วย liquidation floor, หรือ terminal share > 100%, หรือ TTM ไม่ครบ 4 ไตรมาสต่อเนื่อง |
| NCI | หักมูลค่าทางบัญชี (`MinorityInterest`) ใน equity bridge |
| A1 | Drive ใหม่กว่า → บล็อก push, เก็บทั้งสองไฟล์ + backup ลงวันที่, badge แจ้งเตือน |

## Global Constraints

- Layering: `sources/` ห้าม import `pipeline/` หรือ `domain/`; `domain/` ห้าม import `pipeline/`
- ทุก field ใหม่ใน `FinancialFacts` ต้องประกาศ CLOCK และ UNIT ใน `domain/contracts.py` (มิฉะนั้น `tests/test_contracts.py` fail)
- ค่าคงที่: `WACC_G_MIN_SPREAD = 0.03` · young uncertainty floor = `"Very High"` · TTM gap = 80–100 วัน (ค่าเดียวกับ `pipeline/quarterly.GAP_DAYS`; คัดลอกค่ามาไว้ใน sources พร้อม comment ชี้กลับ เพราะ import ข้าม layer ไม่ได้)
- ค่า recommendation ใหม่: `"NOT RATED"` · ป้าย UI: `"ไม่จัดอันดับ"` · signal: `None`
- เปลี่ยน engine แล้ว: รัน `python tools/build_skill.py` แล้ว `tests/test_skill_engine.py` ต้องผ่าน
- FV เปลี่ยน: re-pin ด้วย `python tests/replay_snapshot.py --update` หลังอ่าน diff แล้วเท่านั้น; commit message ระบุหุ้นที่ FV ขยับ > 10%
- เปลี่ยนสิ่งที่เห็นบน UI: bump `config.BUILD` และ `DASH_BUILD` ใน `static/js/app.js` เป็นค่าเดียวกัน (`2026-11-14a`, `b`, …)
- เทสต์ใหม่อยู่ใน `tests/test_fixes_2026_11_14.py` (ไฟล์เดียว, run_tests เจอเองผ่าน glob)
- จบทุก task: `python run_tests.py` ต้องจบด้วย `ALL TEST SUITES PASSED OK`
- git บนโฟลเดอร์ที่เชื่อมต่อ: การลบไฟล์ถูกปิด → ขอ `device_request_delete_permission` ครั้งเดียวก่อน commit แรก (ไม่งั้น `.git/*.lock` ค้าง)
- ทำบน branch `fix/review-2026-11-14` ไม่ commit ลง `main` ตรง

## Review Focus

1. Drive อ่าน version ไม่ได้ (API error ระหว่างเช็ก) → push ต้องไม่เกิด, local เก็บไว้, badge แดง — test ใน Task 1
2. `shares=0` ต้องยังใช้ได้ (ค่าถูกต้อง) ขณะที่ค่าติดลบ/NaN/inf ถูกปฏิเสธ **และ** การปฏิเสธต้องไม่นับเป็น local edit — test ใน Task 1
3. หุ้นที่เดิมเป็น SELL แล้วกลายเป็น NOT RATED: Targets คงน้ำหนักเดิม, Rebalance ไม่ขาย, `_signal` ไม่คืน `"SELL"` — test ใน Task 6
4. บริษัทไม่มี tag `MinorityInterest` → มูลค่าเท่าเดิมทุกหลัก (ไม่ใช่ crash หรือ 0) — test ใน Task 3
5. ไตรมาส 53 สัปดาห์ (ห่าง 98 วัน) ยังนับว่าต่อเนื่อง; ห่าง 101+ วันไม่นับ — test ใน Task 2

## ลำดับและเหตุผล

ตามลำดับที่ผู้ใช้ให้ โดยสลับ engine fixes (#1–#3) มาก่อน B1 เพราะเกณฑ์ "ไม่จัดอันดับ" อ่าน flag ที่ #1 และ #3 สร้าง

| Task | เรื่อง | ไฟล์หลัก |
|---|---|---|
| 1 | A1 + A2 กันข้อมูลหายและแอปล่ม | `sources/gdrive_store.py`, `store_sync.py`, `store.py`, `app.py`, `static/js/app.js` |
| 2 | #3 TTM 4 ไตรมาสต่อเนื่อง | `sources/sec_edgar.py` |
| 3 | #2 WACC − g guard + หัก NCI | `deep_v82._growth`, `young_dcf.evaluate`, `fcff_dcf.value`, `sec_edgar`, `contracts.py`, `normalize.py` |
| 4 | #1 liquidation floor + young uncertainty ≥ Very High | `young_dcf.py`, `uncertainty.assess`, `deep_v82` |
| 5 | B2 beta ของ young firm | `deep_v82._bottom_up_beta` |
| 6 | B1 + B6 "ไม่จัดอันดับ" + คำแนะนำชุดเดียว | `deep_v82._overall`, `advice.py`, `targets.py`, `portfolio.rebalance`, `risk_report.build`, `card.js`, `format.js`, `risk.js` |
| 7 | A3 + A4 ฟอร์มและ ledger | `static/js/portfolio.js`, `domain/ledger.py` |

---

### Task 1: A1 + A2 — Drive conflict guard และตรวจค่า holding

**Files:**
- Modify: `sources/gdrive_store.py` (`drive_pull` ~197, `drive_push` ~240, `_find_id`)
- Modify: `store_sync.py` (`ensure_pull`, `_push_worker`)
- Modify: `store.py` (`set_holding` ~212, `save` ~75)
- Modify: `app.py` (endpoint `/api/holding` — map `ValueError` → 422 แบบเดียวกับ set_cash)
- Modify: `static/js/app.js` (~66 badge: เพิ่มสถานะ conflict)
- Test: `tests/test_fixes_2026_11_14.py`

**Interfaces:**
- Produces: `gdrive_store.STATUS["remote_version"]: str|None`, `STATUS["push_result"] == "conflict"`, `gdrive_store.remote_version(svc, fid) -> str`, `store.set_holding` raises `ValueError` ก่อนแตะ state

- [ ] **Step 1: เขียนเทสต์ที่ fail**
  - `test_push_blocked_when_remote_version_moved`: fake client คืน version `"7"` ตอน pull, `"8"` ตอน push → `drive_push` คืน `False`, `STATUS["push_result"] == "conflict"`, `_upload` ไม่ถูกเรียก, มีไฟล์ `portfolio.json.remote-<YYYYMMDD-HHMM>` ข้าง local
  - `test_push_blocked_when_version_check_errors`: fake `get` raise → คืน `False`, ไม่ upload
  - `test_push_ok_and_version_advances`: version เท่ากัน → upload, `STATUS["remote_version"]` เป็นค่าใหม่ที่ upload คืนมา
  - `test_set_holding_rejects_bad_shares`: `-5`, `nan`, `inf` → `ValueError`; `store_sync._edit_count()` ไม่เพิ่ม; holdings ไม่เปลี่ยน
  - `test_set_holding_allows_zero`: `shares=0` ผ่าน
  - `test_save_refuses_nan`: state มี `float("nan")` → `save` raise `ValueError`, ไฟล์เดิมไม่ถูกแทนที่
- [ ] **Step 2:** `python tests/test_fixes_2026_11_14.py` → FAIL
- [ ] **Step 3: Implement**
  - `drive_pull`: หลัง download สำเร็จ บันทึก `STATUS["remote_version"]` จาก `files().get(fileId, fields="version")`
  - `drive_push`: ก่อน `_upload` อ่าน version ปัจจุบัน; ถ้าไม่เท่ากับที่บันทึกไว้ หรืออ่านไม่ได้ → ดาวน์โหลด remote ไปเก็บ `path + ".remote-" + stamp`, ตั้ง `push_result="conflict"`, คืน `False`; ถ้าไม่มีไฟล์บน Drive (absent) → push ได้. หลัง upload สำเร็จอัปเดต `remote_version` จาก response (`fields="id,version"`)
  - Backup ลงวันที่บน Drive: วันละครั้ง ผ่าน `drive_push_json(f"portfolio.backup-{YYYY-MM-DD}.json", path)` ที่มีอยู่แล้ว (เรียกจาก `_push_worker` เมื่อยังไม่มีของวันนี้)
  - `store_sync`: conflict = state ใหม่ `"conflict"`; `schedule_push` บล็อกเหมือน `"error"` จนกว่าผู้ใช้ restart/pull ใหม่ (ไม่ retry อัตโนมัติ — กันวนเขียนทับ)
  - `set_holding`: ตรวจ `math.isfinite` และ `>= 0` ของ shares/avg_cost **ก่อน** `mark_local_edit()`
  - `save`: `json.dump(..., allow_nan=False)` (ValueError เกิดก่อน `os.replace` จึงไม่ทับไฟล์ดี)
  - `app.js` badge: `push_result==='conflict'` → สีแดง, title บอกชื่อไฟล์ `.remote-*` ที่เก็บไว้
- [ ] **Step 4:** run tests → PASS, แล้ว `python run_tests.py`
- [ ] **Step 5:** bump BUILD `2026-11-14a` + commit `fix(store): block Drive push on remote version conflict; validate holding inputs (A1, A2)`

---

### Task 2: #3 — TTM ต้องเป็น 4 ไตรมาสต่อเนื่อง

**Files:**
- Modify: `sources/sec_edgar.py` `_Xbrl.ttm` (~123–140), `_Xbrl.quarters`
- Test: `tests/test_fixes_2026_11_14.py`

**Interfaces:**
- Produces: `_Xbrl.ttm(...)` คืน `None` เมื่อหา 4 ไตรมาสต่อเนื่องไม่ได้ (เดิมคืนผลรวมผิด); flag `"TTM: quarters not contiguous"` ผ่าน `self.flags` (หรือกลไก flag ที่ `_pick` ใช้อยู่)
- Consumed by: `_ttm`, `_ttm_present`, `_ttm_max`, `_pick`, `_derived_ebit` — ทุกตัวรับ `None` ได้อยู่แล้ว (ตรวจในขั้น 3) และ Task 6 อ่าน flag นี้

- [ ] **Step 1: เทสต์**
  - `test_ttm_rejects_gap`: ไตรมาสสิ้นสุด 2025-06-30, 2025-09-30, 2026-06-30, 2026-09-30 (แบบ SPCX) ไม่มี annual → `ttm()` เป็น `None`
  - `test_ttm_53_week_quarter_ok`: ระยะห่าง 91, 98, 84 วัน → คืนผลรวม 4 ไตรมาส
  - `test_ttm_annual_path_unchanged`: กรณี annual + YTD เดิม → ค่าเท่าเดิม
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** ใน fallback `last4` ตรวจว่า end date ที่เรียงลงมาห่างกันทีละ 80–100 วัน (`TTM_GAP_DAYS = (80, 100)`, comment: "same rule as pipeline/quarterly.GAP_DAYS"); ไม่ผ่าน → flag + `return annual["val"] if annual and annual is recent else None` — annual ใช้ได้เฉพาะเมื่อ `annual["end"]` อยู่ภายใน 100 วันจาก `latest_end`, ไม่งั้น `None`. ตรวจว่า caller ทั้ง 5 ตัวรับ `None` โดยไม่ล่ม (เพิ่ม guard เฉพาะตัวที่ยังไม่รับ)
- [ ] **Step 4:** PASS + `run_tests.py`; `replay_snapshot.py` ดู diff (คาดว่าเปลี่ยนเฉพาะหุ้นที่ quarter ขาด เช่น SPCX → FV หาย) แล้ว `--update`
- [ ] **Step 5:** build_skill + commit `fix(sec): TTM requires four contiguous quarters (#3)`

---

### Task 3: #2 — WACC − g guard และหัก minority interest

**Files:**
- Modify: `domain/engine/deep_v82.py` `_growth` (~1514–1516), `_dcf_inputs` (~1938), `_priced_in` reverse DCF (~1643 `g=rf`), young call (~2112)
- Modify: `domain/engine/young_dcf.py` (~289 `"g_stable": rf` → รับค่าจาก caller; ~176, ~287 bridge)
- Modify: `domain/engine/fcff_dcf.py` `value` (~77)
- Modify: `sources/sec_edgar.py` `FIELD_LADDERS` (~703): field `minority_interest` ← `["MinorityInterest", "MinorityInterestInConsolidatedSubsidiaries"]` (balance-sheet instant, ล่าสุด)
- Modify: `domain/contracts.py` (CLOCK instant, UNIT reporting currency), `pipeline/normalize.py` `_MONEY` (แปลง FX), `FinancialFacts` dataclass
- Test: `tests/test_fixes_2026_11_14.py`

**Interfaces:**
- Produces: `FinancialFacts.minority_interest: Optional[float]` (USD หลัง normalize); `fcff_dcf.value(..., minority_interest=0.0)`; `young_dcf.evaluate(..., g_stable: float)`; `deep_v82.WACC_G_MIN_SPREAD = 0.03`
- กฎ: `g_stable = min(rf, ke_stable - 0.03, cap.wacc_term - WACC_G_MIN_SPREAD)` คำนวณที่ `_growth` ที่เดียว แล้วทุก consumer (DCF, young, PEG, exit PE, excess return, reverse DCF) อ่าน `gr.g_stable` แทน `rf`

- [ ] **Step 1: เทสต์**
  - `test_terminal_spread_at_least_3pp`: fixture แบบ HCA (wacc_term 6.2%, rf 4.1%) → `gr.g_stable <= wacc_term - 0.03`
  - `test_young_uses_same_g_stable`: young model dict มี `g_stable == gr.g_stable`
  - `test_nci_deducted`: firm 100, cash 10, debt 30, NCI 8, shares 1 → equity 72
  - `test_nci_absent_no_change`: `minority_interest=None` → ค่าเท่าเดิมทุกหลัก (Review Focus 4)
  - `test_contracts_cover_minority_interest`: `tests/test_contracts.py` ผ่าน
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** implement ตาม Interfaces; NCI ลบใน bridge ทุกจุด (fcff_dcf 77, young 176 net_debt, young 287 distress, EV ที่ deep_v82 528/1637/2344 บวก NCI) ส่งเป็น keyword เพื่อให้ scenarios / story MC / implied return ได้ค่าเดียวกัน; `None` → 0
- [ ] **Step 4:** PASS + `run_tests.py`; replay diff คาด: HCA FV ลดมาก (≈ $600 ตาม §7), ELV/UNH/NFLX ลดเล็กน้อย → `--update`
- [ ] **Step 5:** build_skill + commit `fix(engine): enforce WACC-g >= 3pp and deduct minority interest (#2)`

---

### Task 4: #1 — Liquidation floor และ uncertainty ขั้นต่ำของ young firm

**Files:**
- Modify: `domain/engine/young_dcf.py` (~304 `point = failure_adjusted(...)`, scenario/MC ที่เรียก `failure_adjusted`)
- Modify: `domain/engine/uncertainty.py` `assess(scenarios, young_dcf=None, floor=None)`
- Modify: `domain/engine/deep_v82.py` ~1108 (ส่ง floor เมื่อ `anc.method.startswith("Young-Company")`)
- Test: `tests/test_fixes_2026_11_14.py`

**Interfaces:**
- Produces: young dict key `"floored": bool` (True เมื่อ FV ถูกดันขึ้นถึง distress); `assess(..., floor="Very High")` คืน rating ไม่ต่ำกว่า floor ตามลำดับ `Low < Medium < High < Very High < Extreme`; `"floored_by"` ใน block
- Consumed by: Task 6 (เกณฑ์ NOT RATED)

- [ ] **Step 1: เทสต์**
  - `test_young_fv_not_below_net_cash`: going concern = 0, p_survival 0.85, distress 7.14 → FV == 7.14, `floored is True`
  - `test_floor_applies_to_scenarios`: bear/base/bull ทุกค่า ≥ distress
  - `test_young_uncertainty_at_least_very_high`: width 0.3 (ปกติได้ Low) + floor → `"Very High"`
  - `test_floor_does_not_lower_extreme`: width 2.0 + floor → `"Extreme"`
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** `point = max(failure_adjusted(...), distress)` ทำใน helper เดียวที่ทุก path (point, scenarios, MC) เรียก — ห้ามแก้ทีละจุด
- [ ] **Step 4:** PASS + `run_tests.py`; replay diff คาด RKLB ≥ เงินสดสุทธิ/หุ้น, uncertainty RKLB ≥ Very High → `--update`
- [ ] **Step 5:** build_skill + commit `fix(engine): floor young-firm value at liquidation; young uncertainty >= Very High (#1)`

---

### Task 5: B2 — ไม่นับความเสี่ยงซ้ำสำหรับ young firm

**Files:**
- Modify: `domain/engine/deep_v82.py` `_bottom_up_beta` (~1301–1322), `_early_stage` (~1265)
- Test: `tests/test_fixes_2026_11_14.py`

**Interfaces:**
- เปลี่ยนพฤติกรรม: early-stage ที่มี industry beta → ใช้ industry bottom-up beta เป็น discount rate; regression beta เก็บเป็น cross-check ใน flag และ `beta_regression` (field เดิมที่ ~2442); `beta_source` เป็น `"bottom-up industry (early stage: failure risk carried by p_survival)"`
- ไม่แตะ financial-firm path (~1288)

- [ ] **Step 1: เทสต์**
  - `test_early_stage_uses_industry_beta`: reg 2.61, industry 1.2 → beta ที่ใช้ = levered industry beta; flag มี "2.61"
  - `test_no_industry_beta_keeps_regression`: industry `None` → regression ตามเดิม
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** ลบ branch "higher regression beta is used" ใน `_bottom_up_beta`; ปรับ docstring ให้ตรง young_dcf ("failure risk is separate from the discount rate")
- [ ] **Step 4:** PASS + `run_tests.py`; replay diff คาด RKLB, HIMS, TMDX, AXON, TSLA WACC ลด FV ขึ้น → `--update`
- [ ] **Step 5:** build_skill + commit `fix(engine): early-stage firms use industry beta; failure risk stays in p_survival (B2)`

---

### Task 6: B1 + B6 — สถานะ "ไม่จัดอันดับ" และคำแนะนำชุดเดียว

**Files:**
- Modify: `domain/engine/deep_v82.py` `_overall` (~2369), `_signal` (~1085), ส่ง uncertainty/anchor เข้า `_overall`
- Modify: `domain/advice.py` `_ALLOWED` (~148)
- Modify: `domain/risk/targets.py` `budget` (~33)
- Modify: `domain/risk/portfolio.py` `rebalance` (~363)
- Modify: `pipeline/risk_report.py` `build` (~652, ~724)
- Modify: `static/js/format.js` (RECO_COLOR/RECO_LABEL ~5), `static/js/card.js` (RGROUP ~147, groups ~154, filter ~183), `static/js/risk.js` (`rebalanceHTML` ~249)
- Test: `tests/test_fixes_2026_11_14.py` + อัปเดต golden ใน `tests/ui_snapshot.py`

**Interfaces:**
- Consumes: `uncertainty["rating"]` (Task 4), young `"floored"` (Task 4), `dcf_terminal_share_pct` / young `terminal_share_of_value_pct`, flag TTM (Task 2)
- Produces: `deep_v82.not_rated_reasons(unc, anc, f) -> list[str]`; reco `"NOT RATED"` เมื่อ list ไม่ว่าง (เหตุผลอยู่ใน flag); `_signal("NOT RATED") is None`; `targets.budget` คืน `(None, "ไม่จัดอันดับ → คงน้ำหนักเดิม")`; `risk_report.build` คืน `"rebalance"` ที่สร้างจาก `targets` rows (ฟังก์ชันใหม่ `portfolio.trades_from_targets(target_rows, weights, sizing_rows, total_value, cost_bps) -> dict` ในรูป dict เดิมของ `rebalance` เพื่อให้ `_after_rebalance` และ `rebalanceHTML` ใช้ต่อได้)

- [ ] **Step 1: เทสต์**
  - `test_not_rated_each_reason`: 4 เคส (Extreme / floored / terminal 125% / TTM flag) → `"NOT RATED"`; เคสปกติ → rating เดิม
  - `test_signal_not_rated_is_none` (Review Focus 3)
  - `test_advice_not_rated_allows_hold_only`: `align("NOT RATED", "REDUCE")` → `"HOLD"`
  - `test_targets_keep_weight_for_not_rated`: budget `None`, row action ≠ SELL
  - `test_rebalance_matches_targets`: ทุก ticker ที่ targets บอก BUY/SELL นอก band ปรากฏใน rebalance ทิศทางเดียวกัน; ใน band → ไม่มี trade
  - `test_existing_sell_still_exits`: SELL / AVOID ปกติยังได้ budget 0 (เทสต์เดิมใน `test_p2c_2026_11.py` ต้องผ่าน)
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** implement
  - `_overall` เรียก `not_rated_reasons` หลัง cap เดิม; composite ยังคำนวณและแสดง (ให้เห็นว่าพื้นฐานเป็นอย่างไร) แต่ reco = `"NOT RATED"`
  - `advice._ALLOWED["NOT RATED"] = ({"HOLD", "HOLD?", "WATCH"}, "HOLD")`
  - `targets._NO_BUDGET` คงเดิม; `budget` เช็ก `"NOT RATED"` ก่อน (คืน None) — ระวัง substring: ใช้เทียบเต็มคำ
  - `rebalance` เดิมไม่ถูกลบในขั้นนี้แต่ไม่ถูกเรียกจาก `risk_report` แล้ว; ลบใน commit เดียวกันหาก grep ไม่พบ caller อื่น (principle: migrate callers แล้วลบ)
  - UI: label `"ไม่จัดอันดับ"`, สี `--muted`, group/filter ใหม่ใน card.js; ตาราง Rebalance หัวข้อบอกว่า "จาก Targets"
- [ ] **Step 4:** PASS + `run_tests.py`; `ui_snapshot.py` อัปเดต golden หลังดูหน้าจอจริงใน browser; replay `--update`
- [ ] **Step 5:** bump BUILD `2026-11-14b`, build_skill, อัปเดต How-to/Ref ส่วนที่อธิบาย rating (เทสต์ doc ต้องผ่าน) + commit `feat(reco): NOT RATED status; rebalance derived from targets (B1, B6)`

---

### Task 7: A3 + A4 — ฟอร์ม Add holding และ ledger start

**Files:**
- Modify: `static/js/portfolio.js` `addHolding` (~48)
- Modify: `domain/ledger.py` `replay` (~83–89)
- Test: `tests/test_fixes_2026_11_14.py`

- [ ] **Step 1: เทสต์**
  - `test_ledger_start_from_first_deposit`: DEPOSIT 2026-01-02 แล้ว BUY → `start == "2026-01-02"`, XIRR ไม่เป็น None
  - `test_ledger_open_start_unchanged`: เริ่มด้วย OPEN → ค่าเดิม
  - JS: ตรวจด้วย browser — ledger active แล้ว Add → ข้อความ error จาก server, ตารางไม่ถูก render ทับ
- [ ] **Step 2:** FAIL
- [ ] **Step 3:** ledger: `start = start or day` ย้ายออกจาก `if typ == "OPEN"` ให้ใช้กับทุกรายการ; `addHolding`: `if(!r.ok){msg.textContent='✗ '+(d.error||r.status);return;}` ก่อน `renderPortfolio` (แบบเดียวกับ `saveCash`)
- [ ] **Step 4:** PASS + `run_tests.py` + ทดสอบฟอร์มจริงบน `localhost:8000`
- [ ] **Step 5:** bump BUILD `2026-11-14c` + commit `fix(ui,ledger): addHolding checks r.ok; ledger start = first transaction (A3, A4)`

---

## สิ่งที่ไม่อยู่ในแผนนี้

A5–A10, B3 (margin ไต่ 10 ปี), B4, B7–B11, C1–C3 — ทำรอบถัดไป
