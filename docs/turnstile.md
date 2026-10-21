# Cloudflare Turnstile

Code: `app/helpers/captcha.py` (backend), `app/lib/turnstile.ts` (alteris-general-ui).
Tests: `tests/test_captcha.py`.

## What it protects

The public forms that go through this API, where bots were creating accounts:

| Endpoint | Frontend |
|---|---|
| `POST /auth/register` | `/get-started`, `/accept-invite` |
| `POST /auth/verification/resend` | `/get-started`, `/accept-invite` |
| `POST /auth/password/forgot` | `/login` (forgot password), `/accept-invite` |

Each request carries a `captcha_token`; the API checks it with Cloudflare before doing
anything else (before rate limits, so bot traffic doesn't use up a school network's
budget). Signing in goes straight to Supabase and isn't covered (see
`docs/rate-limiting.md`).

The widget is invisible unless Cloudflare wants a click; then a small "Verify you are
human" box appears at the bottom of the screen. Tokens are single-use, so every submit
gets a fresh one.

## Responses

| Situation | Response |
|---|---|
| Missing, expired, reused or fake token | `400` "We couldn't confirm you're human. Please try again." |
| Cloudflare unreachable (after one retry), or our secret key is wrong | `503` "Human verification is unavailable right now. Please try again shortly." |

## Setting it up

1. Cloudflare dashboard → Turnstile → Add widget. Hostnames: `app.skolarly.net`,
   `www.app.skolarly.net`, `localhost`. Widget mode: **Managed**.
2. Deploy this backend. Without `TURNSTILE_SECRET_KEY` nothing changes yet.
3. Frontend: set `NEXT_PUBLIC_TURNSTILE_SITE_KEY` (the site key, public) and deploy.
   Forms now send tokens; the backend still ignores them.
4. Render → Environment: set `TURNSTILE_SECRET_KEY` (secret; never commit it). From
   now on requests without a valid token are refused.
5. Try a sign-up on the live site, and search Render logs for `captcha.`.

To switch it off in an emergency, remove `TURNSTILE_SECRET_KEY` in Render.

For local development leave both keys unset, or use Cloudflare's test keys: site key
`1x00000000000000000000BB` (invisible, always passes) with secret
`1x0000000000000000000000000000000AA`; secret `2x0000000000000000000000000000000AA`
always fails.

## Monitoring

| Render log search | Meaning |
|---|---|
| `captcha.failed form=register` | a sign-up without a valid token (bots; at most one line per IP per minute) |
| `captcha.unavailable` | Cloudflare's verification couldn't be reached |
| `captcha.misconfigured` | `TURNSTILE_SECRET_KEY` is wrong |

Cloudflare's Turnstile analytics show how many visitors were challenged and how many
failed; a jump in failures from real users may mean a school network blocks
`challenges.cloudflare.com`.

## Running the tests

    uv run pytest tests/test_captcha.py
    TURNSTILE_LIVE_TEST=1 uv run pytest tests/test_captcha.py -k cloudflare_test_keys

The second command calls Cloudflare with its official test keys.
