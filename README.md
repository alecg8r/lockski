# lockski.ai on netlify

This folder is the whole site. Drag it (zipped) onto Netlify, or push it to a GitHub repo and connect that repo in Netlify for automatic deploys.

## what's here
- `index.html` — the app (home, hot, games with live scores, my bets, graphs)
- `live.html` — the stand-alone live scoreboard
- `data/` — picks, schedule, results, status. Rewritten by the twice-daily refresh runs (via GitHub) — until GitHub is connected these are a snapshot.
- `logos/` — the 32 team logos
- `netlify/functions/bets.mjs` — the bet store (Netlify Blobs). `netlify.toml`, `package.json` — Netlify config.

## bet store setup (2 minutes, once)
Bets sync across devices only when the site is deployed from GitHub (drag-and-drop deploys don't run functions).
1. Push this folder to a GitHub repo and connect it in Netlify (Add new site → Import an existing project).
2. In Netlify: Site configuration → Environment variables → add `LOCKSKI_PIN` = a pin only you know.
3. Open the site, save a bet, type the pin once. Done. Until then bets stay on the device you're using (the app says so at the top of Home).

## fresh picks twice a day
The refresh runs write `data/*.json` and push to GitHub; Netlify redeploys in ~20 seconds. Set up once GitHub is connected (see ops/).
