# C3.3.3 Historical diesel backfill bridge

This is an offline historical analysis of a single diesel news episode. It does not prove real-time detection or causation.

Canonical story `story_4eea0c019e02a0b0`; trigger `fast_7dc1052a23f19abf74ac` published 2026-10-09 18:46:14 UTC; additional detail `story_68317846dddd9807` published 18:47:46 UTC. MT5 report event anchor 18:46:00 UTC is 14 seconds before trigger. The earliest reported post-event closed M15 window starts 19:00 UTC. News was observed by collector at 20:18 UTC, so this is historical backfill.

Run in project root, after copying C3_3_3_validate_diesel.py alongside story_intermarket_validation_engine.py:

```
python C3_3_3_validate_diesel.py --stories data/news/fast_news_stories.json --interpretations data/news/fast_news_interpretations.json --reaction data/news/diesel_historical_reaction.json --output data/news/diesel_story_validation.json
python -m unittest -v test_C3_3_3_validate_diesel
```

The repository regression test uses synthetic JSON fixtures in tests/fixtures/c333. Actual historical JSON files remain local and are excluded from Git. Synthetic tests do not verify the historical results described below.

Both mechanism aggregate statuses are `insufficient_data` at 60m. Oil direction is contradicted in both; USD and Gold direction support the secondary candidate, but yields and inflation expectations are missing. 24h windows are incomplete due to Friday closure. All trade permissions remain false. Manual related-story link is case-specific, NOT automatic story merging. Review before production integration. The existing validator temporarily receives a copied report with canonical event_id; original report ID is preserved in provenance.
