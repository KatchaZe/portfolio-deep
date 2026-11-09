"""
Ken French data library: daily Fama-French 5 factors (2x3) and momentum.
https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
Each file is a zipped CSV of percent returns, one row per day (YYYYMMDD, values...).
"""
import io
import zipfile

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FF5 = "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM = "F-F_Momentum_Factor_daily_CSV.zip"


def parse_csv(text):
    """{YYYY-MM-DD: {column: decimal}} from the daily table (the first header row
    names the columns; footer and annual tables are skipped)."""
    cols, out = None, {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if cols is None:
            if len(cells) > 1 and cells[0] == "" and any(cells[1:]):
                cols = cells[1:]
            continue
        if len(cells[0]) == 8 and cells[0].isdigit() and len(cells) - 1 == len(cols):
            try:
                vals = [float(c) / 100 for c in cells[1:]]
            except ValueError:
                continue
            d = f"{cells[0][:4]}-{cells[0][4:6]}-{cells[0][6:]}"
            out[d] = dict(zip(cols, vals))
    return out


def _unzip(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return z.read(name).decode("latin-1")


def fetch_factors(requests_mod=None, timeout=30, since="2018-01-01"):
    """{date: {MKT, SMB, HML, RMW, CMA, RF, MOM}} from `since` on. Raises on failure."""
    import requests as _r
    requests_mod = requests_mod or _r
    ff = parse_csv(_unzip(requests_mod.get(BASE + FF5, timeout=timeout).content))
    mom = parse_csv(_unzip(requests_mod.get(BASE + MOM, timeout=timeout).content))
    out = {}
    for d, v in ff.items():
        m = mom.get(d)
        if d < since or not m:
            continue
        out[d] = {"MKT": v["Mkt-RF"], "SMB": v["SMB"], "HML": v["HML"], "RMW": v["RMW"],
                  "CMA": v["CMA"], "RF": v["RF"], "MOM": next(iter(m.values()))}
    if not out:
        raise RuntimeError("Ken French factor files parsed to nothing")
    return out
