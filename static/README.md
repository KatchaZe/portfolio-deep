# static/ — the dashboard's CSS, JS and vendored assets

app.py mounts this directory at `/static`. `index.html` holds the markup only and loads:

- `chart.umd.js` (vendored Chart.js 4.5.0, MIT) first, falling back to the jsDelivr
  CDN only if the local file is missing.
- `dashboard.css`, every style the dashboard uses.
- `js/*.js` as plain `<script>` files, no build step. They share one global scope
  because the markup calls handlers through `onclick` attributes. Load order is the
  order of the tags in `index.html`, and `app.js` comes last because it starts the page.

| file | renders / owns |
|---|---|
| `js/format.js` | value formatters (`f`, `money`, `pct`, `rp`, ...), `esc`, `tag`, status colours |
| `js/assumptions.js` | the ERP / S&P P/E form and the stale-assumptions banner |
| `js/strip.js` | the command strip tiles, the momentum banner and the one-line synthesis |
| `js/screen.js` | the GARP screen table and quadrant map (Allocation tab) |
| `js/card_signals.js` | card chips and cells: momentum, rank, earnings dots, moat, guards |
| `js/card_value.js` | card value blocks: anchor FV, upside, value gauge, pre-profit (S20) panel |
| `js/card_drawer.js` | the card drawer (WACC / Ke / EVA, sub-score audit trail) and the 5-year trend strip |
| `js/portfolio.js` | the holding card, the My Portfolio tab and the holding / refresh / daily actions |
| `js/charts.js` | the one Chart.js registry and the doughnut builder |
| `js/allocation.js` | cost-basis pies, what-if and the cash stance (Allocation tab) |
| `js/risk.js` | the Risk Desk (Allocation tab) |
| `js/correlation.js` | the Correlation tab |
| `js/watchlist.js` | the Watchlist tab |
| `js/app.js` | tabs, build stamp (`DASH_BUILD`), persistence badge, keep-alive, boot |

Bump `DASH_BUILD` in `js/app.js` together with `config.BUILD`. Tests read the frontend
through `tests/frontend_source.py`, which follows the tags in `index.html`, and
`python -m tests.ui_snapshot` diffs the rendered dashboard against its pinned baseline.
