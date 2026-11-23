"""The shape of the in-app Ref page: sections in on-screen order, each holding entries.

An Entry explains one thing the dashboard shows. `covers` names the inventory labels
(tools/ui_inventory.py) it explains: an exact label, "re:<regex>" or "kind:<kind>".
Its own `term` always counts as covered. `kind` says whether the value moves a score or
a fair value ("คะแนน"), is display only ("แสดงผล"), is an input the user types
("ข้อมูลเข้า") or is a control ("ปุ่ม").
"""
import re
from dataclasses import dataclass, field

KINDS = ("คะแนน", "แสดงผล", "ข้อมูลเข้า", "ปุ่ม")
FIELDS = (("what", "ความหมาย"), ("calc", "วิธีคิด / สูตร"), ("rule", "เกณฑ์"),
          ("src", "แหล่งข้อมูล"), ("read", "อ่านอย่างไร"), ("where", "อยู่ตรงไหน"))


@dataclass(frozen=True)
class Entry:
    id: str
    term: str
    kind: str
    where: str
    what: str
    calc: str = ""
    rule: str = ""
    src: str = ""
    read: str = ""
    covers: tuple = ()

    def __post_init__(self):
        assert self.kind in KINDS, (self.id, self.kind)
        assert re.fullmatch(r"[a-z0-9-]+", self.id), self.id

    def text(self):
        return " ".join([self.term] + [getattr(self, f) for f, _ in FIELDS])

    def covers_label(self, label):
        if label.text == self.term:
            return True
        for c in self.covers:
            if c.startswith("re:"):
                if re.search(c[3:], label.text):
                    return True
            elif c.startswith("kind:"):
                if label.kind == c[5:]:
                    return True
            elif c == label.text:
                return True
        return False


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    intro: str
    entries: tuple = field(default_factory=tuple)
