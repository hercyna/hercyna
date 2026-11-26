# Multi-stage build: build static artifacts with Python 3.12 + uv, serve with Caddy only (zero Python at runtime).
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HERCYNA_ACCEPT_TERMS=1

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE LICENSE-DATA NOTICE TERMS.md LEGAL.md PRIVACY.md CITATION.cff CHANGELOG.md ./
COPY src ./src
COPY protocol ./protocol
COPY configs ./configs
COPY data ./data
COPY results/table ./results/table
COPY results/validity ./results/validity
COPY results/verdicts ./results/verdicts
COPY results/v2/diagnostics ./results/v2/diagnostics
COPY site/brand ./site/brand
COPY site/copy ./site/copy
COPY site/media ./site/media

ARG HERCYNA_SITE_URL=https://hercyna.org
ARG HERCYNA_NEWSLETTER_ACTION=
ARG HERCYNA_ANALYTICS_SRC=
ENV HERCYNA_SITE_URL=${HERCYNA_SITE_URL} \
    HERCYNA_NEWSLETTER_ACTION=${HERCYNA_NEWSLETTER_ACTION} \
    HERCYNA_ANALYTICS_SRC=${HERCYNA_ANALYTICS_SRC}

RUN uv sync --frozen --no-dev && \
    uv run --frozen bench site build --out /srv/site --site-url "$HERCYNA_SITE_URL" --accept-terms

FROM caddy:2-alpine AS runtime
COPY --from=builder /srv/site /srv/site
COPY Caddyfile /etc/caddy/Caddyfile
ENV PORT=8080
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
