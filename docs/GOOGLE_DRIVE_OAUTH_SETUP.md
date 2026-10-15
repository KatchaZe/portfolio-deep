# ☁️ GOOGLE DRIVE SETUP (วิธี OAuth) — เก็บพอร์ตไม่ให้หาย จริงๆ สักที

> **ทำไมต้องเปลี่ยนวิธี?**
> วิธีเดิม (Service Account + แชร์โฟลเดอร์) **ใช้กับ Gmail ส่วนตัวไม่ได้** เพราะ
> Service Account ไม่มีพื้นที่เก็บไฟล์ของตัวเอง → ตอนเขียนไฟล์จะเจอ error
> `403 storageQuotaExceeded` (อ่านได้ แต่เขียนไม่ได้ = ไม่มี backup จริง)
>
> **วิธีใหม่:** ให้แอป login **เป็นตัวคุณเอง** (OAuth) → ไฟล์ไปอยู่ใน Drive ของคุณ
> ใช้พื้นที่ของคุณเอง (มี 10TB) → เขียนได้ → ข้อมูลไม่หายตอน redeploy ✅
>
> ⏱️ ~15 นาที · 💰 ฟรีทั้งหมด

---

## 🧭 ภาพรวม

```
1. สร้าง OAuth client ID (Desktop app)   ← กุญแจให้แอป login แทนคุณ
2. เพิ่มตัวเองเป็น Test user             ← อนุญาตให้บัญชีคุณใช้ได้
3. รัน get_gdrive_token.py บนเครื่อง     ← เปิดเบราว์เซอร์ กดอนุญาต → ได้ refresh token
4. เอาโค้ดใหม่ขึ้น GitHub                ← gdrive_store.py + requirements.txt
5. ตั้ง 3 env vars บน Render             ← เอา token ไปบอกแอป
6. Deploy แล้วทดสอบ                      ← เพิ่มหุ้น → redeploy → หุ้นยังอยู่
```

---

## 📝 ขั้นตอนละเอียด

### สเต็บ 1 — สร้าง OAuth client ID
1. เข้า **https://console.cloud.google.com** เลือกโปรเจกต์เดิม (`portfolio-deep`)
2. เมนูซ้าย → **APIs & Services → Credentials**
3. กดด้านบน **+ Create credentials → OAuth client ID**
4. **Application type** เลือก **Desktop app** → ตั้งชื่ออะไรก็ได้ → **Create**
5. กด **Download JSON** → ได้ไฟล์มา **เปลี่ยนชื่อเป็น `client_secret.json`**
   แล้ววางไว้ **โฟลเดอร์เดียวกับ** `get_gdrive_token.py`

> 💡 ถ้ายังไม่เคยเปิด Google Drive API ให้พิมพ์ค้น "Google Drive API" → **Enable** ก่อน

### สเต็บ 2 — เพิ่มตัวเองเป็น Test user
1. **APIs & Services → OAuth consent screen**
2. ถ้าถาม User Type เลือก **External** → กรอกชื่อแอป + อีเมลคุณ → Save
3. หา **Test users** → **+ Add users** → ใส่ `katcha2002@gmail.com` → Save
   (ปล่อยให้แอปอยู่สถานะ "Testing" ได้ ไม่ต้องส่งตรวจ)

### สเต็บ 3 — รันสคริปต์ขอ token (บนเครื่องคุณ)
เปิด PowerShell ในโฟลเดอร์โปรเจกต์ แล้วพิมพ์:
```powershell
pip install google-auth-oauthlib google-api-python-client
python get_gdrive_token.py
```
- เบราว์เซอร์จะเด้งขึ้นมา → เลือกบัญชี Google ของคุณ
- ถ้าขึ้นเตือน "Google hasn't verified this app" → **Continue** (เพราะเป็นแอปคุณเอง)
- กด **Allow / อนุญาต**
- สคริปต์จะพิมพ์ค่า 3 ตัวออกมา → **ก๊อปเก็บไว้**

```
GDRIVE_OAUTH_CLIENT_ID     = xxxx.apps.googleusercontent.com
GDRIVE_OAUTH_CLIENT_SECRET = GOCSPX-xxxx
GDRIVE_OAUTH_REFRESH_TOKEN = 1//xxxx
```

> ⚠️ refresh token = กุญแจเข้า Drive คุณ → **ห้าม commit ลง git, ห้ามแชร์**

### สเต็บ 4 — เอาโค้ดใหม่ขึ้น GitHub
แทนที่ไฟล์เดิมด้วยไฟล์ใหม่ 2 ไฟล์ แล้ว push:
- `sources/gdrive_store.py`  (ไฟล์ที่แก้แล้ว)
- `requirements.txt`         (ไฟล์ที่เพิ่ม library แล้ว)
- (จะใส่ `get_gdrive_token.py` ขึ้น repo ด้วยก็ได้ ไม่มีความลับในไฟล์นี้)

```powershell
git add sources/gdrive_store.py requirements.txt get_gdrive_token.py
git commit -m "Drive persistence via OAuth (fix service-account quota issue)"
git push
```

### สเต็บ 5 — ตั้ง env vars บน Render
Render Dashboard → service → **Environment → + Add Environment Variable** ใส่ 3 ตัว:

| Key | Value |
|---|---|
| `GDRIVE_OAUTH_CLIENT_ID` | ค่าจากสเต็บ 3 |
| `GDRIVE_OAUTH_CLIENT_SECRET` | ค่าจากสเต็บ 3 |
| `GDRIVE_OAUTH_REFRESH_TOKEN` | ค่าจากสเต็บ 3 |

- `GDRIVE_FOLDER_ID` — จะเก็บไว้ก็ได้ (ไฟล์จะพยายามลงโฟลเดอร์ `portfolio-data`
  ถ้าลงไม่ได้จะ fallback ไปไว้ที่ My Drive แทนอัตโนมัติ)
- `GDRIVE_SA_JSON` — **ลบทิ้งได้เลย** ไม่ใช้แล้ว
- กด **Save changes** (Render จะ redeploy ให้เอง)

### สเต็บ 6 — ทดสอบของจริง ✅
1. เข้าแอป → เพิ่มหุ้น 1 ตัว (เช่น NVDA)
2. เปิด Google Drive → ควรเห็นไฟล์ **`portfolio.json`** โผล่มา 🎉
3. ที่ Render กด **Manual Deploy → Deploy latest commit** (จำลอง restart)
4. รอ deploy เสร็จ → เข้าแอปใหม่ → **หุ้นต้องยังอยู่** ✅

---

## 🆘 ปัญหาที่อาจเจอ

| อาการ | วิธีแก้ |
|---|---|
| สคริปต์ไม่พิมพ์ refresh token | ไปลบสิทธิ์เก่าที่ https://myaccount.google.com/permissions แล้วรันใหม่ |
| Logs ขึ้น `Drive push failed` | เช็คว่า 3 env ครบ + ค่าถูก (โดยเฉพาะ refresh token ก๊อปครบ) |
| Logs ขึ้น `invalid_grant` | refresh token หมดอายุ/ถูกเพิกถอน → รัน `get_gdrive_token.py` ใหม่ |
| ไฟล์ไม่อยู่ในโฟลเดอร์ `portfolio-data` | ปกติ — fallback ไป My Drive ค้นชื่อ `portfolio.json` เจอแน่ |

---

## 🔍 วิธีดูว่าทำงานหรือยัง (จาก Render Logs)
- ✅ สำเร็จ: `INFO portfolio.gdrive Drive: pushed portfolio.json`
- ✅ ตอนเปิดแอป: `Drive: pulled portfolio.json (NNN bytes)`
- ❌ พลาด: `Drive push failed (kept local copy only): ...` (ดูเหตุผลต่อท้าย)
