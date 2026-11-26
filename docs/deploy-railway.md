# Deploying Hercyna on Railway

This repository deploys the prebuilt static benchmark site using Caddy (`caddy:2-alpine`) from `Dockerfile` and `Caddyfile`. Zero Python processes run in production.

## 1. Create the Railway Service

1. In Railway, create a new project from the public `hercyna/hercyna` repository (or connect your release branch once exported and published).
2. Railway reads `railway.json` automatically (`DOCKERFILE` builder, health check `/healthz`).

## 2. Configure Environment Variables

Set these build/runtime variables in the Railway service dashboard (never commit real endpoints or secrets to git):

| Variable | Required | Purpose |
| --- | --- | --- |
| `HERCYNA_SITE_URL` | Yes | Canonical HTTPS origin (e.g. `https://hercyna.org`) used in canonical tags, Open Graph URLs, `sitemap.xml`, and `feed.xml`. |
| `HERCYNA_NEWSLETTER_ACTION` | Optional | HTTPS form POST URL from your double-opt-in newsletter provider (leave empty to show "Email coming soon"). |
| `HERCYNA_ANALYTICS_SRC` | Optional | HTTPS URL of a cookie-free, self-hosted analytics collector script (leave empty for zero analytics). |

Do not put API keys or Keychain secrets into Railway variables; none are needed to serve the static site.

## 3. Attach Custom Domain and TLS

1. Open **Settings -> Networking -> Custom Domain** in Railway.
2. Add your domain (e.g. `hercyna.org`) and copy the DNS `CNAME` / `ALIAS` target provided by Railway into your DNS provider.
3. Wait for Railway TLS certificate issuance (`HTTPS` active). `Caddyfile` runs with `auto_https off` behind Railway's TLS proxy and sends `Strict-Transport-Security`.

## 4. Trigger Deploy and Run Smoke Checks

After Railway reports deployment healthy on `/healthz`, verify from a terminal:

```bash
SITE="https://hercyna.org"
curl -fsS -I "$SITE/" | grep -iE 'content-security-policy|strict-transport-security|x-content-type-options|x-frame-options|referrer-policy'
curl -fsS "$SITE/healthz"
curl -fsS -I "$SITE/v2/"
curl -fsS -I "$SITE/v1.html"
curl -fsS -I "$SITE/terms.html"
curl -fsS -I "$SITE/feed.xml"
curl -fsS -I "$SITE/robots.txt"
curl -fsS -I "$SITE/sitemap.xml"
curl -fsS -I "$SITE/.well-known/security.txt"
curl -s -o /dev/null -w "%{http_code}\n" "$SITE/does-not-exist-check"  # expect 404
```

Confirm:
- `Content-Security-Policy` does **not** contain `unsafe-inline` or `unsafe-eval`.
- `favicon.ico`, `og/index.png`, `badge/graphiti.svg`, and `data/hercyna-v2-chat-68af8d8e.csv` return HTTP 200.
- `Cache-Control` returns `public, max-age=300, must-revalidate` on HTML and pack JSON files so corrections propagate quickly.

## 5. Rollback

If an issue is discovered after deploy:

1. In Railway **Deployments**, click **Rollback** on the last known good deployment (takes ~15 seconds, no rebuild required).
2. Or revert the bad commit on the release branch and push a clean tag (`v2.0.0-rcX`).
