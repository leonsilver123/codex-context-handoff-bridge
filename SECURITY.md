# Security Policy

## Supported Versions

This project is currently pre-1.0. Security fixes should target the latest
published version.

## Reporting A Vulnerability

Please open a private security advisory if the repository has GitHub security
advisories enabled. Otherwise, contact the repository owner privately before
opening a public issue.

## Sensitive Data

This tool is designed to avoid reading secrets by default. Do not store secrets
inside `.codex-handoff/` files.

The default risk rules flag common secret patterns and sensitive paths such as:

```text
.env
.env.local
.ssh/
id_rsa
id_ed25519
```

## Scope

The project does not intentionally execute unknown Codex App internals. App
Server HTTP execution requires an explicit `--server-url` or configuration.
