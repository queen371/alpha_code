# CodexBar CLI quick ref (usage + cost)

## Install

- App: Preferences -> Advanced -> Install CLI
- Repo: ./bin/install-codexbar-cli.sh

## Commands

- Usage snapshot (web/cli sources):
  - codexbar usage --format json --pretty
  - codexbar --provider all --format json
- Local cost usage (per provider):
  - codexbar cost --format json --pretty
  - codexbar cost --provider <provider-name> --format json

## Cost JSON fields

The payload is an array (one per provider).

- provider, source, updatedAt
- sessionTokens, sessionCostUSD
- last30DaysTokens, last30DaysCostUSD
- daily[]: date, inputTokens, outputTokens, cacheReadTokens, cacheCreationTokens, totalTokens, totalCost, modelsUsed, modelBreakdowns[]
- modelBreakdowns[]: modelName, cost
- totals: totalInputTokens, totalOutputTokens, cacheReadTokens, cacheCreationTokens, totalTokens, totalCost

## Notes

- Cost usage is local-only. It reads JSONL logs under provider-specific session directories (e.g. `~/.codex/sessions/`). Refer to the provider's docs for the exact path.
- If web usage is required (non-local), use codexbar usage (not cost).
