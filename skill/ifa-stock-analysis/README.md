# ifa-stock-analysis (skill source)

This folder is the skill uploaded to claude.ai. Its arithmetic is the portfolio-deep app's:
`scripts/app/` is generated from the app, so do not edit it.

After any change to the app's engine or refresh steps:

    python -m tools.build_skill --zip

then upload `dist/ifa-stock-analysis.zip` to claude.ai (Settings > Capabilities > Skills).
CI (`tests/test_skill_engine.py`) fails while `scripts/app/` is behind the app.

Try it:

    python skill/ifa-stock-analysis/scripts/value.py skill/ifa-stock-analysis/examples/standard-firm.json --brief
