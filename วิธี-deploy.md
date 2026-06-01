# 🚀 วิธี Deploy NEXLOAD บน Railway (ฟรี)

## ไฟล์ที่ต้องใช้
```
nexload-deploy/
├── nexload_web.py     ← ตัวแอพหลัก (แก้แล้ว)
├── requirements.txt   ← list ของ library
├── Dockerfile         ← สูตรการ build
├── railway.toml       ← config Railway
└── .gitignore         ← ไฟล์ที่ไม่ต้อง upload
```

---

## ขั้นตอนที่ 1 — สร้างบัญชี GitHub

1. เปิด https://github.com
2. กด **Sign up** สร้างบัญชีฟรี (ถ้ามีแล้วข้ามได้)

---

## ขั้นตอนที่ 2 — สร้าง Repository บน GitHub

1. กด **+** มุมขวาบน → **New repository**
2. ตั้งชื่อ: `nexload`
3. เลือก **Private** (แนะนำ เพื่อความปลอดภัย)
4. กด **Create repository**

---

## ขั้นตอนที่ 3 — Upload ไฟล์ขึ้น GitHub

1. ใน repository ที่สร้าง กด **uploading an existing file**
2. ลาก 4 ไฟล์นี้ขึ้นไป:
   - `nexload_web.py`
   - `requirements.txt`
   - `Dockerfile`
   - `railway.toml`
3. กด **Commit changes**

---

## ขั้นตอนที่ 4 — Deploy บน Railway

1. เปิด https://railway.app
2. กด **Login with GitHub**
3. กด **New Project** → **Deploy from GitHub repo**
4. เลือก repository `nexload`
5. Railway จะ build อัตโนมัติ รอ ~3-5 นาที

---

## ขั้นตอนที่ 5 — เปิดเว็บ / แชร์ลิงก์

1. หลัง deploy เสร็จ ไปที่แท็บ **Settings**
2. หัวข้อ **Domains** → กด **Generate Domain**
3. จะได้ลิงก์แบบ: `https://nexload-xxx.railway.app`
4. แชร์ลิงก์นี้ให้คนอื่นได้เลย! 🎉

---

## ⚠️ สิ่งสำคัญที่ต้องรู้

| หัวข้อ | รายละเอียด |
|--------|-----------|
| แผนฟรี | $5 credit/เดือน (~500 ชั่วโมง) |
| ไฟล์วิดีโอ | หายทุกครั้งที่ restart → ให้ download ทันทีหลังโหลด |
| yt-dlp อัปเดต | กด "อัปเดต yt-dlp" ในหน้า Settings ของแอพได้เลย |
| หลายคนใช้ | ได้ แต่ถ้าโหลดพร้อมกันจะช้า (server เดียว) |

---

## 🔄 อัปเดตโค้ดในอนาคต

1. แก้ไขไฟล์ใน GitHub
2. Railway จะ build และ deploy ใหม่อัตโนมัติ

---

## ❓ ปัญหาที่พบบ่อย

**Build ล้มเหลว** → เช็กว่า upload ครบ 4 ไฟล์หรือยัง

**เปิดเว็บแล้วขึ้น 502** → รอให้ build เสร็จก่อน (~5 นาที)

**โหลดวิดีโอไม่ได้** → กด "อัปเดต yt-dlp" ในหน้า Settings
