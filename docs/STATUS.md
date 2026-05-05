# STATUS DO PROJETO — Alpha Code
> Ultima atualizacao: 2026-05-04 (DEEP_BUGS V1.1 anexado)

## Estado Geral: REPROVADO PARA PRODUCAO

Audit V1.1 concluido. **1 CRITICO** bloqueador (#101 — 6 testes red commitados na master + sem CI gate). **10 ALTOs** em fila apos o CRITICO. Pendencias do V1.0 confirmadas como ainda abertas + 5 CVEs em deps via pip-audit.

## Issues Criticas Abertas

| # | Titulo | Status | Categoria |
|---|--------|--------|-----------|
| #101 | Suite com 6 testes red commitados + sem CI gate | **ABERTO** (bloqueador) | Testes |

## Issues ALTOs Abertas (Sprint 2)

| # | Titulo | Categoria |
|---|--------|-----------|
| #001 | `_recover_tool_call_from_content` IDs nao-unicos | Bugs |
| #002 | `signal.SIGALRM` quebra fora da main thread | Bugs |
| #018 | Sub-agents auto-aprovam browser_* + git write | Seguranca |
| #019 | Tool results no prompt do sub-agent (prompt injection) | Seguranca |
| #021 | Wizard `.env` com perms 0o644 | Seguranca |
| #023 | Browser allowlist vazia = fail-open | Seguranca |
| #024 | `lxml` 6.0.2 vulneravel CVE-2026-41066 | Seguranca |
| #062 | `compress_context` sem fallback de truncacao | Resiliencia |
| #068 | `_format_result` 2x json.dumps (D004-PERF pendente) | Performance |
| #102 | Cobertura inadequada de sub-agent blocklist | Testes |
| #115 | `.env` atual com perms 664 (chmod 600 imediato) | Seguranca |
| #D013 | Ctrl+C durante run_agent corrompe messages (HTTP 400 nas proximas requests) | Bugs |
| #D014 | http_request quebrado para HTTPS publico (cert mismatch apos substituicao por IP) | Bugs |
| #D015 | URL replace falha em hostname uppercase ou IPv6 (DNS rebinding window) | Bugs |

## Resumo da Auditoria V1.1

| Severidade | Quantidade | Status |
|-----------|------------|--------|
| CRITICO | 1 | 0 corrigidos |
| ALTO | 10 | 0 corrigidos |
| MEDIO | 44 | 0 corrigidos |
| BAIXO | 62 | 0 corrigidos |
| **TOTAL** | **117** | **0 corrigidos** |

## Pendencias do V1.0 confirmadas

Issues do AUDIT V1.0 que continuavam abertas e foram revisitadas:
- ~~D004-PERF~~ → reaberto como **#068 (ALTO)** — `_format_result` 2x json.dumps
- ~~D006-SEC~~ → reaberto como **#021/#115 (ALTO)** — perms 644 do `.env` (e session files)
- ~~D006-RES~~ → reaberto como **#066 (MEDIO)** — messages list sem limite
- ~~D002-RES~~ → reaberto como **#002 (ALTO)** — SIGALRM nao funciona em sub-agents
- ~~D007-SEC~~ → reaberto como **#036 (BAIXO)** — deps sem upper bound
- ~~D005-RES~~ ainda nao ataqcado em V1.1 — SQLite query sem timeout
- ~~D008-RES~~ ainda nao atacado em V1.1 — sub-agents sem rate limiting

## Auditorias

| Versao | Data | Total | Criticos | Status | Relatorio |
|--------|------|-------|----------|--------|-----------|
| V1.0 | 2026-04-03 | 33 | 1 | Base | [archive/AUDIT_V1.0.md](audits/archive/AUDIT_V1.0.md) |
| DEEP V1.0 (6 categorias) | 2026-04-04 | 101 | 4 | 4 CRIT + 8 ALTO corrigidos | [archive/DEEP_*.md](audits/archive/) |
| **V1.1** | **2026-05-04** | **117** | **1** | **REPROVADO PARA PRODUCAO** | [current/AUDIT_V1.1.md](audits/current/AUDIT_V1.1.md) |
| **DEEP LOGIC V1.1** | **2026-05-04** | **27 ativos** (9 novos + 18 pendentes) | **0** | **2 resolvidos vs V1.0** — 4 ALTO/4 MEDIO/1 BAIXO novos | [current/DEEP_LOGIC.md](audits/current/DEEP_LOGIC.md) |
| **DEEP PERFORMANCE V2.0** | **2026-05-04** | **25 ativas** (13 novas + 12 pendentes V1.0) | **0** | **3 resolvidos vs V1.0** (D001/D002/D003) — 7 ALTO/13 MEDIO/4 BAIXO ativas | [current/DEEP_PERFORMANCE.md](audits/current/DEEP_PERFORMANCE.md) |
| **DEEP RESILIENCE V2.0** | **2026-05-04** | **35 ativas** (9 novas + 4 parciais + 22 pendentes + 1 regressao) | **0** | **2 resolvidos vs V1.0** (D001/D011) — 0 ALTO/7 MEDIO/2 BAIXO novos. Inclui regressao silenciosa de #D003 | [current/DEEP_RESILIENCE.md](audits/current/DEEP_RESILIENCE.md) |
| **DEEP MAINTAINABILITY V1.1** | **2026-05-04** | **48 ativas** (16 novas + 4 parciais + 28 pendentes V1.0/V1.1) | **0** | **1 resolvido vs V1.0** (D014) — 0 ALTO/14 MEDIO + 14 BAIXO novos. Categoria regredindo: arquivos > 500L surgindo, deps ocultas, mismatches de categoria | [current/DEEP_MAINTAINABILITY.md](audits/current/DEEP_MAINTAINABILITY.md) |
| **DEEP SECURITY V2.0** | **2026-05-04** | **34 ativas** (12 novas + 4 parciais + 18 pendentes V1.0/V1.1) | **0** | **2 resolvidos** (#017 V1.1 path-after-mkdir; #D001 V1.0 sub-agent destructive) — 4 ALTO/5 MEDIO/3 BAIXO novos. Vetores principais: RCE via `pickle`/`marshal` em execute_python (#D101), sandbox bypass via `python -c`/`node -e` (#D102) | [current/DEEP_SECURITY.md](audits/current/DEEP_SECURITY.md) |
| **DEEP BUGS V1.1** | **2026-05-04** | **42 ativas** (18 novos + 17 V1.1 re-validados + 7 pendentes V1.0) | **0** | **1 resolvido** (#D012 asyncio.get_event_loop em network_tools) + 5 ja resolvidos antes (#D001/D002/D003/D005/D007). 3 ALTO/6 MEDIO/9 BAIXO novos. Bugs criticos: #D013 (Ctrl+C corrompe messages), #D014 (HTTPS quebrado por cert mismatch via DNS rebinding fix), #D015 (URL replace falha em uppercase/IPv6) | [current/DEEP_BUGS.md](audits/current/DEEP_BUGS.md) |

## Sprints

### Sprint 1 — CRITICO (AGORA — bloqueador)
- [ ] #101 — Fix 6 testes red + adicionar GitHub Actions CI

### Sprint 2 — ALTOs (esta semana)
- [ ] #115 — `chmod 600 .env` (quick fix imediato)
- [ ] #001, #002, #018, #019, #021, #023, #024, #062, #068, #102
- [ ] #D013, #D014, #D015 (DEEP_BUGS V1.1) — ver `docs/audits/current/DEEP_BUGS.md`

### Sprint 3 — MEDIOs (este mes)
44 issues — destaque para #118 (codigo morto descobrindo que feature anunciada nao funciona), #117 (system.md desencontrado), #027 (AST blocklist), #066 (limite de messages).

### Backlog — BAIXOs
62 issues. Quick wins: #092 (git add do test_delegate_workspace.py), #094/#114 (corrigir mensagem em bin/alpha).

## Deep Audits — Status

| Categoria | Versao | Novos | Resolvidos | Pendentes | Total ativo |
|-----------|--------|-------|-----------|-----------|-------------|
| logic | V1.1 (2026-05-04) | 9 | 2 (de V1.0) | 18 | 27 |
| performance | V2.0 (2026-05-04) | 13 | 3 (de V1.0: D001 client, D002 regex, D003 env cache) | 12 | 25 |
| resilience | V2.0 (2026-05-04) | 9 | 2 (de V1.0: D001/D011) | 22 + 4 parciais + 1 regressao | 35 |
| maintainability | V1.1 (2026-05-04) | 16 | 1 (de V1.0: D014) | 28 + 4 parciais | 48 |
| security | V2.0 (2026-05-04) | 12 | 2 (#017 V1.1, #D001 V1.0) | 18 + 4 parciais | 34 |
| bugs | V1.1 (2026-05-04) | 18 | 1 (#D012 asyncio em network_tools) | 17 V1.1 + 7 V1.0 | 42 |

Todos os 6 deep audits foram re-rodados em V1.1.

## Pontos Cegos do V1.1

Audits separados recomendados para:
- `prompts/subagent.md` — system prompt nao auditado
- `agents/{default,researcher,test-researcher}/agent.yaml` — perfis nao auditados
- ~80 `skills/*/SKILL.md` — cada skill carregada como instrucao para LLM
- Plugins externos (`plugins/*.py`) — carregamento arbitrario

## Links

- Relatorio V1.1: [docs/audits/current/AUDIT_V1.1.md](audits/current/AUDIT_V1.1.md)
- Audits anteriores: [docs/audits/archive/](audits/archive/)
