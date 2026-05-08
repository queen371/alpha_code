# STATUS DO PROJETO — Alpha Code
> Ultima atualizacao: 2026-05-07 23:00
> Atualizado por: Claude Code (status-update)

---

## Estado Geral: TODOS OS CRITICOS+ALTOS+V2.0 FECHADOS

### Resumo
0 CRITICOs e 0 ALTOs ativos. **Todos os 6 audits DEEP V2.0 fechados na sua leva nova** (BUGS 9/9, LOGIC 9/9, MAINTAINABILITY 7/8 + DM001 deferido, PERFORMANCE 13/13, RESILIENCE 9/9, SECURITY 12/12). Restam apenas **stragglers V1.0/V1.1** referenciados nos planos dos DEEPs mas nao endereçados na rodada V2.0 (~64 MEDIOs/BAIXOs distribuidos — diminui a cada sprint de quick wins). Suite **388/388 verde** (+15 testes neste sprint), CI ativo (Py 3.11+3.12).

---

## ISSUES CRITICAS ABERTAS

Nenhuma issue critica pendente.

---

## ISSUES ALTAS ABERTAS

Nenhuma issue ALTO pendente. Todos os 14 ALTOs originais (V1.1 + DEEPs V2.0) fechados.

---

## ISSUES RESOLVIDAS RECENTEMENTE (V2.0 + V1.1)

| Commit | O que fechou |
|--------|--------------|
| (workdir 2026-05-07 23h) | **MAINT + PERF quick wins batch 2** — 6 fixes: #DM007 (.env.example completo), #086 (wizard `_PROVIDERS` derivado de config), #095 (`_PROJECT_ROOT` compartilhado), #096 (tools index docstring), #D010 (display constants centralizados), #D022 (extract TTL cache 30s/100). +15 testes em `test_maint_perf_quick_wins_2.py`. |
| (workdir 2026-05-07 22h) | **MAINT + PERF quick wins** — 14 fixes (DM004/DM005 dead imports, DM014 script_path init, ALPHA_FEATURES alias removed, #083 dedupe, #085 LOOP_DETECTION dict, #090 shlex.quote, #091 comment, #093 docstring, #094 bin/alpha msg, #097 LIMITS dict, #D013 PG SSRF helper, #D005 fuzzy cache) + 4 verificadas pre-fechadas (DM003, D015, D017, #099). +20 testes. |
| `68cdafc` | **DEEP_PERFORMANCE V1.0 stragglers** — 8 fixes + 2 verificadas pre-fechadas. +17 testes. |
| `db75078` | **DEEP_BUGS V1.0 stragglers** — todas as 9 issues #D022–#D030 fechadas. +16 testes. |
| `006ddf3` | **DEEP_RESILIENCE sprint 2** — #053 (compress error structured), #055 (atexit loop detection), #057 (http_request retry transientes), #051/#D012 (apify polling logged), #065 (browser listener cleanup), #067 (SIGTERM handler), #D010 (extract_multiple_pages log) + 3 verificadas pre-fechadas (#066/#D006, #060, #064). +11 testes. |
| `c1c92f1` | **DEEP_RESILIENCE sprint 1** — #024 (Ctrl+C em approval), #014/#D009 (save_session OSError), #052 (delete dead httpx handler), #054 (BrowserSession reset), #056/#061 (sub-agent traceback + scratch cleanup), #058 (já fechado via D020-RES), #059 (extract_page fallback awareness), #D005 (SQLite timeout), #048 (PG fetch timeout). +11 testes. |
| `d1d6ba5` | **DEEP_SECURITY V1.0/V1.1 batch 2** — #027 (dead regex blocklist removida), #028 (safe_env TTL 60s), #022 (sub-agent task_content sem absolute workspace path), #030 (multi-statement SQL detector segue SQL standard `''`). +13 testes em `test_security_v10_v11_batch2.py`. |
| `99637f4` | **DEEP_SECURITY V1.0/V1.1 batch 1** — #D012 + #D015 (sanitize Bearer/DSN via novo `_security_log.py`), #029 (html.unescape), #032 (block userinfo URL), #033 (strip control chars subagent.md), #034 (apify_run_actor → DESTRUCTIVE), #035 (wizard reject newline). +23 testes em `test_security_v10_v11_fixes.py`. |
| `82f1291` | **Ultimos 7 V2.0 stragglers** — DEEP_LOGIC #DL016, DEEP_RESILIENCE #D020/#D021, DEEP_SECURITY #D107/#D110/#D111/#D112 (#D107 verificado como ja fechado via #D014-BUGS). 13 testes em `test_v2_stragglers.py`. |
| `37aa292` | DEEP_MAINTAINABILITY V1.1 #DM002, #DM006, #DM008, #DM009, #DM010, #DM011, #DM013 (7 MEDIOs); #DM001 deferido |
| `cf0958f` | feat(bin): wrapper `alpha-update` (one-command upgrade) |
| `78b566d` | docs(readme): seção Update |
| `471ca22` | DEEP_PERFORMANCE V2.0 #D014, #D017, #D018, #D019, #D021, #D023, #D024 (7 MEDIOs) |
| `fb26eb4` | DEEP_LOGIC V1.1 #DL015, #DL017, #DL018, #DL020 (4 MEDIOs) |
| `156e9c0` | DEEP_SECURITY V2.0 #D103, #D105, #D106 (MEDIOs) + confirma #D104 via #D020-BUGS |
| `c9ca249` | DEEP_SECURITY #D103 — clipboard_read promovido a DESTRUCTIVE |
| `a224291` | DEEP_BUGS V1.1 #D016, #D017, #D018, #D019, #D020, #D021 (6 MEDIOs) |
| `093accd` | DEEP_RESILIENCE V2.0 #D013, #D014, #D015, #D016, #D017, #D018, #D019 (7 MEDIOs) |
| `c4dc55b` | DEEP_PERFORMANCE V2.0 #D013, #D015, #D016 (3 ALTOs) |
| `0424f3c` | DEEP_LOGIC V1.1 #DL012, #DL013, #DL014, #DL019 (4 ALTOs) |
| `26a0c03` | DEEP_SECURITY V2.0 #D101, #D102, #D108, #D109 (4 ALTOs) |
| `85a3e79` | refactor(security): drop `_extract_relevant_context` (#118 + #019 co-fix) |
| `b9a4e7c` | DEEP_RESILIENCE #062 — hard-truncate fallback de compress |
| `95bd15e` | DEEP_PERFORMANCE #068 + #005 — single-pass `_format_result` |
| `a8b7dc9` | AUDIT_V1.1 #102 — cobertura blocklist sub-agent |
| `799dc50` | AUDIT_V1.1 #018 — sub-agent escape via browser/git write |
| `d86d45c` | AUDIT_V1.1 #024 — pin lxml >=6.1.0 (CVE-2026-41066) |
| `d74e31e` | AUDIT_V1.1 #001 + #002 — recovered tool_call ids + cross-thread regex timeout |
| `cffdb6c` | DEEP_BUGS #D013/#D014/#D015 — Ctrl+C tool placeholders + DNS rebind |

(Total verificado: 14 ALTOs + 50+ MEDIOs/BAIXOs fechados.)

---

## ISSUES PENDENTES POR DEEP

| Deep | V2.0 fechadas | V1.0/V1.1 cross-refs ainda abertas | Doc |
|------|--------------|------------------------------------|-----|
| **DEEP_BUGS** | #D013–#D030 (18/18) ✅ | — | [DEEP_BUGS](audits/current/DEEP_BUGS.md) |
| **DEEP_LOGIC** | #DL012–#DL020 (9/9) ✅ | — | [DEEP_LOGIC](audits/current/DEEP_LOGIC.md) |
| **DEEP_MAINTAINABILITY** | 7/8 + #DM001 deferido | refators grandes restantes (#D001–#D004 subprocess/security/timeout consolidation, #028 path validation, #030/#081/#082 splits, #088 display split, #DM012/#DM015/#DM016, #D006/#D007/#D008/#D009 V1.0 maint, #084/#088/#092) (~14 restantes) | [DEEP_MAINTAINABILITY](audits/current/DEEP_MAINTAINABILITY.md) |
| **DEEP_PERFORMANCE** | 13/13 ✅ | #025/#071 (search_files ripgrep, 45min), #D008 (aiohttp shared), #026/#076 (LLM client) (~3 restantes) | [DEEP_PERFORMANCE](audits/current/DEEP_PERFORMANCE.md) |
| **DEEP_RESILIENCE** | #D013–#D021 (9/9) ✅ | #D008 (rate limits design) — restantes pendentes V1.0/V1.1 todos fechados nos 2 sprints | [DEEP_RESILIENCE](audits/current/DEEP_RESILIENCE.md) |
| **DEEP_SECURITY** | 12/12 ✅ | #023 (browser allowlist policy), #025/#026 (deps CVE remaining), #036 (deps upper bound), #D014 (manifest plugins), #115 V1.1 (env perms verified) (~5 restantes) | [DEEP_SECURITY](audits/current/DEEP_SECURITY.md) |

> **Todos os V2.0 fechados.** Backlog restante e exclusivamente cross-refs V1.0/V1.1 herdadas que nunca foram fechadas em sprints anteriores (~70 issues distribuidas, todas BAIXO/MEDIO).

---

## AUDITORIAS

| Tipo | Versao | Data | Issues ativas | Status | Doc |
|------|--------|------|--------------|--------|-----|
| Audit Geral | V1.1 | 2026-05-04 | ~67 ativas (de 117 originais) | ALTOs liquidados; MEDIO/BAIXO em fila | [AUDIT_V1.1](audits/current/AUDIT_V1.1.md) |
| Deep Bugs | V1.1 | 2026-05-04 | 9 (V1.0 cross-refs) | V2.0 zerado; restos sao V1.0 | [DEEP_BUGS](audits/current/DEEP_BUGS.md) |
| Deep Security | V2.0 | 2026-05-04 | 4 V2.0 + ~15 V1.0/V1.1 | ALTOs zerados; MEDIOs em fila | [DEEP_SECURITY](audits/current/DEEP_SECURITY.md) |
| Deep Maintainability | V1.1 | 2026-05-04 | ~25 V1.0/V1.1 | V2.0 zerado (DM001 deferido) | [DEEP_MAINTAINABILITY](audits/current/DEEP_MAINTAINABILITY.md) |
| Deep Resilience | V2.0 | 2026-05-04 | 2 V2.0 + ~20 V1.0/V1.1 | V2.0 quase zerado | [DEEP_RESILIENCE](audits/current/DEEP_RESILIENCE.md) |
| Deep Logic | V1.1 | 2026-05-04 | 1 (#DL016) | Praticamente zerado | [DEEP_LOGIC](audits/current/DEEP_LOGIC.md) |
| Deep Performance | V2.0 | 2026-05-04 | 5 V2.0 + 7 V1.0 | ALTOs zerados; MEDIOs em fila | [DEEP_PERFORMANCE](audits/current/DEEP_PERFORMANCE.md) |
| MVP Plan | — | — | — | Nao realizado | — |

---

## SPRINT ATUAL

**Concluido (2026-05-07 22h):** Quick wins MAINT + PERF — 14 fixes + 4 pre-fechadas. +20 testes.

**Antes (2026-05-07 21h15):** DEEP_PERFORMANCE V1.0 stragglers — 8 fixes + 2 verificadas pre-fechadas.

**Antes (2026-05-07 20h30):** DEEP_BUGS V1.0 stragglers — 9 issues fechadas. DEEP_BUGS 100% zerado.

**Antes (2026-05-07 19h45):** DEEP_RESILIENCE sprint 2 — 7 fixes + 3 verificadas pre-fechadas.

**Antes (2026-05-07 19h):** DEEP_RESILIENCE sprint 1 — 9 issues. +11 testes.

**Antes (2026-05-07 18h):** DEEP_SECURITY batch 2 — #027, #028, #022, #030.

**Antes (2026-05-07 17h):** DEEP_SECURITY batch 1 — 7 issues + 5 quick wins.

**Antes (2026-05-07 16h):** Os ultimos 7 V2.0 stragglers fechados.

---

## PROXIMO SPRINT (sugerido)

Backlog DEEP_SECURITY agora reduzido a 5 itens densos. Outros DEEPs tem mais issues mas de menor valor.

### Opcao A — DEEP_SECURITY restantes (~5, decisoes de design ou esforco grande)
- [ ] #023 — flag browser_require_allowlist conservadora — **decisao de UX/segurança** (30min)
- [ ] #036 — upper bound + lockfile (uv/pip-tools) (1h)
- [ ] #D014 — manifest/hash signature em plugins/ (4h)
- [ ] #025/#026 — pytest 9.0.2 CVE remaining (vide D108 closure parcial)
- [ ] #115 V1.1 — `.env` perms verified post-fix (5min)

### Opcao B — DEEP_RESILIENCE restantes (apenas #D008)
- [ ] #D008 — sub-agentes paralelos exaurem rate limits LLM (~30min, requer design de global LLM rate limiter)

### ~~Opcao C — DEEP_BUGS V1.0 stragglers~~ ✅ Concluido

### Opcao B — DEEP_RESILIENCE V1.0/V1.1 (~20 abertas, maior backlog)
- [ ] #066 / #D006 — messages cresce sem limite (30min)
- [ ] #D005 — SQLite query sem timeout (5min)
- [ ] #048 — PG fetch sem timeout (5min)
- [ ] #053, #055, #056, #059, #060, #061 — swallows + cleanups (~1h)
- [ ] #057, #058, #064, #065, #067, #D010 — HTTP/git/browser cleanups (~1h)

### Opcao C — DEEP_BUGS V1.0 stragglers (~9 itens, baixo risco)
- [ ] #D022–#D030 — error_paths, jitter, SQLite URI, glob workspace, apify KeyError, dead code, descriptions

### Opcao D — DEEP_PERFORMANCE V1.0 cross-refs
- [ ] #025/#071, #027/#072, #D005, #D007, #D008, #D009, #D010, #D012

---

## METRICAS DE PROGRESSO

| Metrica | Valor |
|---------|-------|
| Issues encontradas (V1.0 + V1.1 + DEEPs V2.0) | ~360 acumuladas |
| Issues criticas pendentes | **0** |
| Issues ALTO pendentes | **0** |
| Issues V2.0 abertas (cross-deep) | **0** ✅ |
| Issues V1.0/V1.1 stragglers | ~12 distribuidas (todas BAIXO/MEDIO + refators grandes) |
| Suite de testes | **368/368** verde |
| CI gate | Ativo (Py 3.11 + 3.12) |
| MVP bloqueadores | Nao avaliado (sem MVP_PLAN) |

---

## DECISOES RECENTES

Nenhuma ADR registrada em `docs/decisions/`. Considere documentar:
- DM001 (split de `main.py`/repl) deferido — registrar como ADR com justificativa
- Politica de approval para `delegate_*` e sub-agent destructive blocklist
- Escolha de httpx sobre aiohttp para o cliente principal
- DNS rebinding mitigation (IP pinning + SNI override)

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
| 2026-05-06 | Sprint massivo de fixes: ALTOs do V1.1 + DEEPs V2.0 ALTOs/MEDIOs liquidados |
| 2026-05-07 | DEEP_MAINTAINABILITY V2.0 fechado (7/8); `alpha-update` wrapper |
| 2026-05-07 | **Ultimos 7 V2.0 stragglers fechados** — todos os 6 DEEPs V2.0 zerados (BUGS 9/9, LOGIC 9/9, MAINT 7/8 + DM001 deferido, PERF 13/13, RES 9/9, SEC 12/12). Suite 244 → 257 |
| 2026-05-07 | **DEEP_SECURITY V1.0/V1.1 sprint** — 7 issues fechadas via `_security_log.py` + quick wins. Suite 257 → 280 |
| 2026-05-07 | **DEEP_SECURITY batch 2** — +4 issues (#027 dead code, #028 TTL, #022 path privacy, #030 SQL standard). Suite 280 → 293 |
| 2026-05-07 | **DEEP_RESILIENCE sprint 1** — +9 issues (Ctrl+C approval, save OSError, dead httpx handler, browser singleton reset, sub-agent traceback+scratch cleanup, extract fallback log, SQLite+PG timeouts). Suite 293 → 304 |
| 2026-05-07 | **DEEP_RESILIENCE sprint 2** — +7 issues + 3 verificadas pre-fechadas. Suite 304 → 315. DEEP_RESILIENCE essencialmente esgotado. |
| 2026-05-07 | **DEEP_BUGS V1.0 stragglers** — todas as 9 issues #D022–#D030 fechadas. DEEP_BUGS 100% zerado (18/18). Suite 315 → 331. |
| 2026-05-07 | **DEEP_PERFORMANCE V1.0 stragglers** — 8 fixes + 2 verificadas pre-fechadas. Suite 331 → 348. |
| 2026-05-07 | **Quick wins MAINT+PERF** — 14 fixes (dead imports, init guards, alias removal, dedup, config dicts LOOP_DETECTION/LIMITS, shlex.quote, comment, docstring, bin/alpha msg, PG SSRF helper, fuzzy cache) + 4 pre-fechadas. Suite 348 → 368. |

---

*Atualizado automaticamente — Revisao humana recomendada.*
