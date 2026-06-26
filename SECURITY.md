# Security Policy

## Reporting a Vulnerability

These repositories are shared privately. If you discover leaked credentials,
secrets, or any sensitive data in this repo, **do not open a public issue**.
Contact the repository owner directly so it can be rotated and removed.

## Supported Versions

Only the `main` branch is maintained.

## Secrets discipline

This repo is sanitized: no API keys, tokens, IP addresses, project IDs, or
`.env` files are committed. Real values are replaced with placeholders
(e.g. `<VPS1_IP>`, `<SUPABASE_PROJECT_REF>`, `<USERNAME>`). If you fork or
adapt this, keep your own secrets in `.env` (git-ignored) and never commit them.
