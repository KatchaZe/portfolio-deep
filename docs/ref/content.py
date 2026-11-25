"""The Ref page content, in on-screen order, and the labels that need no entry."""
from docs.ref.content_a import SECTIONS_A
from docs.ref.content_b import SECTIONS_B
from docs.ref.content_c import SECTIONS_C
from docs.ref.content_scout import SCOUT

# the Sector scout tab sits after Performance (LEDGER), before the glossary
SECTIONS = SECTIONS_A + SECTIONS_B + SECTIONS_C[:5] + (SCOUT,) + SECTIONS_C[5:]

# Inventory labels with no meaning of their own beyond the word: generic column heads,
# edit/delete controls and screen-reader names of controls the Ref already explains.
CHROME = {
    "Ticker": "หัวคอลัมน์ชื่อหุ้น", "Sector": "หัวคอลัมน์ sector", "หุ้น": "หัวคอลัมน์จำนวนหุ้น",
    "ราคา": "หัวคอลัมน์ราคา", "บันทึก": "ปุ่มบันทึกฟอร์ม", "ยกเลิกแก้ไข": "ปุ่มยกเลิกฟอร์ม",
    "แก้": "ปุ่มแก้รายการ", "ลบ": "ปุ่มลบรายการ", "แก้ ลบ": "ปุ่มแก้และลบในแถวเดียว",
    "ลบรายการ …": "ชื่อปุ่มลบสำหรับ screen reader", "ลบ … ออกจาก watchlist": "ชื่อปุ่มลบสำหรับ screen reader",
    "กรองตาม rating": "ชื่อกลุ่มปุ่มกรองสำหรับ screen reader (ดู filter-rating)",
    "ที่มา": "หัวคอลัมน์ที่มาของ beta (ดู stress)",
    "FY": "คำนำหน้าปีงบในชื่อแกน (ดู ตัวย่อ)", "Unrated": "ค่าภายในของ uncertainty ที่ไม่ถูกแสดง",
}


def entries():
    return [e for s in SECTIONS for e in s.entries]


def entry(eid):
    return next(e for e in entries() if e.id == eid)
