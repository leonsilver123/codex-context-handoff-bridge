# Publishing Checklist

Use this checklist before publishing the repository on GitHub.

## Recommended Repository Contents

Keep:

```text
README.md
README.zh-CN.md
ACCEPTANCE.md
AGENTS.md
install.ps1
LICENSE
CONTRIBUTING.md
SECURITY.md
CHANGELOG.md
scripts/codex_handoff.py
scripts/codex-handoff.ps1
scripts/handoff-smoke.ps1
scripts/test_codex_handoff.py
.github/workflows/ci.yml
docs/INSTALL.md
docs/USAGE.md
docs/PUBLISHING.md
docs/zh-CN/INSTALL.md
docs/zh-CN/USAGE.md
.gitignore
```

Avoid publishing machine-local runtime files unless you intentionally want them
as examples:

```text
.codex-handoff/
.tmp-tests/
```

The installer creates a fresh `.codex-handoff/` scaffold in the target project.

## Verify Before Release

Run from the repository root:

```powershell
.\scripts\codex-handoff.ps1 verify
.\scripts\handoff-smoke.ps1 -SkipAuto
```

Also test installing into a clean temporary project:

```powershell
$tmp = Join-Path $env:TEMP "codex-handoff-clean-project"
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
.\install.ps1 -ProjectPath $tmp -Force
Push-Location $tmp
.\scripts\codex-handoff.ps1 doctor --json
Pop-Location
```

## First GitHub Publish

From the repository root:

```powershell
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/<your-name>/<your-repo>.git
git push -u origin main
```

If the repository already exists locally, only add the remote and push.

## License

This project includes an MIT license. If you need a different license, replace
`LICENSE` before publishing.

## Release Tag

For the first release:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

Then create a GitHub Release from tag `v0.1.0` and paste the relevant section
from `CHANGELOG.md`.

## Suggested GitHub Description

```text
File-backed Codex context compression and handoff bridge for long-running Codex App projects.
```

## Suggested Topics

```text
codex
ai-agent
context-management
handoff
automation
developer-tools
```
