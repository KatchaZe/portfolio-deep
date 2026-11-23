"""
ui_inventory: every user-visible label the dashboard can show, with the file and line
it comes from. Rerun it after a UI change:

    python -B -m tools.ui_inventory            # counts per screen section
    python -B -m tools.ui_inventory --list     # every label, file:line, kind
    python -B -m tools.ui_inventory --json     # machine-readable

What counts as a label: the text of a heading, table header, button, summary, option or
legend; the text of an element whose class marks it as a label (CHIP_CLASSES); a title=
or aria-label= tooltip; and the values of the lookup tables that turn engine codes into
words (TABLES). Text built at run time shows its ${...} parts as "…". The How to and
Ref pages themselves are left out: they are what this inventory is checked against.
"""
import html
import json
import os
import re
import sys
from collections import namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.frontend_source import frontend_files  # noqa: E402

Label = namedtuple("Label", "text kind file line section")

HOLE = "\x00"
LABEL_TAGS = ("th", "h2", "h3", "h4", "button", "summary", "caption", "option", "legend")
CHIP_CLASSES = {"l", "cap", "lbl", "tagsrc", "pill", "tag", "kchip", "chip", "legend", "badge",
                "secno", "pp-k0", "pp-k1", "scalelab", "ucase", "stat"}
ATTRS = ("title", "aria-label")
# lookup tables whose values are words on screen
TABLES = ("DTABS", "PP_LABEL", "PP_METRIC_TH", "PP_SIG", "PP_DIR", "PP_HINT", "PP_TREND",
          "PP_COLS", "RECO_LABEL", "ACT_COLOR", "RANK_COLOR", "EARN_GLYPH", "VERDICT_TOK",
          "SCEN_TOK", "FACTOR_LABEL", "TXN_LABEL", "PILLAR", "SORTS", "KM_TIP", "KM_MISSING",
          "ASSET_CLASS_NOTE", "PP_TH")
# helpers whose first argument is the label they print
LABEL_FUNCS = ("stripTile", "row", "chip", "storyRow", "secHTML", "cell", "earnLine", "earnRow",
               "pillarBar", "pair", "kpi", "tile", "T")
FUNC_RE = re.compile(r"\b(%s)\(\s*(['\"`])(.{2,120}?)\2\s*[,)]" % "|".join(LABEL_FUNCS))
# a whole literal that is a short capitalised label ("RISK-ON", "Portfolio Snapshot")
WORD_RE = re.compile(r"(?=.*[A-Z])[A-Z][A-Za-z0-9 ·/()&+?✓✗⚠-]{1,38}")
NOT_WORDS = {"Content-Type", "POST", "GET", "End", "Home", "Escape", "Enter"}
CODE_TABLES = ("ASSET_CLASS",)      # ticker -> class maps: tickers, not words
KEY_TABLES = ("ACT_COLOR", "RANK_COLOR", "VERDICT_TOK", "SCEN_TOK", "EARN_GLYPH")
# the screen section each file's labels belong to
SECTION_OF_FILE = {
    "index.html": "header", "app.js": "header", "portfolio.js": "header", "assumptions.js": "tools",
    "strip.js": "strip", "screen.js": "allocation", "card.js": "card", "format.js": "card",
    "card_signals.js": "signals", "card_value.js": "valuation", "card_drawer.js": "deep",
    "card_peer.js": "peer", "charts.js": "allocation", "allocation.js": "allocation",
    "risk.js": "risk", "correlation.js": "correlation", "fx_usdthb.js": "correlation",
    "watchlist.js": "watchlist", "ledger.js": "ledger",
}
SKIP_FILES = {"howto_figs.js", "ref_page.js"}
REF_BEGIN, REF_END = "<!-- HOWTO-REF:BEGIN -->", "<!-- HOWTO-REF:END -->"
SECTION_ORDER = ("header", "tools", "strip", "card", "valuation", "deep", "signals", "peer",
                 "watchlist", "allocation", "risk", "correlation", "ledger", "other")
_OPS = "(,=:[!&|?{};+-*%<>~^"


def js_literals(src):
    """(text, line) for every string and template literal in a JS source; a template's
    ${...} parts become HOLE and the literals inside them are returned on their own."""
    out, n = [], len(src)

    def template(i, line):
        buf, start = [], line
        while i < n:
            c = src[i]
            if c == "\\":
                buf.append(src[i:i + 2]); i += 2; continue
            if c == "`":
                out.append(("".join(buf), start)); return i + 1, line
            if c == "$" and src[i + 1:i + 2] == "{":
                buf.append(HOLE); i, line = expr(i + 2, line); continue
            if c == "\n":
                line += 1
            buf.append(c); i += 1
        return i, line

    def quoted(i, line, q):
        buf, start = [], line
        while i < n and src[i] != q:
            if src[i] == "\\":
                buf.append(src[i + 1:i + 2]); i += 2; continue
            if src[i] == "\n":
                break
            buf.append(src[i]); i += 1
        out.append(("".join(buf), start))
        return i + 1, line

    def regex(i):
        cls = False
        while i < n:
            c = src[i]
            if c == "\\":
                i += 2; continue
            if c == "[":
                cls = True
            elif c == "]":
                cls = False
            elif c == "/" and not cls:
                i += 1
                while i < n and src[i].isalpha():
                    i += 1
                return i
            elif c == "\n":
                return i
            i += 1
        return i

    def scan(i, line, stop_at_brace):
        depth, p = 0, ""
        while i < n:
            c = src[i]
            if c == "/" and src[i + 1:i + 2] == "/":
                j = src.find("\n", i); i = n if j < 0 else j; continue
            if c == "/" and src[i + 1:i + 2] == "*":
                j = src.find("*/", i + 2); line += src[i:j].count("\n"); i = n if j < 0 else j + 2; continue
            if c in "'\"":
                i, line = quoted(i + 1, line, c); p = "a"; continue
            if c == "`":
                i, line = template(i + 1, line); p = "a"; continue
            if stop_at_brace and c == "{":
                depth += 1
            elif stop_at_brace and c == "}":
                if depth == 0:
                    return i + 1, line
                depth -= 1
            elif c == "/" and (p == "" or p in _OPS or src[max(0, i - 7):i].rstrip().endswith("return")):
                i = regex(i + 1); p = "a"; continue
            elif c == "\n":
                line += 1
            if not c.isspace():
                p = c
            i += 1
        return i, line

    def expr(i, line):
        return scan(i, line, True)

    scan(0, 1, False)
    return out


TAG_RE = re.compile(r"<(%s)\b[^>]*>(.*?)</\1\s*>" % "|".join(LABEL_TAGS), re.S | re.I)
CLASS_EL_RE = re.compile(r"<(\w+)\b[^>]*\bclass=['\"]([^'\"]*)['\"][^>]*>(.*?)</\1\s*>", re.S)
ATTR_RE = re.compile(r"\b(%s)=(['\"])(.*?)\2" % "|".join(ATTRS), re.S)
INNER_TAG = re.compile(r"<[^>]*>")


def clean(text):
    t = INNER_TAG.sub(" ", text)
    t = html.unescape(t).replace(HOLE, "…")
    t = re.sub(r"\s+", " ", t).strip(" ·:—-")
    t = re.sub(r"(…\s*)+", "… ", t).strip()
    if not re.search(r"[A-Za-z฀-๿]{2,}", t):
        return ""
    return t[:160]


def labels_in(text):
    """(kind, label) pairs found in one chunk of markup."""
    found = []
    for m in TAG_RE.finditer(text):
        found.append(("tag:" + m.group(1).lower(), m.group(2)))
    for m in CLASS_EL_RE.finditer(text):
        if CHIP_CLASSES & set(m.group(2).split()):
            found.append(("class:" + m.group(2).split()[0], m.group(3)))
    for m in ATTR_RE.finditer(text):
        found.append(("attr:" + m.group(1), m.group(3)))
    return [(k, clean(v)) for k, v in found if clean(v)]


def table_ranges(src, names=TABLES):
    """line range of each named const table in a JS source."""
    ranges = {}
    for m in re.finditer(r"const (%s)\s*=\s*([\[{])" % "|".join(names), src):
        open_c = m.group(2)
        close_c = "]" if open_c == "[" else "}"
        depth, j = 0, m.end() - 1
        while j < len(src):
            if src[j] == open_c:
                depth += 1
            elif src[j] == close_c:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        ranges[m.group(1)] = (src.count("\n", 0, m.start()) + 1, src.count("\n", 0, j) + 1)
    return ranges


def strip_docs(page):
    """index.html without the How to / Ref block, line numbers kept."""
    a, b = page.find(REF_BEGIN), page.find(REF_END)
    if a < 0 or b < 0:
        a, b = page.find('<div id="p4"'), page.find('<div class="foot"')
    if a < 0 or b < 0:
        return page
    return page[:a] + "\n" * page.count("\n", a, b) + page[b:]


def inventory():
    items, seen = [], set()

    def add(text, kind, path, line):
        base = os.path.basename(path)
        if (text, base) in seen:
            return
        seen.add((text, base))
        items.append(Label(text, kind, base, line, SECTION_OF_FILE.get(base, "other")))

    for path in frontend_files():
        base = os.path.basename(path)
        if base in SKIP_FILES or not path.endswith((".js", ".html")):
            continue
        src = open(path, encoding="utf-8").read()
        if base == "index.html":
            src = strip_docs(src)
            for m in TAG_RE.finditer(src):
                t = clean(m.group(2))
                if t:
                    add(t, "tag:" + m.group(1).lower(), path, src.count("\n", 0, m.start()) + 1)
            for ln, row in enumerate(src.split("\n"), 1):
                for kind, t in labels_in(row):
                    add(t, kind, path, ln)
            continue
        ranges = table_ranges(src)
        code = table_ranges(src, CODE_TABLES)
        for text, ln in js_literals(src):
            tbl = next((name for name, (a, b) in ranges.items() if a <= ln <= b), None)
            if tbl:
                t = clean(text)
                if t and not text.startswith("--") and not re.fullmatch(r"[a-z_0-9-]+", text):
                    add(t, "table:" + tbl, path, ln)
                continue
            in_code = any(a <= ln <= b for name, (a, b) in code.items())
            if (HOLE not in text and not in_code and text == text.strip() and WORD_RE.fullmatch(text) and text not in NOT_WORDS
                    and not re.match(r"M-?\d", text) and not re.fullmatch(r"[A-Z][a-z]+[A-Z]\w*", text)):
                add(text, "word", path, ln)
            for kind, t in labels_in(text):
                add(t, kind, path, ln)
        for m in FUNC_RE.finditer(src):
            t = clean(re.sub(r"\$\{[^}]*\}", HOLE, m.group(3)))
            if t:
                add(t, "arg:" + m.group(1), path, src.count("\n", 0, m.start()) + 1)
        lines = src.split("\n")
        for name in KEY_TABLES:
            if name in ranges:
                a, b = ranges[name]
                body = "\n".join(lines[a - 1:b])
                for k in re.findall(r"[{,]\s*['\"]?([A-Za-z][A-Za-z ?/]*)['\"]?\s*:", body):
                    add(k.strip(), "table-key:" + name, path, a)
    return items


def main(argv):
    items = inventory()
    if "--json" in argv:
        print(json.dumps([i._asdict() for i in items], ensure_ascii=False, indent=1))
        return
    if "--list" in argv:
        for i in sorted(items, key=lambda x: (SECTION_ORDER.index(x.section), x.file, x.line)):
            print("%s %s:%d [%s] %s" % (i.section, i.file, i.line, i.kind, i.text))
        return
    counts = {}
    for i in items:
        counts[i.section] = counts.get(i.section, 0) + 1
    for s in SECTION_ORDER:
        if s in counts:
            print("%-12s %4d" % (s, counts[s]))
    print("%-12s %4d" % ("total", len(items)))


if __name__ == "__main__":
    main(sys.argv[1:])
