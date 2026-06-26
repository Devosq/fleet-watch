# Security Policy

## Reporting a vulnerability
Please report security issues privately via GitHub Security Advisories
("Report a vulnerability" on the Security tab) rather than a public issue.
You'll get a response within a few days.

## Scope notes
- Secrets live in `.env` (git-ignored). Never commit real tokens.
- Repo descriptions fetched from GitHub are treated as untrusted input: they are
  delimited in the LLM prompt, URLs are stripped from model output, and all
  fields are HTML-escaped before rendering. Still, always inspect a recommended
  repo yourself before cloning it.
