"""
build_ref: writes the How to and Ref panels into index.html from docs/ref/.

    python -B -m tools.build_ref           # rewrite the block between the markers
    python -B -m tools.build_ref --check   # exit 1 when index.html is out of date

The content lives in docs/ref/content*.py (Ref) and docs/ref/howto.py (How to); the page
markup is generated here so both tabs ship as static HTML that reads without JS.
static/js/ref_page.js adds the search, expand/collapse and links on top.
"""
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from docs.ref.content import SECTIONS, entries     # noqa: E402
from docs.ref.howto import TASKS                    # noqa: E402
from docs.ref.model import FIELDS                   # noqa: E402
from tools.ui_inventory import REF_BEGIN, REF_END  # noqa: E402

INDEX = os.path.join(ROOT, "index.html")
KIND_CLASS = {"คะแนน": "k-score", "แสดงผล": "k-show", "ข้อมูลเข้า": "k-input", "ปุ่ม": "k-ctl"}
KIND_NOTE = {"คะแนน": "มีผลต่อคะแนนหรือ Fair Value", "แสดงผล": "แสดงผลอย่างเดียว ไม่มีผลต่อคะแนน",
             "ข้อมูลเข้า": "ค่าที่ผู้ใช้กรอก", "ปุ่ม": "ปุ่มหรือการควบคุม"}
_LEGACY_START = "    <!-- ===================== HOW TO"
_LEGACY_END = "  </div>\n  <div class=\"foot\">"


def esc(s):
    return html.escape(s, quote=True)


def search_text(e, section):
    return re.sub(r"\s+", " ", (e.text() + " " + section.title + " " + e.id).lower()).strip()


def render_value(text):
    """a field of three or more " · " items reads as a list, shorter ones as a sentence"""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if depth == 0 and text.startswith(" · ", i):
            parts.append(text[start:i])
            start = i + 3
    parts.append(text[start:])
    if len(parts) < 3:
        return esc(text)
    return '<ul class="refli">%s</ul>' % "".join("<li>%s</li>" % esc(p) for p in parts)


def render_entry(e, section):
    rows = "".join('<div><dt>%s</dt><dd>%s</dd></div>' % (label, render_value(getattr(e, f)))
                   for f, label in FIELDS if getattr(e, f))
    return ('<article class="refent" id="ref-%s" tabindex="-1" data-q="%s">'
            '<h4 class="refterm">%s <span class="refkind %s" title="%s">%s</span></h4>'
            '<dl class="refdl">%s</dl></article>'
            % (e.id, esc(search_text(e, section)), esc(e.term), KIND_CLASS[e.kind], KIND_NOTE[e.kind],
               e.kind, rows))


def render_ref():
    n = len(entries())
    toc = "".join('<li><a href="#refsec-%s">%s</a> <span class="refn">%d</span></li>'
                  % (s.id, esc(s.title), len(s.entries)) for s in SECTIONS)
    secs = "".join(
        '<details class="refsec" id="refsec-%s"><summary><h3 class="ht2">%s</h3>'
        '<span class="refn">%d รายการ</span></summary><div class="refbody"><p class="muted">%s</p>%s</div></details>'
        % (s.id, esc(s.title), len(s.entries), esc(s.intro), "".join(render_entry(e, s) for e in s.entries))
        for s in SECTIONS)
    return f"""    <div id="p5" role="tabpanel" aria-labelledby="tab5" class="prose pnl refpage" style="display:none">
      <h2 class="ht">Ref · ทุกค่าบน dashboard: ความหมาย วิธีคิด เกณฑ์ และที่มา</h2>
      <p class="muted">เรียงตามหน้าจอจากบนลงล่าง · ป้ายท้ายชื่อบอกว่าค่านั้น <span class="refkind k-score">คะแนน</span> มีผลต่อคะแนนหรือ Fair Value หรือ <span class="refkind k-show">แสดงผล</span> อย่างเดียว · หน่วยส่วนต่างเป็น % เสมอ</p>
      <div class="reftools" role="search">
        <label for="refq">ค้นหา</label>
        <input id="refq" class="inp" type="search" autocomplete="off" placeholder="เช่น ROIC, quota, dumbbell, MoS" aria-describedby="refcount">
        <span id="refcount" class="note" role="status" aria-live="polite">ทั้งหมด {n} รายการ</span>
        <span class="refbtns"><button type="button" class="btn sm" id="refOpenAll">เปิดทั้งหมด</button><button type="button" class="btn sm" id="refCloseAll">ปิดทั้งหมด</button></span>
      </div>
      <nav class="reftoc" aria-label="สารบัญ Ref"><ol>{toc}</ol></nav>
      <div id="refempty" class="empty" hidden><b>ไม่พบรายการที่ตรงกับคำค้น</b><span>ลองคำที่สั้นลง หรือคำภาษาอังกฤษบนหน้าจอ เช่น ROIC, WACC, quota</span><button type="button" class="btn sm" id="refClear">ล้างคำค้น</button></div>
      {secs}
    </div>
"""


def render_howto():
    def step(text, ids):
        links = "".join(' <a class="reflink" href="#ref-%s">%s</a>' % (i, esc(next(e.term for e in entries() if e.id == i)))
                        for i in ids)
        return "<li>%s%s</li>" % (esc(text), (' <span class="refsee">ดูใน Ref:%s</span>' % links) if links else "")
    toc = "".join('<li><a href="#howto-%s">%s</a></li>' % (k, esc(t)) for k, t, _, _ in TASKS)
    body = "".join('<section class="htask" id="howto-%s" aria-labelledby="howto-%s-h"><h3 class="ht2" id="howto-%s-h">%s</h3>'
                   '<p class="muted">%s</p><ol class="hsteps">%s</ol></section>'
                   % (k, k, k, esc(t), esc(sub), "".join(step(a, b) for a, b in steps))
                   for k, t, sub, steps in TASKS)
    return f"""    <div id="p4" role="tabpanel" aria-labelledby="tab4" class="prose pnl howto" style="display:none">
      <h2 class="ht">How to · ใช้แดชบอร์ดนี้ทีละงาน</h2>
      <p class="muted">หลักคิด: Value ก่อนเสมอ → คุณภาพ (moat และงบที่ไว้ใจได้) → จังหวะ (momentum ใช้จับเวลา ไม่ใช่เหตุผลซื้อ) · ลิงก์ ดูใน Ref พาไปที่คำอธิบายเต็มของแต่ละค่า</p>
      <nav class="reftoc" aria-label="งานใน How to"><ol>{toc}</ol></nav>
      {body}
    </div>
"""


def render_block():
    return REF_BEGIN + "\n" + render_howto() + render_ref() + "    " + REF_END + "\n"


def splice(page):
    a, b = page.find(REF_BEGIN), page.find(REF_END)
    if a >= 0 and b >= 0:
        a = page.rfind("\n", 0, a) + 1
        b = page.find("\n", b) + 1
    else:
        a, b = page.find(_LEGACY_START), page.find(_LEGACY_END)
        assert a >= 0 and b >= 0, "How to / Ref block not found in index.html"
    return page[:a] + "    " + render_block() + page[b:]


def main(argv):
    page = open(INDEX, encoding="utf-8").read()
    new = splice(page)
    if "--check" in argv:
        if new != page:
            print("index.html How to / Ref block is out of date: python -B -m tools.build_ref")
            return 1
        print("index.html How to / Ref block is current")
        return 0
    with open(INDEX, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote How to (%d tasks) and Ref (%d sections, %d entries)" % (len(TASKS), len(SECTIONS), len(entries())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
