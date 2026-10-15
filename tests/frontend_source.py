"""
frontend_source — the dashboard source exactly as the browser receives it.

index.html loads its CSS and JS from /static. Tests that check the frontend reads a
payload key, or that pull a render function out to run it in node, read index.html
plus every /static file it references, in load order, so a file the page stops
loading also stops counting. Vendored third-party files are left out.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDORED = {"chart.umd.js"}
_REF = re.compile(r'<(?:script|link)\b[^>]*\b(?:src|href)="/static/([^"]+)"')


def frontend_files():
    """index.html, then each /static file it loads, as absolute paths."""
    index = os.path.join(ROOT, "index.html")
    with open(index, encoding="utf-8") as fh:
        refs = _REF.findall(fh.read())
    return [index] + [os.path.join(ROOT, "static", r) for r in refs
                      if os.path.basename(r) not in VENDORED]


def frontend_source():
    parts = []
    for path in frontend_files():
        with open(path, encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)
