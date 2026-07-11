# static/ — vendored frontend assets (P2-9)

`index.html` loads `/static/chart.umd.js` FIRST and falls back to the jsDelivr CDN
only if the local file is missing — so the dashboard keeps working either way.

To vendor Chart.js (recommended — no CDN dependency at runtime):

1. Download once: https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js
2. Save it here as `static/chart.umd.js`
3. Commit it (it's ~205 KB, MIT license)

app.py mounts this directory at `/static` automatically when it exists.
