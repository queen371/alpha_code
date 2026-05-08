# Git hooks

Local-only safety nets. Git does not track `.git/hooks/`, so each clone
needs to install them once.

## Install

From the repo root:

```bash
cp scripts/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

Verify:

```bash
ls -l .git/hooks/pre-commit
```

## What's here

### `pre-commit`

Refuses to commit obvious secrets. Blocks on:

- Anthropic / OpenAI / xAI keys (`sk-ant-...`, `sk-...`, `xai-...`)
- GitHub PATs (`ghp_...`, `gho_...`, `ghs_...`, `ghu_...`)
- AWS access keys (`AKIA...`)
- Google API keys (`AIza...`)
- Slack tokens (`xox[abps]-...`)
- Stripe live keys (`sk_live_...`, `pk_live_...`)
- Generic high-entropy `KEY=` / `TOKEN=` / `SECRET=` / `PASSWORD=`
  assignments with values ≥20 chars (placeholders like `YOUR_KEY`,
  `${VAR}`, `os.getenv(...)`, `<token>` are skipped).
- Files named like `.env`, `*.key`, `*.pem`, `credentials*.json`,
  `secrets*.json` being newly added.

Bypass for false positives:

```bash
git commit --no-verify
```

Use sparingly — the whole point is to catch the case where you forgot.

## When to update

Re-copy from `scripts/hooks/` to `.git/hooks/` whenever this directory
changes. (No auto-sync — keeping it manual avoids "your hook silently
updated" surprises.)
