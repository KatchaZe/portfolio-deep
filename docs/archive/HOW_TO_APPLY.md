# วิธีนำการแก้ไขขึ้น GitHub / Render (HOW TO APPLY)

ขั้นตอนมาตรฐานเวลาแก้โค้ดในโฟลเดอร์นี้แล้วอยากให้ขึ้น production (Render auto-deploy).
เวอร์ชัน engine ปัจจุบัน = **DEEP v8.2** (`config.py → DEEP_VERSION="8.2"`).

---

## ขั้นตอนหลัก (ทำทุกครั้ง)

```bash
cd portfolio-app-v2            # repo ของคุณ
# 1) แก้ไฟล์ที่ต้องการ
python run_tests.py            # 2) ต้องเห็น ALL TEST SUITES PASSED OK (20 ชุด)
git add -A
git commit -m "อธิบายสั้นๆ ว่าทำอะไร"
git push                       # 3) Render จะ auto-deploy ให้เอง (ถ้าต่อ GitHub ไว้)
```

> ถ้า `run_tests.py` ขึ้น `FAILED:` → อย่า push. อ่าน error แล้วย้อนไปดูไฟล์ที่เพิ่งแก้ก่อน
> (วิธีอัปเกรด/แก้ที่พบบ่อย ดู `BEGINNER_GUIDE.md`).

---

## ไฟล์ที่มักแก้บ่อย (path ตรงกับ repo)

| งาน | ไฟล์ |
|---|---|
| สูตร DEEP / valuation | `domain/engine/deep_v82.py` (active) · `contract.py` · `__init__.py` (registry) |
| สลับเวอร์ชัน engine / ค่าคงที่ (ERP) | `config.py` |
| momentum (สัญญาณหลัก) | `domain/momentum.py` |
| Risk Desk (Allocation tab) | `domain/engine/risk.py` · `pipeline/risk_prices.py` |
| ดึง/รวม/ตรวจข้อมูล | `sources/*.py` · `pipeline/normalize.py` `validate.py` `refresh.py` |
| API | `app.py` (FastAPI) |
| หน้าเว็บ | `index.html` |
| เทสต์ | `tests/*.py` · `run_tests.py` |

> ถ้าจะส่งเป็น git patch แทนการ push ตรง: สร้างด้วย `git format-patch -1` แล้วฝั่งปลายทางใช้
> `git am < xxx.patch`; ถ้าติด conflict ให้กลับไปใช้วิธี commit/push ปกติด้านบน.

---

## อย่าลืม (ตอน deploy สาธารณะบน Render)

ตั้ง environment variables บน Render (Dashboard → Environment) — **ห้าม commit ค่าเหล่านี้ลง git**:

| ตัวแปร | จำเป็น? | ใช้ทำอะไร |
|---|---|---|
| `APP_TOKEN` | แนะนำมาก | รหัสล็อกแอป — เปิด `/?token=รหัส` ครั้งเดียว แล้วจำด้วย cookie 30 วัน |
| `SEC_CONTACT_EMAIL` | แนะนำ | อีเมลติดต่อสำหรับ SEC fair-access (ไม่ hard-code ในโค้ด) |
| `FMP_API_KEY` | ไม่บังคับ | sector/beta/price + consensus path จาก FMP (ไม่ใส่ก็ได้ — ใช้ Yahoo แทน) |
| `FINNHUB_API_KEY` / `ALPHAVANTAGE_API_KEY` | ไม่บังคับ | แหล่ง EPS-surprise เสริม (cross-check) |
| `GDRIVE_OAUTH_CLIENT_ID` / `GDRIVE_OAUTH_CLIENT_SECRET` / `GDRIVE_OAUTH_REFRESH_TOKEN` | แนะนำ | persistence ขึ้น Google Drive — พอร์ตไม่หายตอน redeploy (ดู `GOOGLE_DRIVE_OAUTH_SETUP.md`) |

> 📖 อ่าน `BEGINNER_GUIDE.md` (วิธีรัน/แก้/อัปเกรด engine) · `README.md` (ภาพรวม) · `DESIGN.md` (สถาปัตยกรรมเต็ม)
