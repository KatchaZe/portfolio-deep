"""Self-contained review pages for the Sector scout tab, from the job's JSON files.

  python3 -B tools/scout_review.py OUT_DIR STORE.json SCOUT_JSON...

One page per industry: the app's own dashboard.css (fonts inlined), static/js/scout.js and a
fetch shim that answers /api/scout, /api/scout/view and /api/quota from the payload
domain/scout.py builds. Nothing is fetched at view time, so the page opens from disk."""
import base64
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from domain import scout  # noqa: E402

SHELL = """<!DOCTYPE html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sector scout review: __TITLE__</title><style>__CSS__</style></head><body>
<main class="wrap" style="max-width:1280px;margin:0 auto;padding:12px 16px">
<div id="p8" role="tabpanel" class="scout">
  <div class="phead"><h2 style="font-size:var(--t-lg)">Sector scout <span class="proxy">ค่าประมาณ ไม่ใช่คะแนน DEEP</span>
  <span class="note">review page (offline) · build __BUILD__</span></h2></div>
  <p id="scoutLive" class="sr" aria-live="polite"></p><div id="scoutCtl"></div>
  <div id="scoutBody"><div class="empty">กำลังโหลด&hellip;</div></div><p class="note" id="scoutFoot"></p>
</div></main>
<script>
const __D = __DATA__;
function showTab(){}
window.fetch = async (u) => {
  const ok = (o) => ({ ok: true, status: 200, json: async () => o });
  if (u.startsWith('/api/scout/view')) { const n = decodeURIComponent(u.split('industry=')[1]); return ok(__D.views[n]); }
  if (u.startsWith('/api/scout')) return ok(__D.list);
  if (u.startsWith('/api/quota')) return ok({ per_ticker: 6, headroom_calls: 0 });
  return { ok: false, status: 404, json: async () => ({ error: 'review page: ' + u }) };
};
</script>
<script>__JS__</script>
<script>loadScout();</script>
</body></html>"""


def css():
    text = open(os.path.join(ROOT, "static", "dashboard.css"), encoding="utf-8").read()

    def inline(m):
        path = os.path.join(ROOT, "static", "fonts", m.group(1))
        return "url(data:font/woff2;base64,%s)" % base64.b64encode(open(path, "rb").read()).decode()
    return re.sub(r"url\(fonts/([^)]+)\)", inline, text)


def main(out_dir, store_path, files):
    import config
    store = json.load(open(store_path, encoding="utf-8"))
    holdings = list(store.get("holdings") or {})
    book = {t: ((m.get("v2") or {}).get("beta_calc"), (m.get("v2") or {}).get("price_date"))
            for t, m in (store.get("momentum") or {}).items() if (m.get("v2") or {}).get("beta_calc") is not None}
    from pipeline import scout_job
    held = scout_job.holding_industries(store)
    datas = [json.load(open(f, encoding="utf-8")) for f in files]
    views, runs = {}, {}
    for D in datas:
        views[D["industry"]] = scout.slim(scout.build(D, holdings, dt.date.fromisoformat(D["as_of"]), book))
        runs[D["industry"]] = {"finished": D["as_of"] + "T00:00:00+00:00", "as_of": D["as_of"],
                               "members": len(D["members"]), "secs": 0, "requests": {"sec": 0, "companyfacts": 0,
                                                                                    "price": 0, "history": 0, "fmp": 0}}
    names = sorted(set(views) | set(held))
    lst = {"status": {"state": "idle", "queue": [], "runs": runs}, "default": sorted(held),
           "industries": [{"name": n, "members": None, "held": held.get(n, []), "run": runs.get(n)} for n in names]}
    js = open(os.path.join(ROOT, "static", "js", "scout.js"), encoding="utf-8").read()
    style = css()
    os.makedirs(out_dir, exist_ok=True)
    for D in datas:
        n = D["industry"]
        # each page opens on its own industry
        page_js = js.replace("if (!SC.sel && have.length) SC.sel =", "if (!SC.sel) SC.sel = %s; if (!SC.sel && have.length) SC.sel =" % json.dumps(n))
        blob = json.dumps({"list": lst, "views": views}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        html = (SHELL.replace("__TITLE__", n).replace("__CSS__", style).replace("__BUILD__", config.BUILD)
                .replace("__DATA__", blob).replace("__JS__", page_js))
        path = os.path.join(out_dir, "SectorScout_Review_%s.html" % scout.slug(n))
        open(path, "w", encoding="utf-8").write(html)
        print(path, len(html.encode()) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
