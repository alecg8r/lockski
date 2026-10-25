// lockski.ai bet store (Netlify Blobs, store "lockski", key "bets"). GET reads; POST needs the pin (LOCKSKI_PIN).
// Safety rules:
//  - A failed read never turns into an empty write (503, nothing written).
//  - Writes are conditional on the version just read (etag), retried up to 3 times, so two phones can't erase each other.
//  - Before every change the previous document is copied to "hist/<timestamp>" (last 60 kept), plus one copy per day.
//  - Adds are idempotent: the same bet sent twice within 10 minutes is stored once.
import { getStore } from "@netlify/blobs";

const EMPTY = () => ({ bets: {}, settings: { unit: 10 } });
const okBet = b => b && b.date && Number(b.stake) > 0 && Number(b.mult) >= 1.01 && b.at && (b.type === "parlay" ? Array.isArray(b.legs) && b.legs.length > 1 : b.gid && b.team);
const sameBet = (a, b) => a.type === b.type && Number(a.stake) === Number(b.stake) && Number(a.mult) === Number(b.mult) &&
  JSON.stringify(a.legs || [a.gid, a.team]) === JSON.stringify(b.legs || [b.gid, b.team]) && Math.abs(Date.parse(a.at) - Date.parse(b.at)) < 6e5;

export default async (req) => {
  const store = getStore("lockski");
  const read = async () => {
    const r = await store.getWithMetadata("bets", { type: "json" });   // throws on storage errors -> 503 below
    return r ? { d: r.data || EMPTY(), etag: r.etag, exists: true } : { d: EMPTY(), etag: null, exists: false };
  };
  const json = (o, s = 200) => Response.json(o, { status: s, headers: { "cache-control": "no-store" } });
  try {
    if (req.method === "GET") {
      const u = new URL(req.url);
      if (u.searchParams.get("backups") === "1") {
        const { blobs } = await store.list({ prefix: "hist/" });
        return json({ backups: blobs.map(b => b.key).sort().reverse().slice(0, 60) });
      }
      return json((await read()).d);
    }
    if (req.method !== "POST") return new Response("method", { status: 405 });
    const pin = Netlify.env.get("LOCKSKI_PIN") || "";
    if (pin && req.headers.get("x-lockski-pin") !== pin) return new Response("pin", { status: 401 });
    let body; try { body = await req.json(); } catch { return new Response("bad json", { status: 400 }); }

    for (let attempt = 0; attempt < 3; attempt++) {
      const { d, etag, exists } = await read();
      const before = JSON.stringify(d);
      let id = null;
      if (body.op === "add") {
        if (!okBet(body.bet)) return new Response("bad bet", { status: 400 });
        const dup = Object.entries(d.bets).find(([, b]) => sameBet(b, body.bet));
        if (dup) return json({ ...d, id: dup[0], duplicate: true });
        id = "b" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); d.bets[id] = body.bet;
      } else if (body.op === "delete") { if (!d.bets[String(body.id)]) return json(d); delete d.bets[String(body.id)]; }
      else if (body.op === "unit") { const u = Number(body.unit); if (!(u > 0)) return new Response("bad unit", { status: 400 }); d.settings = { ...(d.settings || {}), unit: u }; }
      else if (body.op === "restore") { for (const [k, b] of Object.entries(body.bets || {})) if (okBet(b) && !d.bets[k]) d.bets[k] = b; }
      else if (body.op === "restoreBackup") {   // bring back a saved copy (adds missing bets, never deletes)
        const old = await store.get(String(body.key || ""), { type: "json" }); if (!old || !old.bets) return new Response("no such backup", { status: 404 });
        for (const [k, b] of Object.entries(old.bets)) if (okBet(b) && !d.bets[k]) d.bets[k] = b;
      }
      else return new Response("unknown op", { status: 400 });
      if (JSON.stringify(d) === before) return json({ ...d, id });
      const stamp = new Date().toISOString();
      try { await store.setJSON("hist/" + stamp, JSON.parse(before)); await store.setJSON("backup-" + stamp.slice(0, 10), { at: stamp, ...d }); } catch {}
      const res = etag ? await store.setJSON("bets", d, { onlyIfMatch: etag }) : !exists ? await store.setJSON("bets", d, { onlyIfNew: true }) : await store.setJSON("bets", d);
      if (res && res.modified === false) continue;   // someone else wrote in between: re-read and re-apply
      try {   // keep the newest 60 history copies
        const { blobs } = await store.list({ prefix: "hist/" });
        const old = blobs.map(b => b.key).sort().slice(0, Math.max(0, blobs.length - 60));
        await Promise.all(old.map(k => store.delete(k)));
      } catch {}
      return json({ ...d, id });
    }
    return new Response("busy, try again", { status: 409 });
  } catch (e) {
    return new Response("bet store unavailable: " + (e && e.message), { status: 503, headers: { "cache-control": "no-store" } });
  }
};

export const config = { path: "/api/bets" };
