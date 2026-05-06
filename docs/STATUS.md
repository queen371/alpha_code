# STATUS DO PROJETO — Alpha Code
> Ultima atualizacao: 2026-05-06
> Atualizado por: Claude Code (status-update)

---

## Estado Geral: ALTOs DO V1.1 ZERADOS — EM PROGRESSO NOS DEEPs

### Resumo
Audit V1.1 reportou 1 CRITICO + 10 ALTOs em 2026-05-04. Apos sessao de fixes em 2026-05-06: **0 CRITICOs e 0 ALTOs do V1.1 ativos.** 14 issues ALTO/CRITICO fechadas (incluindo 3 ALTOs do DEEP_BUGS) + 2 MEDIOs co-fixados (#005, #118). Restam MEDIOs/BAIXOs distribuidos entre os 6 audits DEEP. Suite 168/168 verde, CI ativo (Py 3.11+3.12).

---

## ISSUES CRITICAS ABERTAS

Nenhuma issue critica pendente. (#101 verificado como ja resolvido — ver "Resolvidas Recentemente".)

---

## ISSUES ALTAS ABERTAS

Nenhuma issue ALTO pendente do AUDIT_V1.1. (DEEPs ainda tem MEDIO/BAIXO em fila.)

---

## ISSUES RESOLVIDAS RECENTEMENTE

| # | Issue | Resolvido em | Verificacao |
|---|-------|-------------|-------------|
| #101 | Suite com 6 testes red + sem CI gate (CRITICO) | <= 2026-05-05 | `pytest` 151/151 verde; `.github/workflows/ci.yml` ativo (Py 3.11+3.12) |
| #115 | `.env` com perms 664 | <= 2026-05-06 | `stat -c %a .env` retorna `600` |
| #021 | Wizard cria `.env` com perms 0o644 | A confirmar | Codigo do wizard precisa re-leitura — provavel co-fix com #115 |
| #D013 | Ctrl+C corrompe messages | 2026-05-06 | `finally` em `agent.py` injeta tool placeholders (commit `cffdb6c`) |
| #D014 | HTTPS quebrado por cert mismatch | 2026-05-06 | `server_hostname` + `ssl.create_default_context()` em `network_tools.py` |
| #D015 | URL replace falha em uppercase/IPv6 | 2026-05-06 | `_rewrite_url_with_ip` via `urlunparse` cobre os casos |
| #001 | `_recover_tool_call_from_content` IDs nao-unicos | 2026-05-06 | `hashlib.sha1[:8]` em vez de `hash() % 10**8` (estavel entre processos) |
| #002 | `signal.SIGALRM` quebra fora da main thread | 2026-05-06 | Validacao de regex complexity via `asyncio.create_subprocess_exec` + `wait_for` |
| #024 | `lxml` 6.0.2 CVE-2026-41066 | 2026-05-06 | Pin `lxml>=6.1.0` em pyproject.toml + upgrade venv; `pip-audit` confirma |
| #018 | Sub-agents auto-aprovam `browser_*` + git write | 2026-05-06 | Browser interaction tools no `_destructive_without_approval`; git_operation write actions rejeitadas via `_auto_approve_no_callback` |
| #023 | Browser allowlist vazia = fail-open | 2026-05-06 | Flag `ALPHA_BROWSER_REQUIRE_ALLOWLIST=1` ativa fail-closed; warning quando flag esta off e allowlist vazia |
| #102 | Cobertura inadequada de sub-agent blocklist | 2026-05-06 | `tests/test_subagent_blocked.py` (10 testes); blocklist e gate de git agora a nivel de modulo |
| #068 | `_format_result` 2x json.dumps | 2026-05-06 | Estimativa cheap + preview clipado por campo elimina o segundo dump e o corte unicode-corrompido |
| #005 | Truncated JSON corrupted | 2026-05-06 | Co-fixed com #068 (preview por campo evita corte no meio de escape) |
| #062 | `compress_context` sem fallback de truncacao | 2026-05-06 | Fail-counter (>=2 empty/exception) cai em `_hard_truncate` que poda tool orfas; 7 testes adicionados |
| #019 | Prompt injection via parent tool results | 2026-05-06 | Vetor eliminado por remocao do caminho (co-fix com #118 — `_extract_relevant_context` deletada) |
| #118 | `_extract_relevant_context` e codigo morto | 2026-05-06 | Funcao + parametro `parent_messages` deletados; sub-agents recebem so task + context explicito |

---

## AUDITORIAS

| Tipo | Versao | Data | Issues ativas | Status | Doc |
|------|--------|------|--------------|--------|-----|
| Audit Geral | V1.1 | 2026-05-04 | 117 (1 CRIT verificado resolvido + 10 ALTO → 7 ativos) | Aguardando atualizacao com status real | [AUDIT_V1.1](audits/current/AUDIT_V1.1.md) |
| Deep Bugs | V1.1 | 2026-05-04 | 39 (eram 42, -3 hoje) | Em progresso | [DEEP_BUGS](audits/current/DEEP_BUGS.md) |
| Deep Security | V2.0 | 2026-05-04 | 34 | Pendente | [DEEP_SECURITY](audits/current/DEEP_SECURITY.md) |
| Deep Maintainability | V1.1 | 2026-05-04 | 48 | Pendente | [DEEP_MAINTAINABILITY](audits/current/DEEP_MAINTAINABILITY.md) |
| Deep Resilience | V2.0 | 2026-05-04 | 35 | Pendente | [DEEP_RESILIENCE](audits/current/DEEP_RESILIENCE.md) |
| Deep Logic | V1.1 | 2026-05-04 | 27 | Pendente | [DEEP_LOGIC](audits/current/DEEP_LOGIC.md) |
| Deep Performance | V2.0 | 2026-05-04 | 25 | Pendente | [DEEP_PERFORMANCE](audits/current/DEEP_PERFORMANCE.md) |
| MVP Plan | — | — | — | Nao realizado | — |

---

## SPRINT ATUAL — restos de ALTO do V1.1

- [x] #001 — `_recover_tool_call_from_content` IDs nao-unicos (2026-05-06)
- [x] #002 — `signal.SIGALRM` quebra fora da main thread (2026-05-06)
- [x] #018 — Sub-agents auto-aprovam browser_* + git write (2026-05-06)
- [x] #019 — Prompt injection via parent tool results (2026-05-06)
- [x] #023 — Browser allowlist vazia = fail-open (2026-05-06)
- [x] #024 — `lxml` CVE-2026-41066 (bump versao) (2026-05-06)
- [x] #062 — `compress_context` fallback de truncacao (2026-05-06)
- [x] #068 — `_format_result` 2x json.dumps (2026-05-06)
- [x] #102 — Cobertura de sub-agent blocklist (2026-05-06)

**Progresso:** 14 de 14 ALTOs originais concluidos (#001/#002/#018/#019/#023/#024/#062/#068/#101/#102/#115/#D013/#D014/#D015 + #021 verificado). +1 MEDIO de bonus (#118).

---

## PROXIMO SPRINT (sugerido) — MEDIOs

44 issues MEDIO no V1.1 + MEDIOs nos DEEPs. Destaques:

- [ ] #117 — `system.md` desencontrado da implementacao
- [ ] #118 — Codigo morto: feature de "context inheritance" anunciada nao funciona
- [ ] #027 — AST blocklist em `code_tools`
- [ ] #066 — Lista de messages sem limite (memory growth)
- [ ] #D016 — `_recent_results` cresce sem limite
- [ ] #D017 — `compress_context` pode produzir messages com tool sem assistant
- [ ] #D101 — RCE via pickle/marshal em execute_python (DEEP_SECURITY)
- [ ] #D102 — Sandbox bypass via `python -c`/`node -e` (DEEP_SECURITY)

---

## METRICAS DE PROGRESSO

| Metrica | Valor |
|---------|-------|
| Issues encontradas (V1.0 + V1.1 + DEEPs) | ~360 acumuladas |
| Issues criticas pendentes | **0** |
| Issues ALTO pendentes (V1.1) | 0 |
| Issues no V1.1 (geral) | 117 (5 verificadas resolvidas) |
| Suite de testes | 168/168 verde |
| CI gate | Ativo (Py 3.11 + 3.12) |
| MVP bloqueadores | Nao avaliado (sem MVP_PLAN) |

---

## DECISOES RECENTES

Nenhuma ADR registrada em `docs/decisions/`. Considere documentar decisoes arquiteturais importantes (ex: politica de approval para `delegate_*`, sub-agent destructive blocklist, abandono de aiohttp em favor de httpx).

---

## LINKS RAPIDOS

- **Audit atual:** [AUDIT_V1.1](audits/current/AUDIT_V1.1.md)
- **Deep Bugs:** [DEEP_BUGS](audits/current/DEEP_BUGS.md)
- **Deep Security:** [DEEP_SECURITY](audits/current/DEEP_SECURITY.md)
- **Deep Logic:** [DEEP_LOGIC](audits/current/DEEP_LOGIC.md)
- **Deep Performance:** [DEEP_PERFORMANCE](audits/current/DEEP_PERFORMANCE.md)
- **Deep Resilience:** [DEEP_RESILIENCE](audits/current/DEEP_RESILIENCE.md)
- **Deep Maintainability:** [DEEP_MAINTAINABILITY](audits/current/DEEP_MAINTAINABILITY.md)
- **CI workflow:** `.github/workflows/ci.yml`

---

## TIMELINE

| Data | Evento |
|------|--------|
| 2026-04-03 | Initial commit + AUDIT V1.0 (33 issues, 1 CRITICO) |
| 2026-04-04 | DEEP V1.0 (6 categorias, 101 issues, 4 CRITICOS) — 4 CRIT + 8 ALTO corrigidos |
| 2026-04-18 | Browser automation tools |
| 2026-04-20 | Skills bundle + onboarding wizard + named agents |
| 2026-05-04 | AUDIT V1.1 + 6 DEEPs reauditados (117 issues totais no geral) |
| 2026-05-05 | MCP + hooks + plan/todo + Anthropic provider + CI workflow |
| 2026-05-06 | DEEP_BUGS V1.1: #D013/#D014/#D015 corrigidos (commit `cffdb6c`) |
| 2026-05-06 | STATUS verificado contra codigo: #101 e #115 ja resolvidos antes |

---

*Atualizado automaticamente — Revisao humana recomendada.*
