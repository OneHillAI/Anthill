// Cloudflare Pages Function -> served at https://anthill.run/api/health
//
// A trivial liveness probe and the first tenant of the anthill.run "services" surface: any file under
// www/functions/ maps to a path (functions/api/health.js -> /api/health). Kept dependency-free and
// side-effect-free so it doubles as a smoke test that the Functions runtime is wired up.
export function onRequestGet() {
  return new Response(JSON.stringify({ ok: true, service: "anthill.run" }), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
