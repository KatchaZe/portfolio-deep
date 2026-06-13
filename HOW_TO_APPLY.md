# วิธีนำการแก้ไขนี้ขึ้น GitHub (HOW TO APPLY)

มี 2 วิธี — เลือกอันไหนก็ได้ ผลเหมือนกัน

---

## วิธี A (ง่ายสุด) — ก๊อปไฟล์ทับ แล้ว push เอง

ไฟล์ในโฟลเดอร์นี้คือไฟล์ที่แก้แล้ว วางทับโครงสร้างเดิมได้ตรงๆ (path ตรงกัน):

```
app.py · config.py · render.yaml · run_tests.py · index.html
README.md · DESIGN.md · REVIEW.md · BEGINNER_GUIDE.md (ไฟล์ใหม่)
pipeline/refresh.py · pipeline/normalize.py
domain/engine/deep_v73.py
sources/yahoo.py
tests/test_app_fixes.py (ไฟล์ใหม่)
```

ขั้นตอน:
```bash
cd portfolio-deep              # repo เดิมของคุณ
# คัดลอกไฟล์จากโฟลเดอร์นี้ทับ (path เดียวกัน)
python run_tests.py            # ต้องเห็น ALL TEST SUITES PASSED ✅
git add -A
git commit -m "Hardening 2: fix freeze + what-if crash, lock-free fetch, APP_TOKEN auth, warning flags, beginner guide"
git push
```
Render จะ auto-deploy ให้เอง (ถ้าต่อ GitHub ไว้)

---

## วิธี B — ใช้ git patch (เก็บ commit message ให้อัตโนมัติ)

```bash
cd portfolio-deep
git am < hardening2.patch      # ใช้ไฟล์ hardening2.patch ในโฟลเดอร์นี้
python run_tests.py            # ตรวจ
git push
```
ถ้า `git am` ติด conflict (เพราะ repo คุณต่างจากที่ผมโคลนมา) ให้ใช้ **วิธี A** แทน

---

## อย่าลืม (ตอน deploy สาธารณะ)

ตั้ง environment variables บน Render (Dashboard → Environment) — **ห้าม commit ค่าเหล่านี้ลง git**:

| ตัวแปร | จำเป็น? | ใช้ทำอะไร |
|---|---|---|
| `APP_TOKEN` | แนะนำมาก | รหัสล็อกแอป — เปิด `/?token=รหัส` ครั้งเดียว แล้วจำด้วย cookie |
| `SEC_CONTACT_EMAIL` | แนะนำ | อีเมลติดต่อสำหรับ SEC (เดิม hard-code อีเมลส่วนตัวไว้ — ย้ายออกแล้ว) |
| `FMP_API_KEY` | ไม่บังคับ | sector/beta จาก FMP (ไม่ใส่ก็ได้) |

> 📖 อ่าน `BEGINNER_GUIDE.md` สำหรับวิธีรัน/แก้/อัปเกรด engine แบบละเอียด
