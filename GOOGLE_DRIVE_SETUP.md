# ☁️ GOOGLE DRIVE SETUP — เก็บพอร์ตไม่ให้หาย (คู่มือมือใหม่ ทีละสเต็บ)

> **ปัญหา:** Render free tier ลบข้อมูล `data/portfolio.json` ทุกครั้งที่ deploy/restart
> **วิธีแก้:** ให้แอป sync ไฟล์นี้ขึ้น Google Drive อัตโนมัติ → ข้อมูลไม่หาย เข้าได้ทุกที่
>
> ⏱️ ใช้เวลา ~15 นาที ทำครั้งเดียวจบ · 💰 ฟรีทั้งหมด

---

## 🧭 ภาพรวม (ทำอะไรบ้าง)

```
1. สร้าง Google Cloud project          ← ที่เก็บการตั้งค่า
2. เปิด Google Drive API               ← อนุญาตให้แอปคุยกับ Drive
3. สร้าง "Service Account"             ← บัญชีหุ่นยนต์ที่แอปใช้แทนตัวคุณ
4. โหลด key JSON                       ← กุญแจให้แอปล็อกอิน
5. สร้างโฟลเดอร์ใน Drive + แชร์ให้หุ่นยนต์  ← ที่เก็บไฟล์จริง
6. ตั้ง 2 env vars บน Render            ← เอากุญแจ+โฟลเดอร์ไปบอกแอป
```

> 💡 **Service Account คืออะไร?** คือ "บัญชี Google แบบหุ่นยนต์" ที่ไม่ใช่ตัวคุณ
> มันมีอีเมลของตัวเอง (เช่น `xxx@yyy.iam.gserviceaccount.com`) แอปจะล็อกอินด้วยบัญชีนี้
> เพื่อเขียนไฟล์ลง Drive แทนการใช้บัญชี Google ส่วนตัวของคุณ (ปลอดภัยกว่า)

---

## 📝 ขั้นตอนละเอียด

### สเต็บ 1 — สร้าง Google Cloud project
1. เข้า **https://console.cloud.google.com**
2. มุมบนซ้าย คลิก dropdown ชื่อโปรเจกต์ → **"New Project"**
3. ตั้งชื่อ เช่น `portfolio-deep` → กด **Create**
4. รอสักครู่ แล้วเลือกโปรเจกต์นี้ให้เป็น active (dropdown บนซ้าย)

### สเต็บ 2 — เปิด Google Drive API
1. ช่องค้นหาด้านบน พิมพ์ **"Google Drive API"** → คลิกผลลัพธ์
2. กดปุ่ม **"Enable"** (สีน้ำเงิน) → รอจนเขียว

### สเต็บ 3 — สร้าง Service Account
1. ช่องค้นหา พิมพ์ **"Service Accounts"** → คลิกเข้าไป
2. กด **"+ Create Service Account"** (ด้านบน)
3. ช่อง **Service account name** ใส่ เช่น `portfolio-writer` → กด **Create and Continue**
4. ช่อง **Grant this service account access** → **ข้ามได้** กด **Continue**
5. ขั้นสุดท้าย กด **Done**
6. คุณจะเห็นบัญชีใหม่ในรายการ — **ก๊อปอีเมลของมันเก็บไว้** (รูปแบบ `portfolio-writer@portfolio-deep.iam.gserviceaccount.com`) จะใช้ในสเต็บ 5

### สเต็บ 4 — โหลด key JSON
1. คลิกที่ service account ที่เพิ่งสร้าง
2. ไปแท็บ **"Keys"** → กด **"Add Key"** → **"Create new key"**
3. เลือก **JSON** → กด **Create**
4. ไฟล์ `.json` จะถูกดาวน์โหลดลงเครื่อง → **เก็บให้ดี ห้ามให้ใครเห็น** (นี่คือกุญแจ)

> ⚠️ **สำคัญ:** ห้ามอัปไฟล์นี้ขึ้น GitHub เด็ดขาด — ถ้าหลุดคนอื่นเขียน Drive คุณได้

### สเต็บ 5 — สร้างโฟลเดอร์ Drive + แชร์ให้หุ่นยนต์
1. เปิด **https://drive.google.com**
2. สร้างโฟลเดอร์ใหม่ เช่น `portfolio-data`
3. เปิดเข้าไปในโฟลเดอร์ → ดูที่ URL จะเป็นแบบนี้:
   ```
   https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz
                                          └──────── นี่คือ FOLDER_ID ────────┘
   ```
   **ก๊อป FOLDER_ID เก็บไว้** (ส่วนหลัง `/folders/`)
4. คลิกขวาที่โฟลเดอร์ → **Share** → วางอีเมล service account (จากสเต็บ 3)
   → ตั้งสิทธิ์เป็น **Editor** → กด **Send**
   (ถ้าขึ้นเตือนว่าเป็นอีเมลภายนอก กด Share ต่อได้)

### สเต็บ 6 — ตั้ง env vars บน Render
1. เปิดไฟล์ JSON (จากสเต็บ 4) ด้วย Notepad → **ก๊อปเนื้อหาทั้งหมด**
2. เข้า **Render Dashboard → service → Environment → + Add Environment Variable**
3. ใส่ 2 ตัว:

| Key | Value |
|---|---|
| `GDRIVE_SA_JSON` | วางเนื้อหาไฟล์ JSON ทั้งก้อน (ทั้งหมดในบรรทัดเดียว) |
| `GDRIVE_FOLDER_ID` | FOLDER_ID จากสเต็บ 5 |

4. กด **Save Changes → "Save and deploy"** → รอ deploy เสร็จ 🟢

---

## ✅ เช็คว่าทำงาน

1. เข้าแอป → เพิ่มหุ้น 1 ตัว (เช่น NVDA)
2. เปิด Google Drive → โฟลเดอร์ `portfolio-data` → **ควรเห็นไฟล์ `portfolio.json` โผล่มา** 🎉
3. ทดสอบของจริง: ที่ Render กด **Manual Deploy → Deploy latest commit** (จำลอง restart)
4. รอ deploy เสร็จ → เข้าแอปใหม่ → **หุ้น NVDA ต้องยังอยู่** ✅ (เมื่อก่อนจะหาย)

---

## 🆘 ปัญหาที่อาจเจอ

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| ไฟล์ไม่โผล่ใน Drive | ยังไม่ได้แชร์โฟลเดอร์ให้ service account | ทำสเต็บ 5 ข้อ 4 ให้ครบ (Editor) |
| Logs ขึ้น "Drive push failed" | JSON วางไม่ครบ/ผิด หรือ FOLDER_ID ผิด | ก๊อป JSON ใหม่ทั้งก้อน เช็ค FOLDER_ID |
| Logs ขึ้น "Drive libs missing" | requirements ยังไม่มี google libs | เช็คว่า push `requirements.txt` ใหม่แล้ว (มี `google-api-python-client`) |
| ข้อมูลยังหาย | env vars ไม่ถูกอ่าน | เช็คชื่อ Key เป๊ะ: `GDRIVE_SA_JSON`, `GDRIVE_FOLDER_ID` |
| ทุกอย่างเงียบ ไม่มี error แต่ไม่ sync | ไม่ได้ตั้ง env (แอปรันโหมด local ปกติ) | ตั้ง env vars ให้ครบ 2 ตัว แล้ว redeploy |

> 💡 **โหมดปลอดภัย:** ถ้าไม่ตั้ง env 2 ตัวนี้ แอปจะทำงานแบบ local ปกติทุกอย่าง
> (เก็บลงไฟล์ในเครื่อง ไม่ sync Drive) — ไม่พัง ไม่ error

---

## 🔒 ความปลอดภัย (อ่านสักนิด)

- key JSON = กุญแจเขียน Drive → **ห้าม commit ลง git, ห้ามแชร์**
- service account เข้าถึงได้**เฉพาะโฟลเดอร์ที่คุณแชร์ให้**เท่านั้น (ไม่เห็น Drive ส่วนตัวอื่นของคุณ)
- ถ้ากุญแจหลุด: เข้า Google Cloud → Service Accounts → Keys → ลบ key เก่า → สร้างใหม่
- scope ที่ใช้คือ `drive.file` = แตะได้เฉพาะไฟล์ที่แอปสร้างเอง (แคบสุด ปลอดภัยสุด)

---

## 🔁 ถ้าอยากเลิกใช้ Drive (กลับไป local อย่างเดียว)

ที่ Render → Environment → ลบ `GDRIVE_SA_JSON` กับ `GDRIVE_FOLDER_ID` → Save and deploy
แอปจะกลับไปโหมด local ทันที (ข้อมูลล่าสุดที่ sync ไว้ยังอยู่ใน Drive ให้ดาวน์โหลดเองได้)

---

*เอกสารเกี่ยวข้อง: `RENDER_DEPLOY_GUIDE.md` · `BEGINNER_GUIDE.md` · `README.md`*
