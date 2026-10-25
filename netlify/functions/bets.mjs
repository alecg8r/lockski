// lockski.ai bet store: one JSON document in Netlify Blobs. GET reads; POST needs the pin (LOCKSKI_PIN env var).
// Every write also keeps a dated backup, so a bad day can be undone.
import { getStore } from "@netlify/blobs";

export default async (req) => {
  const store = getStore("lockski");
  const read = async () => (await store.get("bets", { type: "json" })) || { bets: {}, settings: { unit: 10 } };
  if (req.method === "GET") return Response.json(await read(), { headers: { "cache-control": "no-store" } });
  if (req.method !== "POST") return new Response("method", { status: 405 });
  const pin = Netlify.env.get("LOCKSKI_PIN") || "";
  if (pin && req.headers.get("x-lockski-pin") !== pin) return new Response("pin", { status: 401 });
  let body; try { body = await req.json(); } catch { return new Response("bad json", { status: 400 }); }
  const d = await read(); let id = null;
  const okBet = b => b && b.date && Number(b.stake) > 0 && Number(b.mult) >= 1.01 && b.at && (b.type === "parlay" ? Array.isArray(b.legs) && b.legs.length > 1 : b.gid && b.team);
  if (body.op === "add") { if (!okBet(body.bet)) return new Response("bad bet", { status: 400 }); id = "b" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); d.bets[id] = body.bet; }
  else if (body.op === "delete") { delete d.bets[String(body.id)]; }
  else if (body.op === "unit") { const u = Number(body.unit); if (!(u > 0)) return new Response("bad unit", { status: 400 }); d.settings = { ...(d.settings || {}), unit: u }; }
  else if (body.op === "restore") { for (const [k, b] of Object.entries(body.bets || {})) if (okBet(b) && !d.bets[k]) d.bets[k] = b; }
  else return new Response("unknown op", { status: 400 });
  await store.setJSON("bets", d);
  try { await store.setJSON("backup-" + new Date().toISOString().slice(0, 10), { at: new Date().toISOString(), ...d }); } catch {}
  return Response.json({ ...d, id }, { headers: { "cache-control": "no-store" } });
};

export const config = { path: "/api/bets" };
