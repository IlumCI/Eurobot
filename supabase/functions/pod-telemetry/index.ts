// pod-telemetry: secure ingest for pod runtime telemetry.
// Auth is a per-pod token (custom auth, so verify_jwt is disabled) — a compromised pod
// can only ever write its OWN pod_state row, never spoof another pod. Uses the service
// role to write pod_state (which is service-role-only under RLS).
// Deployed via the Supabase MCP (deploy_edge_function); this copy is the source of truth.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

async function sha256Hex(s: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return out === 0;
}

const NUMERIC = ["equity", "deploy", "trend", "hawkes_n", "lvr_daily", "band_lower", "band_upper"];
const STATES = ["HATCHING", "FLYING", "PERCHED", "ASHES", "VETOED"];

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

  let body: { pod_id?: string; token?: string; state?: Record<string, unknown> };
  try {
    body = await req.json();
  } catch {
    return new Response("invalid json", { status: 400 });
  }
  const { pod_id, token, state } = body;
  if (!pod_id || !token || !state || typeof state !== "object") {
    return new Response("bad request", { status: 400 });
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  const { data: pod } = await supabase
    .from("pods").select("id, runtime_token_hash").eq("id", pod_id).maybeSingle();
  const presented = await sha256Hex(token);
  if (!pod?.runtime_token_hash || !timingSafeEqual(pod.runtime_token_hash, presented)) {
    return new Response("unauthorized", { status: 401 });
  }

  const row: Record<string, unknown> = { pod_id, updated_at: new Date().toISOString() };
  if (typeof state.runtime_state === "string" && STATES.includes(state.runtime_state)) {
    row.runtime_state = state.runtime_state;
  }
  for (const k of NUMERIC) {
    if (typeof state[k] === "number" && Number.isFinite(state[k])) row[k] = state[k];
  }

  const { error } = await supabase.from("pod_state").upsert(row, { onConflict: "pod_id" });
  if (error) return new Response("write failed", { status: 500 });
  return new Response(JSON.stringify({ ok: true }), {
    headers: { "Content-Type": "application/json" },
  });
});
