# 🔎 Repo Audit — Portfolio DEEP v7.3 (full-stack review)

อัปเดต: หลังเปลี่ยน Google Drive persistence เป็น OAuth (มิ.ย. 2026)

---

## ✅ จำเป็น — Core runtime (ห้ามลบ)

| ไฟล์ / โฟลเดอร์ | หน้าที่ |
|---|---|
| `app.py` | FastAPI server + endpoints + auth (APP_TOKEN) |
| `index.html` | หน้าเว็บ dashboard (3 แท็บ) — frontend ทั้งหมดในไฟล์เดียว |
| `config.py` | ค่าคงที่/ENV (FMP key, SEC contact, DEEP_VERSION, paths) |
| `store.py` | persistence: load/save `portfolio.json` + เรียก drive_pull/push |
| `sources/` | `sec_edgar.py` (financials หลัก), `fmp.py` (profile), `yahoo.py` (fwd EPS/momentum), `gdrive_store.py` (Drive sync), `__init__.py` |
| `domain/` | `facts.py`, `indicators.py`, `engine/contract.py`, `engine/deep_v73.py` (เครื่องคำนวณ DEEP), `engine/__init__.py` (registry) |
| `pipeline/` | `normalize.py`, `validate.py`, `rev_track.py`, `refresh.py` (orchestration) |
| `requirements.txt` · `render.yaml` · `Procfile` | deploy config |

## 🛠 จำเป็นเฉพาะตอน dev/test (เก็บไว้ อย่าลบถ้ายัง maintain โค้ด)

| ไฟล์ | หน้าที่ |
|---|---|
| `tests/test_*.py` + `tests/fixtures/` | regression net — ล็อกตัวเลขจริง (AVGO/ABBV/ORCL/NVO/MSFT) กันบั๊ก v1 กลับมา. README สั่งให้ commit fixtures |
| `capture.py` | ดึง fixtures จริงครั้งเดียว |
| `verify.py` | เช็คความถูกต้องข้อมูลหุ้น (CHECK!) |
| `run_tests.py` | ตัวรันชุดเทสต์ |
| `get_gdrive_token.py` | สคริปต์ OAuth ครั้งเดียว (ตอนนี้อยู่นอก repo — แนะนำ `git add` เก็บไว้ในอนาคต) |

## 🗑 ลบได้ทันที — generated/junk (อยู่ใน .gitignore อยู่แล้ว, ไม่กระทบ repo)

| รายการ | ขนาด | เหตุผล |
|---|---|---|
| `__pycache__/` ทุกโฟลเดอร์ (`*.pyc`) | ~0.3MB | bytecode ของ Python สร้างใหม่อัตโนมัติ (มีทั้ง 3.10 และ 3.14 ปนกัน) |
| `data/cache/` | **~20MB** | cache SEC companyfacts — โหลดใหม่ได้เมื่อใช้งาน |
| `data/portfolio.json` (local) | ~40KB | สำเนา local — ตอนนี้ backup อยู่บน Drive แล้ว (ลบในเครื่องได้ ไม่กระทบของจริง) |

> รวมลบได้ ~21MB. คำสั่งล้าง (ปลอดภัย):
> `Remove-Item -Recurse -Force .\__pycache__, .\domain\__pycache__, .\sources\__pycache__, .\pipeline\__pycache__, .\tests\__pycache__, .\data\cache`

## ⚠️ ล้าสมัย / ซ้ำซ้อน — docs

| ไฟล์ | สถานะ |
|---|---|
| `GOOGLE_DRIVE_SETUP.md` | **ล้าสมัย** (วิธี Service Account ใช้ไม่ได้บน Gmail ฟรี) → แก้เป็น stub ชี้ไปคู่มือใหม่แล้ว (จะลบทิ้งเลยก็ได้) |
| `GOOGLE_DRIVE_OAUTH_SETUP.md` | ✅ คู่มือใหม่ที่ถูกต้อง — **ยังไม่ได้ `git add`** (untracked) |
| `README.md` · `DESIGN.md` · `BEGINNER_GUIDE.md` · `architecture.mermaid` | ✅ อัปเดตให้ตรง OAuth แล้ว |
| `HOW_TO_APPLY.md` · `REVIEW.md` · `UPGRADE_ENGINE.md` | เก็บไว้ (เนื้อหาเฉพาะทาง ไม่ซ้ำ) |

## 🧹 หมายเหตุ Git ที่ต้องรู้

1. **ไฟล์ 73 ไฟล์ขึ้น "modified" แต่ไม่ได้แก้เนื้อหาจริง** — เป็นแค่ CRLF↔LF (ยืนยันด้วย `git diff --ignore-cr-at-eol` = ว่าง) → **อย่า `git add .`** มั่ว. เพิ่ม `.gitattributes` (ทำให้แล้ว) แล้วรัน `git add --renormalize .` ครั้งเดียวเพื่อเคลียร์
2. `GOOGLE_DRIVE_OAUTH_SETUP.md` ยัง untracked → ต้อง `git add`
3. `get_gdrive_token.py` ยังไม่อยู่ใน repo (อยู่โฟลเดอร์ googleauth) — จะเก็บเข้า repo ก็ดี (ไม่มีความลับในไฟล์นี้ แต่ `client_secret.json` **ห้าม** commit)

## 🏗 สถาปัตยกรรม (สรุป)
`sources → pipeline → domain(engine) → store(+Google Drive) → FastAPI → dashboard`
แยกชั้นชัด, engine สลับเวอร์ชันได้ (DEEP_VERSION), persistence มี Drive mirror กัน redeploy แล้ว — โครงสร้างดี เหมาะกับ single-user app
