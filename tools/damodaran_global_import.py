"""Write data/cache/damodaran_global_sic.json from a local copy of Damodaran's indname.xls
(SPEC R29), for a machine where the app cannot download the workbook. Reads the file
only; the US table (damodaran_indname.json) is not touched.

    python -m tools.damodaran_global_import /path/to/indname.xls [cache_dir]
"""
import sys

import config
from sources import damodaran_companies as dc


def main(path, cache_dir=None):
    with open(path, "rb") as fh:
        rows = dc.parse_indname_global(fh.read())
    dc.write_global(cache_dir or config.CACHE_DIR, rows)
    print(f"{len(rows)} non-US rows, {sum(1 for r in rows if r[3])} with a SIC code")


if __name__ == "__main__":
    main(*sys.argv[1:3])
