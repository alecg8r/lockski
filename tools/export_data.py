"""Write the refresh run's results into this repo's data/ folder (the Netlify app reads these files).

Usage: python3 tools/export_data.py WORK/in META_JSON
  WORK/in   : the engine work folder after run.py (model.json, ledger.json, weeks.json)
  META_JSON : the meta/status document exactly as written to the database this run
Never touches bets: bets live in the Netlify bet store, not in this repo.
"""
import json, sys
from pathlib import Path

W, META = Path(sys.argv[1]), Path(sys.argv[2])
D = Path(__file__).resolve().parents[1] / "data"
D.mkdir(exist_ok=True)
old_led = json.loads((D / "ledger.json").read_text()) if (D / "ledger.json").exists() else {}
for name in ("model", "ledger", "weeks"):
    src = W / f"{name}.json"
    data = json.loads(src.read_text())
    assert data, f"{name}.json is empty"
    if name == "ledger":   # a final the live job already graded stays, unless the daily run has its own result
        for gid, doc in old_led.items():
            if doc.get("result") and gid in data and not data[gid].get("result"):
                data[gid]["result"] = doc["result"]
    (D / f"{name}.json").write_text(json.dumps(data, separators=(",", ":")))
meta = json.loads(META.read_text()); meta = meta.get("data", meta)
(D / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
m = json.loads((D / "model.json").read_text())
assert len(m.get("teams", {})) == 32, "model must have 32 teams"
print("exported:", {p.name: p.stat().st_size for p in D.glob("*.json")})
