# Rate limiting

Code: `app/helpers/rate_limit.py`. Tests: `tests/test_rate_limit*.py`, `tests/test_rate_limited_routes.py`.

## What is limited

| Rule (env override) | Default | Counts | Where |
|---|---|---|---|
| `login_ip` | 20 / 1 min | every sign-in attempt from an IP | `POST /auth/login` |
| `login_account` | 5 / 15 min | attempts on one email since its last successful sign-in | `POST /auth/login` |
| `register_ip` | 3 / 1 h | new accounts from an IP | `POST /auth/register` |
| `register_invited_ip` | 30 / 1 h | sign-ups carrying a valid invite token for that email | `POST /auth/register` |
| `verify_account` | 3 / 1 h | confirmation emails to one address (signup + resend) | `/auth/register`, `/auth/verification/resend` |
| `reset_account` | 3 / 1 h | password-reset emails to one address | `/auth/password/forgot` |
| `email_ip` | 20 / 1 h | resend + reset requests from an IP | same |
| `invite_user` | 10 / 1 h | invites sent or resent by one administrator | `POST /users/invite`, `/users/{id}/resend-invite` |
| `invite_tenant` | 30 / 1 h | the same, per school across all its admins | same |
| `api_user` | 120 / 1 min | any authenticated request by one user | everything using `get_current_user`, plus `/auth/me`, `/auth/session` |
| `enquiry_ip` | 60 / 1 h | unauthenticated enquiry submissions | `POST /enquiries` |

Windows slide: a request counts until exactly one window after it was made. Rejected
requests are not counted, so retrying while blocked doesn't extend the wait.

Over a limit the API answers `429` with a `Retry-After` header (seconds) and the usual
`{"detail": "Too many attempts. Please try again in 15 minutes."}`, which the UI already
shows as-is. Nothing in the response depends on whether an email has an account: unknown
emails are counted and blocked exactly like real ones.

AI marking runs in `alteris-marking-api-service`, which has its own deployment; its
subscription-based limits belong there.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `UPSTASH_REDIS_REST_URL` | – | Already used. Required in production. |
| `UPSTASH_REDIS_REST_TOKEN` | – | Already used. Secret: set in Render, never commit. |
| `RATE_LIMIT_<RULE>` | see table | `count/seconds`, e.g. `RATE_LIMIT_LOGIN_IP=30/60`. A malformed value logs `rate_limit.bad_config` and the default is used. |
| `CLIENT_IP_HEADER` | `cf-connecting-ip` | Header with the caller's IP that only our edge can set. Empty to disable. |
| `TRUSTED_PROXY_HOPS` | `1` | Used when that header is absent: the `X-Forwarded-For` entry this many places from the right. Entries further left are client-supplied and ignored. |
| `RATE_LIMITING_ENABLED` | `true` | Emergency switch (`false` turns every limit off). |

## Redis

Upstash Redis (REST API) is used, as before: no new service or Python dependency.
Each check is one `EVAL` of a short Lua script, so counting is atomic across every
worker and Render instance. Keys are `rl2:<rule>:<sha256 of subject>`; raw emails,
user ids and IPs are never stored. Each key expires one window after its last request.

Put the Upstash database in the region closest to the Render service: every
authenticated request now waits on one Upstash round trip (a few ms in-region, more
across regions). The HTTP connection is reused between requests.

### When Upstash is down or slow (2 s timeout)

* Sign-in, sign-up, account emails and invites fall back to the same limits counted in
  each instance's memory. Protection is weaker (each instance counts separately) but
  never gone, and nobody is locked out by the outage itself.
* General API traffic is let through, so schools keep working.
* `rate_limit.redis_unavailable` is logged at ERROR, at most once a minute per instance.
* Recovery is automatic on the next request that reaches Redis.

## Deploying on Render

1. Upstash console: confirm the existing database's region matches Render's (or create
   one there) and copy the REST URL and token.
2. Render → `alteris-auth-service` → Environment: make sure `UPSTASH_REDIS_REST_URL` and
   `UPSTASH_REDIS_REST_TOKEN` are set. Leave the `RATE_LIMIT_*` overrides unset to use the
   defaults above.
3. Deploy. In Logs, search `rate_limit.ip_source`. One line per instance should say
   `source=cf-connecting-ip`. If it says `x-forwarded-for` or `peer`, client IPs aren't
   coming from Cloudflare's header: stop and check before relying on per-IP limits
   (all users could otherwise share one IP's budget). Render's own forwarding behaviour
   is not documented, which is why the Cloudflare header is preferred.
4. Search `rate_limit.redis_unavailable`: there should be none.

## Supabase: protection FastAPI cannot give

Both school UIs sign in by calling Supabase directly
(`supabase.auth.signInWithPassword` in `alteris-general-ui/app/login/page.tsx` and
`alteris-marking-ui-app/src/lib/auth.tsx`). Those requests never reach this API, so
`POST /auth/login`'s limits do **not** protect them. Configure in the Supabase dashboard:

1. **Authentication → Rate Limits**: lower "sign-ups and sign-ins" (default 30 per 5 min
   per IP) to around 10, and check the token-refresh and verification limits.
2. **Authentication → Attack Protection → CAPTCHA**: enable Cloudflare Turnstile with the
   site's secret key. Supabase then verifies the token server-side on sign-in, sign-up
   and password recovery; the UIs must pass `options: { captchaToken }` to
   `signInWithPassword` (and to any other Supabase auth call they make). Do the frontend
   change in the same release, or every sign-in will fail.
3. **Leaked password protection** (Pro plan) rejects passwords found in breaches.

Alternatively, switch the UIs to `POST /auth/login` (it already returns Supabase's
session tokens) so these limits apply, while keeping Supabase's own limits as a backstop.

## Monitoring (Render log search)

| Search | Meaning |
|---|---|
| `rate_limit.blocked rule=login_account` | someone is guessing one account's password (subject is a hash) |
| `rate_limit.blocked rule=login_ip` / `register_ip` | one IP probing many accounts or creating accounts (IP shown) |
| `rate_limit.blocked rule=api_user` | a user or script hammering the API |
| `rate_limit.redis_unavailable` | Upstash unreachable, fallback in force |
| `rate_limit.bad_config` | a `RATE_LIMIT_*` value is malformed |

Each blocked line appears at most once a minute per subject per instance. Consider a
Render log stream to a tool that can alert on these (Better Stack, Datadog, etc.).

## Cost

Upstash bills per command ($0.20 per 100K on pay-as-you-go; 500K/month free). The
general API limit adds one command per authenticated request; auth endpoints add one or
two. For example 1,000 daily active staff × 300 requests/day ≈ 9M commands/month ≈ $18.
Compare with Upstash's fixed plan ($10/month, 250 MB) once usage is known (Upstash's
console shows the real command count). Nearly all of that is the `api_user` check; the
auth limits cost almost nothing. If it grows too costly, the cheapest change is to
apply `api_user` only to writes (POST/PATCH/PUT/DELETE).

## Running the tests

    uv run pytest tests/

The Redis integration tests (`tests/test_rate_limit_redis.py`) run against a local Redis
behind Upstash's REST emulator; the file's docstring has the Docker commands.
