# static/ — the dashboard's CSS, JS and vendored assets

app.py mounts this directory at `/static`. `index.html` holds the markup only and loads:

- `fonts/` IBM Plex Sans Thai + IBM Plex Mono (woff2, SIL OFL, see `fonts/OFL.txt`),
  referenced from `dashboard.css`. Nothing is fetched from a CDN.
- `dashboard.css`, every style the dashboard uses: one token theme (light + dark, `:root`
  variables) and the shared components. JS never writes a hex colour; it names a token
  (`var(--bull)`) or a class (`tone t-bear`).
- `js/*.js` as plain `<script>` files, no build step. They share one global scope
  because the markup calls handlers through `onclick` attributes. Load order is the
  order of the tags in `index.html`, and `app.js` comes last because it starts the page.

| file | renders / owns |
|---|---|
| `js/format.js` | value formatters (`money`, `usd`, `nf`, `sgn`, `pp2pct`, ...), `esc`, `tag`, `tone`, status-colour tokens |
| `js/assumptions.js` | the ERP / S&P P/E form and the stale-assumptions banner |
| `js/strip.js` | the command strip tiles, the momentum banner and the one-line synthesis |
| `js/screen.js` | the GARP screen table and quadrant map (Allocation tab) |
| `js/card_signals.js` | card chips and cells: momentum, rank, earnings dots, moat, guards |
| `js/card_value.js` | card value blocks: MoS + Bear–Bull scale, pre-profit (S20) panel, methods table, scenarios, story Monte Carlo histogram, relative-valuation scatter |
| `js/card_drawer.js` | detail tabs "DEEP & ทุน" (sub-score audit trail, ROIC vs WACC moat charts) and "แนวโน้ม 5 ปี" (one shared year axis) |
| `js/card.js` | the stock card (summary + five detail tabs, built on first open), the portfolio summary band and the filter / sort toolbar |
| `js/portfolio.js` | the My Portfolio tab entry point and the holding / cash / refresh / daily actions |
| `js/charts.js` | SVG / HTML chart helpers coloured by tokens: doughnut, bars, paired bars, KPI tiles, numbered panels |
| `js/allocation.js` | money per holding vs the book, cost-basis doughnuts, what-if and the cash stance (Allocation tab) |
| `js/risk.js` | the Risk Desk (Allocation tab) |
| `js/correlation.js` | the Correlation tab |
| `js/watchlist.js` | the Watchlist tab |
| `js/howto_figs.js` | the How to figures: inert copies of the live components with numbered markers that match each table |
| `js/fx_usdthb.js` | the USD/THB 1-year panel on the Correlation tab; draws MOCK data until the payload carries `correlation.fx_usdthb` (contract at the top of the file) |
| `js/app.js` | tabs (arrow keys), theme toggle, tools panel state, build stamp (`DASH_BUILD`), persistence badge, keep-alive, boot |

Bump `DASH_BUILD` in `js/app.js` together with `config.BUILD`. Tests read the frontend
through `tests/frontend_source.py`, which follows the tags in `index.html`, and
`python -m tests.ui_snapshot` diffs the rendered dashboard against its pinned baseline.
