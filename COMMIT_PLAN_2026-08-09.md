# คำสั่ง add / commit — 2026-08-09

แบ่ง **2 commit** เพราะสองก้อนนี้ review คนละแบบ:
ก้อนแรก *เปลี่ยนตัวเลข* (ต้องอ่าน replay diff) · ก้อนสอง *ไม่เปลี่ยนตัวเลขเลย* (เป็นตาข่ายกันพลาด)

ทดสอบแล้วว่า **commit 1 ยืนได้ด้วยตัวเอง** — เทสต์ชุดเดิม 46 ชุดผ่านหมดโดยไม่ต้องพึ่ง commit 2

---

## 0) เตรียม — ให้ `.coverage` ไม่หลุดเข้า repo

```bash
cd "C:\Users\Katcha\Documents\Claude\Projects\Stock Screening\portfolio-app-v2"
echo .coverage>> .gitignore
echo htmlcov/>> .gitignore
```

ตรวจสถานะก่อน:

```bash
git status --short
git log --oneline -1
```

---

## 1) Commit ที่ 1 — เครื่องยนต์คำนวณ (เปลี่ยนตัวเลข)

```bash
git add domain/engine/deep_v82.py domain/facts.py domain/trend.py domain/pead.py
git add pipeline/refresh.py pipeline/normalize.py pipeline/screen.py pipeline/consensus.py
git add sources/sec_edgar.py index.html
git add tests/test_review3.py tests/test_review3d.py tests/test_trend.py
git add tests/test_pillars_a.py tests/test_pillars_b.py tests/test_screen.py
git add CODE_REVIEW_2026-08-08_ROUND3.md ARCHITECTURE_PLAN_2026-08-08.md
```

```powershell
git commit -F ..\commit1.txt
```

> **อย่าใช้ `-m "..." -m ""` บน PowerShell** — PowerShell 5.1 **ตัดอาร์กิวเมนต์สตริงว่างทิ้ง**
> ก่อนส่งให้ git · git จึงเห็น `-m` ติดกันสองตัว แล้วกลืนบรรทัดถัดไปเป็น `-m` value
> ส่วนบรรทัดหลังจากนั้นกลายเป็น **pathspec** →
> `error: pathspec 'D1-D4 ...' did not match any file(s) known to git`
> ใช้ `-F <ไฟล์>` แทน จบทั้งปัญหาสตริงว่างและปัญหา quoting

---

## 2) Commit ที่ 2 — ตาข่ายกันพลาด (ไม่เปลี่ยนตัวเลข)

```bash
git add domain/contracts.py pipeline/dataquality.py pipeline/validate.py
git add tests/test_contracts.py tests/test_invariants.py tests/test_dataquality.py
git add tests/replay_snapshot.py tests/replay_baseline.json
git add run_tests.py DESIGN.md REVIEW_PROCESS.md COMMIT_PLAN_2026-08-09.md .gitignore
```

```powershell
git commit -F ..\commit2.txt
```

เสร็จแล้วลบไฟล์ข้อความทิ้ง (มันอยู่ **นอก** repo อยู่แล้ว จึงไม่โผล่ใน `git status`):

```powershell
Remove-Item ..\commit1.txt, ..\commit2.txt
```

---

## 3) ตรวจหลัง commit

```bash
python run_tests.py
git log --oneline -3
git status --short
```

`git status` ควรเหลือแค่ไฟล์ที่ ignore ไว้ (`data/`, `.coverage`, `__pycache__`)

---

## ⚠️ ก่อนเชื่อตัวเลขใหม่ — ยังต้องทำอีก 4 ขั้น

commit แล้วไม่ได้แปลว่าตัวเลขใหม่ถูก · `pipeline/refresh.py` มี **coverage 0%** และ `sources/fmp.py` **8%** —
สอง path ที่เพิ่งแก้เรื่อง fallback งบเก่า **ยังไม่เคยรันจริง**

1. **refresh ทีละ 2–3 ตัวก่อน ไม่ใช่ทั้งพอร์ต**
   `MSFT` (ปกติ) · `TSM` (งบเก่า — ทดสอบ fallback) · `NVO` (DKK — ทดสอบ FX)
2. `python -m tests.replay_snapshot` → **อ่านทุกบรรทัดที่ขยับ อธิบายให้ได้ทีละบรรทัด**
3. เปิดหน้าเว็บ ดูว่า **แถบเทรนด์ 5 ปี** ขึ้นจริง และป้าย **"วัดไม่ได้"** ใน GARP screen ขึ้นจริง
4. ถ้าผ่านหมด → refresh ทั้งพอร์ต แล้ว
   `python -m tests.replay_snapshot --update` และ commit baseline แยกอีกก้อน:

```bash
git add tests/replay_baseline.json
git commit -m "chore(replay): accept baseline after the first live refresh on the new engine"
```

---

## หมายเหตุ

* `data/portfolio.json` ถูก **gitignore** ไว้แล้ว — ข้อมูลพอร์ตจริงไม่ขึ้น repo
* `tests/replay_baseline.json` **ขึ้น repo** และมีแต่ผลวิเคราะห์ (คะแนน · FV · ROIC) —
  ตรวจแล้ว **ไม่มีจำนวนหุ้นหรือต้นทุน** · แต่มันเปิดเผยว่าคุณติดตามหุ้นตัวไหนบ้าง ถ้า repo เป็น public ให้พิจารณาก่อน
* ถ้าอยากย้อน: `git revert <hash>` ทีละก้อนได้ เพราะแยก commit ไว้แล้ว
