# Helio premium subscriptions — setup

Helio (hel.io, MoonPay-owned, ~2% fee, Solana-native) does the payments **and** the Telegram
gating. This function is only the **subscriber registry** — it records who's paying so the
dashboard and per-pool alerts can use it. Helio still adds/kicks people from the channel itself.

## 1. Channels (one-time)

- **Free channel** ("Vältgeist Alerts!", `-1004440662799`) — stays public. Helio never touches it.
- **Premium channel** — create a NEW **private** channel (e.g. "Vältgeist Alerts — PRO"). Add
  **two** admins: (a) Helio's gating bot, (b) your alerts bot (so it can post premium content).
  Give Helio the **premium** channel's id only.

## 2. Helio dashboard

1. Create a **Subscription** product: **$21 / month**, settle in **USDC** (accept SOL at spot too).
2. Under access/gating, connect **Telegram** and select the **premium** channel.
3. Create a **webhook** for subscription events (`SUBSCRIPTION_STARTED`, `_RENEWED`/`_PENDING_PAYMENT`,
   `_ENDED`):
   - URL: `https://<your-project-ref>.supabase.co/functions/v1/helio-webhook`
   - Copy the generated **shared token**.

## 3. Deploy this function

```bash
# table (once):
supabase migration up            # or apply supabase/migrations/0001_subscribers.sql via the MCP
# secret + deploy (verify_jwt OFF — Helio uses its own bearer token, not a Supabase JWT):
supabase secrets set HELIO_WEBHOOK_TOKEN=<the shared token from Helio>
supabase functions deploy helio-webhook --no-verify-jwt
```
(Both the migration and the deploy can also be done through the Supabase MCP.)

## 4. Finalize the field mapping

Helio keeps exact webhook field names behind an authed reference, so the function extracts fields
defensively and **stores the full payload in `subscribers.raw`**. After the FIRST real (or Helio
test) webhook:

```sql
select last_event, raw from public.subscribers order by created_at desc limit 1;
```

Look at `raw`, then tighten the `deepFind(...)` key lists in `index.ts` for `subscriptionId`,
buyer email/telegram, amount, and `expires_at`. Redeploy. Until then, `raw` guarantees nothing
is lost.

## What this does / doesn't do

- ✅ Verifies Helio's bearer token, records STARTED/RENEWED (active) and ENDED, keeps raw payloads.
- ✅ Gives per-subscriber `telegram_user_id` — the hook the per-pool premium alerts will target.
- ❌ Does NOT add/kick people from Telegram — Helio does that. Don't duplicate it.
- ❌ Does NOT take custody of anything. This is subscription revenue, not user trading funds.
