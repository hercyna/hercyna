# Privacy Notice (DRAFT, for review)

Status: DRAFT, for review. Not legal advice.

## Plain-Language Summary

1. **Static site:** No cookies, no third-party scripts, no ad trackers, and no analytics by default. All fonts load from the same origin (`fonts/`).
2. **Optional analytics:** Off by default. Only enabled if an operator sets an explicit self-hosted, cookie-free analytics script URL (`HERCYNA_ANALYTICS_SRC`), so no cookie banner is needed.
3. **Newsletter and updates:** You can follow via Atom feed (`feed.xml`) or GitHub releases without sharing any email. If an external double-opt-in email form (`HERCYNA_NEWSLETTER_ACTION`) is configured, your email is submitted directly from your browser to that provider under an unchecked consent box. Hercyna stores zero emails.
4. **CLI (`bench`):** First-run acceptance writes only `terms_version` and `accepted_date` to `~/.config/hercyna/accepted.json`. No name, email, host identifier, or usage telemetry is ever collected or transmitted.

## 1. Website Data Practices

- **No third-party requests:** Fonts (`Newsreader`, `IBM Plex Sans`, `JetBrains Mono`) and preview media are self-hosted on this origin.
- **No cookies or local identity tracking:** Theme preference (`light` or `dark`) uses `localStorage` under `hercyna-theme` purely in your browser and never leaves your device.
- **Server logs:** Standard infrastructure HTTP logs handled by the hosting platform are limited to operational security and reliability diagnostics as governed by the hosting platform.
- **Cookie-free analytics policy:** Analytics are disabled by default. If later enabled via `HERCYNA_ANALYTICS_SRC`, only a cookie-free, self-hostable collector is permitted, keeping the site free of tracking cookies.

## 2. Updates and Email Subscriptions

- **When email is inactive:** The updates panel shows "Email coming soon" with direct Atom (`feed.xml`) and GitHub release links.
- **When an external provider URL is configured:** Subscriptions post directly to the configured provider (`HERCYNA_NEWSLETTER_ACTION`), use double opt-in confirmation handled by that provider, require an explicit unchecked consent checkbox, include a hidden anti-spam honeypot field, load zero provider JavaScript on Hercyna pages, and store zero subscriber addresses on Hercyna servers or git repositories.

## 3. CLI Acceptance Storage

Running `bench` checks `~/.config/hercyna/accepted.json` (respecting `$XDG_CONFIG_HOME`). The file contains strictly:

```json
{
  "terms_version": "2026-11-25-v1",
  "accepted_date": "2026-11-25"
}
```

No name, email address, username, path, or telemetry event is stored or sent anywhere.

## 4. Public Benchmark Submissions

Submitted packs undergo strict verification (`bench verify`) requiring text-free JSON summaries, timing metrics, and anonymized hashes. Any submission containing free-form user prompts or private logs is rejected automatically.
