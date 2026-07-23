// helio-webhook: record Helio (hel.io) subscription events into `subscribers`.
//
// Helio itself handles the Telegram gating (its bot adds a buyer to the premium channel on
// payment and kicks them when the sub lapses). This function is NOT the enforcer — it's our
// own subscriber registry: the source of truth for who's paying, which the dashboard and the
// future per-pool premium alerts build on. Enforcement stays with Helio; we just mirror state.
//
// Auth: Helio sends `Authorization: Bearer <shared token>` (generated when you create the
// webhook). We compare it constant-time against the HELIO_WEBHOOK_TOKEN secret. (custom auth,
// so deploy with verify_jwt disabled, like pod-telemetry.)
//
// Events: SUBSCRIPTION_STARTED / SUBSCRIPTION_RENEWED (or *_PENDING_PAYMENT grace) -> active,
//         SUBSCRIPTION_ENDED -> ended. PayLink CREATED (one-off) is stored as a paylink event.
//
// NOTE: Helio keeps exact field names behind an authed API reference, so extraction below is
// defensive (deep key search) and we ALWAYS store the raw payload in `raw`. Finalize the field
// mapping against the FIRST real webhook (it's logged + saved raw) — don't guess-and-forget.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return out === 0;
}

// Depth-first search for the first value under any of `keys` (case-insensitive). Helio nests
// buyer/amount details, so we look everywhere rather than hard-coding a path we can't verify yet.
function deepFind(obj: unknown, keys: string[], depth = 0): unknown {
  if (obj == null || depth > 6) return undefined;
  const want = keys.map((k) => k.toLowerCase());
  if (typeof obj === "object") {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      if (want.includes(k.toLowerCase()) && v != null && typeof v !== "object") return v;
    }
    for (const v of Object.values(obj as Record<string, unknown>)) {
      const hit = deepFind(v, keys, depth + 1);
      if (hit !== undefined) return hit;
    }
  }
  return undefined;
}

const asStr = (v: unknown) => (v == null ? null : String(v));
const asNum = (v: unknown) => {
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
};

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

  const expected = Deno.env.get("HELIO_WEBHOOK_TOKEN") ?? "";
  const auth = req.headers.get("authorization") ?? "";
  const presented = auth.replace(/^Bearer\s+/i, "");
  if (!expected || !timingSafeEqual(expected, presented)) {
    return new Response("unauthorized", { status: 401 });
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return new Response("invalid json", { status: 400 });
  }

  const event = String(
    body.event ?? body.type ?? body.eventType ?? (body.event as Record<string, unknown>)?.type ?? "",
  ).toUpperCase();
  const isSub = event.includes("SUBSCRIPTION");
  const ended = event.includes("ENDED") || event.includes("CANCEL");

  // Best-effort field extraction — finalize against a real payload (stored in `raw`).
  const subId = asStr(deepFind(body, ["subscriptionId", "subscription_id"])) ??
    asStr(deepFind(body, ["id"]));
  const row: Record<string, unknown> = {
    helio_subscription_id: subId,
    status: isSub ? (ended ? "ended" : "active") : "paylink",
    tier: asStr(deepFind(body, ["planName", "productName", "name"])) ?? "premium",
    customer_email: asStr(deepFind(body, ["email", "customerEmail", "buyerEmail"])),
    telegram_user_id: asStr(deepFind(body, ["telegramUserId", "telegramId", "telegram_user_id"])),
    telegram_username: asStr(deepFind(body, ["telegramUsername", "telegramHandle"])),
    amount: asNum(deepFind(body, ["amount", "totalAmount", "totalAmountUsd"])),
    currency: asStr(deepFind(body, ["currency", "currencyType", "stableCoin", "symbol"])),
    started_at: asStr(deepFind(body, ["startDate", "createdAt", "startedAt"])),
    expires_at: asStr(deepFind(body, ["nextChargeDate", "endDate", "expiresAt", "nextRenewalDate"])),
    last_event: event || "UNKNOWN",
    raw: body,
    updated_at: new Date().toISOString(),
  };

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  // Upsert on the Helio subscription id when we have one, so STARTED then ENDED collapse to one
  // row; if we couldn't find an id yet, just insert (still captured in `raw` to finalize later).
  const q = subId
    ? supabase.from("subscribers").upsert(row, { onConflict: "helio_subscription_id" })
    : supabase.from("subscribers").insert(row);
  const { error } = await q;
  if (error) {
    console.error("helio-webhook write failed", error.message, "event", event);
    return new Response("write failed", { status: 500 });
  }
  // Log the event so the first real payload is easy to find in the function logs.
  console.log("helio-webhook", event, "sub", subId ?? "(no id)", "-> status", row.status);
  return new Response(JSON.stringify({ ok: true }), {
    headers: { "Content-Type": "application/json" },
  });
});
