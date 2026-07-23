-- subscribers: registry of Helio premium subscribers (source of truth for who's paying).
-- Helio handles the actual Telegram access (add on pay / kick on lapse); this table mirrors
-- that state for our dashboard + the future per-pool premium alerts. Service-role only, like
-- pod_state — RLS on with no policies, so only the edge function (service role) can touch it.
create table if not exists public.subscribers (
    id                      uuid primary key default gen_random_uuid(),
    helio_subscription_id   text unique,                    -- Helio's sub id (upsert key)
    status                  text not null default 'active', -- active | ended | paylink
    tier                    text not null default 'premium',
    customer_email          text,
    telegram_user_id        text,                           -- for per-pool alerts targeting later
    telegram_username       text,
    amount                  numeric,
    currency                text,
    started_at              timestamptz,
    expires_at              timestamptz,
    last_event              text,
    raw                     jsonb,                          -- full Helio payload, to finalize mapping
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now()
);

create index if not exists subscribers_status_idx  on public.subscribers (status);
create index if not exists subscribers_expires_idx on public.subscribers (expires_at);
create index if not exists subscribers_tg_idx      on public.subscribers (telegram_user_id);

alter table public.subscribers enable row level security;
-- no policies: only the service role (edge function) bypasses RLS. Client keys get nothing.
