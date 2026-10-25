# lockski.ai

NHL picks, live scores, live odds and a bet tracker. Live at lockski.netlify.app.

- `index.html`, `live.html`, `logos/`, `netlify/` — the app (deploys to Netlify only when these change)
- `data/` — live data the app reads straight from GitHub (rewritten by the live engine every 10 minutes)
- `data/inputs/` — goalies, injuries, odds (lineup runs) and box-score results
- `engine/` — the model; `tools/` — live engine, results grading, shot-quality (xG) scoring
- `.github/workflows/live.yml` — runs it all, around the clock

Bets are never stored here; they live in the Netlify bet store behind your pin.
