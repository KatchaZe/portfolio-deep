# ⚠️ DEPRECATED — ใช้ `GOOGLE_DRIVE_OAUTH_SETUP.md` แทน

> วิธีเดิมในไฟล์นี้ (Service Account + แชร์โฟลเดอร์) **ใช้กับ Gmail ส่วนตัวไม่ได้**
> เพราะ Service Account ไม่มี storage quota → ตอนเขียนไฟล์เจอ `403 storageQuotaExceeded`
> (อ่านได้ แต่เขียน/สร้างไฟล์ไม่ได้ = ไม่มี backup จริง)
>
> ✅ **วิธีที่ใช้งานได้จริง:** OAuth (login เป็นตัวคุณเอง) — ดู **[`GOOGLE_DRIVE_OAUTH_SETUP.md`](./GOOGLE_DRIVE_OAUTH_SETUP.md)**

---

## ทำไมต้องเปลี่ยน (สรุปสั้น)

| | Service Account (เดิม) | OAuth user (ใหม่) |
|---|---|---|
| สิทธิ์เขียนไฟล์ใน Drive ส่วนตัว | ❌ ไม่มีโควต้า → 403 | ✅ ใช้โควต้าของคุณ (เช่น 10TB) |
| เจ้าของไฟล์ที่สร้าง | service account (ไม่มีที่เก็บ) | ตัวคุณเอง |
| ใช้กับ Gmail ฟรี | ❌ (ต้อง Workspace + Shared Drive) | ✅ |
| env vars | `GDRIVE_SA_JSON` | `GDRIVE_OAUTH_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` |

โค้ด `sources/gdrive_store.py` ยังรองรับ Service Account ไว้เป็น fallback (legacy) แต่ทางที่
แนะนำและทดสอบแล้วว่าใช้ได้บน Render free tier คือ OAuth ตามคู่มือใหม่

👉 ไปที่ **[`GOOGLE_DRIVE_OAUTH_SETUP.md`](./GOOGLE_DRIVE_OAUTH_SETUP.md)**
