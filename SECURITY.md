# Security Policy

## Scope

Hercyna publishes static HTML/CSS/JS results artifacts, verification tooling (`bench verify`), and adapter benchmarks intended to run inside isolated virtual environments.

## Supported Versions

Only the current release candidate (`v2.0.0-rc1` / branch `v2`) and frozen `protocol/v1.json` / `protocol/v2.json` receive security fixes.

## Reporting a Vulnerability

Do **not** open a public GitHub issue for security vulnerabilities, credential exposure, or verification bypasses.

1. Use GitHub **Private Vulnerability Reporting** (Security advisories tab on the repository) when reporting against `hercyna/hercyna`, OR contact the maintainer through the links listed at the project security contact (`/.well-known/security.txt`).
2. Include:
   - Affected file path or CLI command (`bench ...`),
   - Reproduction steps or minimal proof of concept,
   - Impact assessment (e.g., text-free leak, CSP bypass, verification bypass, dependency issue).
3. We aim to acknowledge receipt within **5 business days** and share a remediation or coordinated disclosure plan within **15 business days**.

## Security Guarantees in This Build

- Static site builds emit **zero inline scripts**, **zero inline styles**, and enforce a strict `Content-Security-Policy` without `unsafe-inline`.
- Submitted benchmark packs (`bench verify`) reject private view directories and check for free-form text leaks (`textfree.py`).
- Provider API keys remain in OS Keychain storage and never enter git history, `Dockerfile`, or container configuration.
