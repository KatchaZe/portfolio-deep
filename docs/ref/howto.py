"""The How to page: short tasks for the current workflow. Each step is (text, [ref ids]):
the ids become links to the Ref entries that explain what the step reads."""

TASKS = (
  ("daily", "ใช้งานประจำวัน", "5 นาทีต่อวัน ไม่กิน quota", (
    ("ดูป้ายสถานะข้างชื่อแอป: Drive ✓ แปลว่า backup ปกติ ถ้าเห็น ✗ หรือ ⚠ conflict ให้ชี้ดูสาเหตุก่อนแก้ข้อมูล", ["drive"]),
    ("กด Run Daily เพื่อดึงราคาปิด T-1 และ momentum ใหม่ banner เหลืองจะหายเมื่อข้อมูลไม่เก่ากว่า 4 วัน", ["run-daily", "banner-mom"]),
    ("อ่าน Command strip ซ้ายไปขวา: Regime → Market value → ผลจริง vs SPY → Philosophy แล้วอ่านบรรทัด วันนี้ ที่สรุปทั้ง 4 ไทล์", ["tile-regime", "tile-market", "tile-bench", "tile-philo", "synth"]),
    ("ดูแถบสรุปพอร์ต: Portfolio MoS และน้ำหนักตาม Rating บอกภาพรวมก่อนดูรายตัว", ["summary-band"]),
    ("เรียงการ์ดตาม MoS หรือกรองเฉพาะ BUY / SELL เพื่อดูตัวที่ต้องตัดสินใจก่อน", ["sort", "filter-rating", "filter-erode"]),
  )),
  ("refresh", "รัน Fundamental Refresh", "เมื่อมีงบใหม่ หรือข้อมูลพื้นฐานเปลี่ยน", (
    ("กดเมื่อ: งบไตรมาสออกแล้ว · เพิ่มหุ้นใหม่ · แก้ Assumptions หรือ Revenue mix · หรืออย่างน้อยเดือนละครั้ง ไม่ต้องกดทุกวัน", ["run-refresh", "assume", "revmix"]),
    ("ก่อนกด อ่านป้าย FMP quota: เหลือ X ครั้ง ≈ Y หุ้น คือจำนวนหุ้นที่ยัง Refresh ได้ในกรณีแย่สุด (6 ครั้งต่อหุ้น จาก 250 ครั้งต่อวัน รีเซ็ต 07:00 น. เวลาไทย)", ["quota"]),
    ("ถ้าป้ายขึ้น ⛔ FMP ปฏิเสธ แปลว่า FMP หยุดตอบวันนี้ (อาจก่อนถึง 250) Refresh ยังทำงานด้วย Yahoo และข้อมูลรอบก่อน หุ้นที่ถูกข้ามติดป้าย ข้อมูลรอบก่อน และได้คิวแรกในรอบหน้า", ["quota", "chips-head"]),
    ("หลัง Refresh เสร็จ ดูจุดสี confidence และจำนวน ⚑ ของหุ้นที่ตัวเลขเปลี่ยนมาก", ["confidence", "flags"]),
  )),
  ("card", "อ่านการ์ดและลำดับการตัดสินใจ", "มูลค่าก่อนเสมอ แล้วคุณภาพ แล้วจังหวะ", (
    ("1. DEEP rating บนหัวการ์ดคือคำตัดสินหลัก ไม่จัดอันดับ แปลว่ามูลค่าเชื่อไม่ได้พอ อย่าซื้อเพิ่มตามมูลค่า", ["rating"]),
    ("2. MoS และบรรทัดความไม่แน่นอน: ถูกจริงเมื่อ MoS ถึงเกณฑ์ของแถว uncertainty นั้น ไม่ใช่แค่เป็นบวก ดู net ถ้าจะคิดผลตอบแทน", ["mos", "uncertainty", "upside"]),
    ("3. คุณภาพ: ป้าย Moat (ROIC − WACC) และ EQ ถ้า spread ติดลบ ราคาถูกอาจเป็น value trap", ["spread", "eq-chip", "pillars"]),
    ("4. จังหวะ: Action และ Momentum ใช้เลือกเวลาเข้า ไม่ใช่เหตุผลซื้อ ตลาด RISK-OFF เปลี่ยนฝั่งซื้อเป็น WAIT", ["action", "momentum", "guards"]),
    ("5. ขนาด: ก่อนซื้อจริง ใช้ What-if และดู Position sizing ใน Risk Desk", ["whatif", "sizing"]),
    ("ตัวเลขใดดูแปลก เปิดรายละเอียด แล้วดูแท็บ มูลค่า (วิธีไหนเป็น ★) และแท็บ ข้อมูล (flag)", ["drawer", "anchor", "flags"]),
  )),
  ("peer", "ใช้แท็บ คู่แข่ง & Moat", "แสดงผลอย่างเดียว ไม่เปลี่ยนคะแนนหรือ Fair Value", (
    ("อ่านหัวแท็บก่อน: แนวโน้ม moat (↗ → ↘) และจำนวนสัญญาณที่วัดได้ ถ้าวัดได้ไม่ถึง 2 ตัวยังสรุปไม่ได้", ["moat-trend"]),
    ("ดูสัญญาณ 4 ตัว แถบไหนเลยเส้นประคือนับว่าเปลี่ยนจริง เกณฑ์ยังเป็นเกณฑ์ชั่วคราว", ["s1", "s2", "s3", "s4"]),
    ("ในกล่องส่วนแบ่ง สลับ เฉพาะธุรกิจเดียวกัน (N ราย) กับ ทั้งกลุ่ม Damodaran ปุ่มนี้เปลี่ยนทั้งกราฟและตารางคู่แข่ง", ["share-box", "rules-abc"]),
    ("Dumbbell บอกว่าใครครองตลาดและหุ้นนี้ขยับจากไหนไปไหนใน 3 ปี Stacked area บอกสัดส่วนรายปีรวม 100% ลายบนแถบแยกคู่แข่งอันดับ 1–5", ["dumbbell", "stacked", "top5-hhi"]),
    ("แหล่ง moat เขียนว่า สอดคล้องกับ ไม่ใช่ข้อสรุป และขึ้นเฉพาะหุ้นที่ ROIC > WACC", ["moat-sources"]),
    ("ADR เช่น TSM, NVO, ASML ใช้ SIC จากรายชื่อทั่วโลกของ Damodaran ถ้าชื่อไม่ตรงแถวเดียว แท็บจะไม่เทียบ gross margin", ["adr-sic"]),
  )),
  ("scout", "หาหุ้นใหม่ด้วย Sector scout", "แสดงผลอย่างเดียว ไม่ใช้ FMP จนกว่าจะกดรัน DEEP", (
    ("เปิดแท็บ Sector scout เลือกอุตสาหกรรมที่จะรัน (ค่าเริ่มต้นคืออุตสาหกรรมของหุ้นในพอร์ต) แล้วกด รันที่เลือก รอจนสถานะขึ้น รันเสร็จล่าสุด", ["scout-job"]),
    ("ดูมุมมอง เฉพาะธุรกิจเดียวกัน ก่อน แล้วสลับ ทั้งกลุ่ม Damodaran ถ้าอยากเห็นทั้งอุตสาหกรรม", ["scout-view"]),
    ("อ่าน น่าส่งรัน DEEP และ funnel แล้วเปิดรายละเอียดหุ้นที่สนใจ ดูธงและแหล่งของทุกตัวเลข", ["scout-funnel", "scout-flags", "scout-tiers"]),
    ("หุ้นที่ถูกตัดออกอยู่ในกล่อง ตัดออก พร้อมเหตุผล ไม่ได้หายไปเฉย ๆ", ["scout-excl"]),
    ("ถ้าน่าสนใจ กด เพิ่มเข้า watchlist แล้ว รัน DEEP (ใช้ FMP 6 ครั้ง) คำตัดสินจริงคือ DEEP ไม่ใช่คะแนน scout", ["scout-watch"]),
  )),
  ("flags", "เมื่อเห็น flag หรือคำเตือน", "ระบบข้ามและบอกเหตุผล ไม่เดาตัวเลข", (
    ("⚑n บนการ์ด: เปิดแท็บ ข้อมูล อ่านรายการ ถ้าลงท้ายว่า retried on a later refresh รอบหน้าจะลองใหม่เอง", ["flags"]),
    ("จุดแดง (confidence <50): อย่าใช้ MoS ตัดสินจนกว่าจะเข้าใจ flag", ["confidence"]),
    ("หนี้สินรวม ณ วันที่ใดไม่ทราบ: ปีนั้นถูกตัดออกจาก invested capital ROIC และทุนใหม่อาจวัดไม่ได้ เป็นการกันตัวเลขผิด ไม่ใช่บั๊ก", ["debt", "inc-roic"]),
    ("⟳ refresh หรือ × n/a ในแถวผลงบ: กด Run Fundamental Refresh หรือรอแหล่งข้อมูล", ["earn-row"]),
    ("หมายเหตุข้อมูลในแท็บ คู่แข่ง & Moat บอกบริษัทที่ไม่นับและเหตุผล", ["peer-notes"]),
    ("ไม่จัดอันดับ: เหตุผลอยู่ใน flag (uncertainty Extreme · มูลค่า liquidation · terminal เกิน 100% · TTM ไม่ครบ)", ["rating"]),
  )),
  ("deploy", "รับอัปเดตและ deploy", "bundle → merge → Manual Deploy → Refresh", (
    ("ไฟล์ .bundle จะอยู่ในโฟลเดอร์ Stock Screening เช่น ref-howto-2026-11-24.bundle", []),
    ("ใน repo บนเครื่อง: git fetch \"<path>/ref-howto-2026-11-24.bundle\" ref-howto แล้ว git merge FETCH_HEAD บน main และรัน python run_tests.py", []),
    ("git push ขึ้น GitHub แล้วเปิด Render → service ของแอป → Manual Deploy → Deploy latest commit", []),
    ("เมื่อ deploy เสร็จ เปิดแอปแล้วกด Ctrl+F5 ดู Build stamp ท้ายหน้าว่าตรงกับ server", ["build-stamp"]),
    ("ถ้าอัปเดตแตะ engine ให้กด Run Fundamental Refresh ถ้าแตะแค่หน้าจอ กด Run Daily ก็พอ", ["run-refresh", "run-daily"]),
  )),
)
