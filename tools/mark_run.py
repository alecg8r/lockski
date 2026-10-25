"""Record a failed or skipped cycle in data/meta.json (runLog) without touching any other file.
Usage: python3 tools/mark_run.py "<reason>" """
import json, sys
from datetime import datetime, timezone
from pathlib import Path
p = Path(__file__).resolve().parents[1] / "data" / "meta.json"
meta = json.loads(p.read_text()) if p.exists() else {}
entry = {"at": datetime.now(timezone.utc).isoformat(), "kind": "live", "ok": False, "reason": sys.argv[1] if len(sys.argv) > 1 else "failed"}
meta["runLog"] = [entry] + [x for x in meta.get("runLog", []) if x.get("kind") == "live"][:29]
p.write_text(json.dumps(meta, separators=(",", ":")))
print("recorded:", entry["reason"])
