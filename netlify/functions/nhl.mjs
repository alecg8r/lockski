// lockski.ai live feed proxy: passes the NHL's public score/schedule feed through our own address, so the app
// never depends on the browser being allowed to call another site. Only these read-only paths are allowed.
const OK = [/^score\/(now|\d{4}-\d{2}-\d{2})$/, /^schedule\/(now|\d{4}-\d{2}-\d{2})$/, /^gamecenter\/\d+\/(landing|boxscore)$/];
export default async (req) => {
  const path = new URL(req.url).pathname.replace(/^\/api\/nhl\//, "");
  if (!OK.some(r => r.test(path))) return new Response("not allowed", { status: 400 });
  try {
    const r = await fetch("https://api-web.nhle.com/v1/" + path, { headers: { "user-agent": "lockski.ai" } });
    const body = await r.text();
    return new Response(body, { status: r.status, headers: { "content-type": "application/json", "cache-control": "public, max-age=10" } });
  } catch (e) { return new Response(JSON.stringify({ error: "upstream" }), { status: 502, headers: { "content-type": "application/json" } }); }
};
export const config = { path: "/api/nhl/*" };
