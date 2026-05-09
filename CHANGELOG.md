# Changelog

## [1.1.0](https://github.com/freire19/Alpha_Code/compare/alpha-code-v1.0.0...alpha-code-v1.1.0) (2026-05-08)


### Features

* /skills REPL command + slash autocomplete + skills audit tool ([0bfc40e](https://github.com/freire19/Alpha_Code/commit/0bfc40e6bd158e524ca36749e8b853dc284fb2a6))
* add browser automation tools and consolidate WIP ([f343706](https://github.com/freire19/Alpha_Code/commit/f343706f234f8dda1673b89c65ec9ff9bd08c275))
* add skills bundle, onboarding wizard, named agents, workspace isolation ([aabd7d8](https://github.com/freire19/Alpha_Code/commit/aabd7d8308300f9b949628147ff5888e5ad6a20a))
* ALPHA.md auto-load + /init + pre-commit secret guard ([82b2885](https://github.com/freire19/Alpha_Code/commit/82b2885820bf2bcf696c1d6b53feef898bc6e9f5))
* **attachments:** per-provider vision_format to fix DeepSeek image input ([79548f5](https://github.com/freire19/Alpha_Code/commit/79548f5d9396da2441b515c792cde8983064c20a))
* **bin:** add alpha-update wrapper for one-command upgrades ([cf0958f](https://github.com/freire19/Alpha_Code/commit/cf0958fea95246238e2dd1c4607ebd6193decdfb))
* default to DeepSeek V4 with 1M context detection ([6b2a0c9](https://github.com/freire19/Alpha_Code/commit/6b2a0c953f26e1da5dc5f77fe19b2332d1b28c33))
* gemini OCR fallback, inline edit diffs, async SSRF guard ([e9a35fb](https://github.com/freire19/Alpha_Code/commit/e9a35fbab3bad639c68d475f7a32e0dc4148c075))
* image attachments in REPL + adaptive context compression ([8a8e449](https://github.com/freire19/Alpha_Code/commit/8a8e449b25bb62ce9a5f77f39bdaf713a72f9bf9))
* MCP, hooks, plan/todo, Anthropic provider, lean profile, CI ([3702651](https://github.com/freire19/Alpha_Code/commit/3702651c44d92a7073795bcb05cc3a09fb48a755))
* **prompt:** harden ALPHA identity against misidentification ([37afa08](https://github.com/freire19/Alpha_Code/commit/37afa0807b330f8f36ab7c75424606c9dc4243af))
* **subagent:** configurable approval policy via FEATURES + env ([42fb56a](https://github.com/freire19/Alpha_Code/commit/42fb56a2b7c71a05482b29672439405ed2278db8))
* **ui:** context-window feedback + cleaner tool-call rendering ([550eecb](https://github.com/freire19/Alpha_Code/commit/550eecb28da1d5a35586ea7243f20ea6beb3598b))
* **ui:** dynamic phase labels in thinking indicator ([3dfffaf](https://github.com/freire19/Alpha_Code/commit/3dfffaf880256dfd0926adf6c29dd08f4cf59fea))


### Bug Fixes

* 4 cross-loop / cross-context bugs + ReDoS heuristic + cleanup ([8a37f1e](https://github.com/freire19/Alpha_Code/commit/8a37f1eb25f778c19333ff37615b0d8097414ee1))
* AUDIT_V1.2 batch — concurrency, context, sandbox guards ([0eb9699](https://github.com/freire19/Alpha_Code/commit/0eb96997ef257a64418b9a7d2994159613c0caa2))
* backfill tool messages on interrupt + harden DNS rebinding mitigation ([cffdb6c](https://github.com/freire19/Alpha_Code/commit/cffdb6ce5e10593d634e1a7cadaa4524db4a191f))
* **bugs:** close 4 MEDIOs from DEEP_BUGS V1.1 ([a224291](https://github.com/freire19/Alpha_Code/commit/a2242912054cc807d4683e3eb860194b18c1c3bd))
* **bugs:** close all 9 DEEP_BUGS V1.0 stragglers (#D022–#D030) ([db75078](https://github.com/freire19/Alpha_Code/commit/db75078bf35504d0be784288be757cb7c48e9a67))
* close last 7 V2.0 stragglers across security, resilience, logic ([82f1291](https://github.com/freire19/Alpha_Code/commit/82f129165e0e83a5beafcb128fdd8258ada8359b))
* **config:** mark deepseek provider as supports_vision=True ([684373f](https://github.com/freire19/Alpha_Code/commit/684373fcc06770f0e2a7331c8ce370d76d88dd79))
* **config:** revert deepseek supports_vision to False ([486b1ff](https://github.com/freire19/Alpha_Code/commit/486b1ffffdd6afa76892dda04e70c5536bd657ff))
* **deps:** pin lxml&gt;=6.1.0 to close CVE-2026-41066 ([d86d45c](https://github.com/freire19/Alpha_Code/commit/d86d45cc18365735e08dbc9775847c9f1b750a02))
* DL batch — workspace helper consolidation, error-shape uniformity ([288526c](https://github.com/freire19/Alpha_Code/commit/288526c0674537d81f1b6fe89ad8a0eb6fedf5a9))
* **history:** block path traversal via crafted session_id ([56d65ab](https://github.com/freire19/Alpha_Code/commit/56d65abaabbd46d7ce6314910ea4a80127bc3376))
* **llm:** preserve reasoning_content for DeepSeek thinking mode ([ded9530](https://github.com/freire19/Alpha_Code/commit/ded9530befbf2abe51b281412c53bb38445ca117))
* **logic:** close 4 ALTOs from DEEP_LOGIC V1.1 ([0424f3c](https://github.com/freire19/Alpha_Code/commit/0424f3cb262637ed4dea72dcdd087f910b4a24f0))
* **logic:** close 4 MEDIOs from DEEP_LOGIC V1.1 ([fb26eb4](https://github.com/freire19/Alpha_Code/commit/fb26eb40eb3745cb5fce66c71396956c7b69fedc))
* **resilience:** close 7 MEDIOs from DEEP_RESILIENCE V2.0 ([093accd](https://github.com/freire19/Alpha_Code/commit/093accdb150622f15d0f5ddd14f681a10c41806a))
* **resilience:** close 7 more DEEP_RESILIENCE V1.0/V1.1 stragglers ([006ddf3](https://github.com/freire19/Alpha_Code/commit/006ddf3854d88ac0d8a50165712cf0206a9c2d5c))
* **resilience:** close 9 DEEP_RESILIENCE V1.0/V1.1 stragglers ([c1c92f1](https://github.com/freire19/Alpha_Code/commit/c1c92f1facf29ba32c6412ce927dbdeed4087802))
* **resilience:** hard-truncate fallback when compression LLM fails ([b9a4e7c](https://github.com/freire19/Alpha_Code/commit/b9a4e7cae89a3ff5af8152d8b6d1f9597e7c7d62))
* **security:** close 4 ALTOs from DEEP_SECURITY V2.0 ([26a0c03](https://github.com/freire19/Alpha_Code/commit/26a0c031441f52570cc8c0cd6e656e64247e0909))
* **security:** close 4 MEDIOs from DEEP_SECURITY V2.0 ([156e9c0](https://github.com/freire19/Alpha_Code/commit/156e9c05211cb92f5ecb6048acb6c02b4bd97bf4))
* **security:** close 4 more DEEP_SECURITY V1.0/V1.1 stragglers ([d1d6ba5](https://github.com/freire19/Alpha_Code/commit/d1d6ba5522eb772807190aea818995588a505a48))
* **security:** close 7 DEEP_SECURITY V1.0/V1.1 stragglers ([99637f4](https://github.com/freire19/Alpha_Code/commit/99637f48cdc58d815e82a6b920c2df2e76beebd1))
* **security:** close CRITICO [#009](https://github.com/freire19/Alpha_Code/issues/009) — execute_python AST bypass via low-level OS modules ([f142d23](https://github.com/freire19/Alpha_Code/commit/f142d2316357b3aad4906bb76091120e563c1aa5))
* **security:** close sub-agent escape via browser tools and git write ([799dc50](https://github.com/freire19/Alpha_Code/commit/799dc50e3e18cdf28056b0287f49a38fc9bcf688))
* **security:** hardening pass + provider vision guard ([906ec1c](https://github.com/freire19/Alpha_Code/commit/906ec1c209008631693f6afe61707b2f87d95e34))
* **security:** require approval for clipboard_read (DEEP_SECURITY #D103) ([c9ca249](https://github.com/freire19/Alpha_Code/commit/c9ca2494c9a300e23807822bbf223fc1bd4f0bca))
* simplify batch — retry/atomic-write/IO hardening + spinner polish ([6c2fece](https://github.com/freire19/Alpha_Code/commit/6c2fece3b82c1e911993c00ef90204ed3de0f69e))
* stable recovered tool_call ids + cross-thread regex timeout ([d74e31e](https://github.com/freire19/Alpha_Code/commit/d74e31e212ddef6a25dc2759a446351fb42bc56f))


### Performance

* **agent:** strip common prefix in loop-detection similarity check ([9683ae1](https://github.com/freire19/Alpha_Code/commit/9683ae18d48daf762ab7637d53ee22c4576e1b05))
* close 3 ALTOs from DEEP_PERFORMANCE V2.0 ([c4dc55b](https://github.com/freire19/Alpha_Code/commit/c4dc55b1f75574213315b885293a31728630c4b2))
* close 7 MEDIOs from DEEP_PERFORMANCE V2.0 ([471ca22](https://github.com/freire19/Alpha_Code/commit/471ca221034630ccbef15f23d183f54b3b4b0550))
* **search:** migrate search_files to ripgrep with python fallback ([07e954c](https://github.com/freire19/Alpha_Code/commit/07e954cd78310d05e86cdd3cdd6a58c0e93f39c3))
* single-pass _format_result with per-field preview clipping ([95bd15e](https://github.com/freire19/Alpha_Code/commit/95bd15e70886b6b3b5139324606a810b80c8cbb6))


### Refactoring

* **cli:** camada 3 — security + structure cleanup ([27f4dc5](https://github.com/freire19/Alpha_Code/commit/27f4dc5c343c61ab779d9f3ee915f1229b0d5f2c))
* **cli:** dispatch table for slash commands + REPL integration tests ([00fe291](https://github.com/freire19/Alpha_Code/commit/00fe2916cd7876ca659fc9d459226811d622af94))
* close 7 of 8 MEDIOs from DEEP_MAINTAINABILITY V1.1 ([37aa292](https://github.com/freire19/Alpha_Code/commit/37aa2924283719d9977cc37707c42ec75af6f969))
* consolidate TOOL_TIMEOUTS + fix pipeline FD leak ([33c1793](https://github.com/freire19/Alpha_Code/commit/33c1793bf046b82da1d238e6e5aef6649eaca8c2))
* **display:** tools list reads category from registry ([f706de6](https://github.com/freire19/Alpha_Code/commit/f706de6d309c7d2e893d4e651264b6c1bbd01d2c))
* **security:** drop _extract_relevant_context (dead code + injection vector) ([85a3e79](https://github.com/freire19/Alpha_Code/commit/85a3e791a1496e2240ca2b0e033f31def58e6e86))
* split composite/delegate tools into focused helpers + rate limiter ([6500cf8](https://github.com/freire19/Alpha_Code/commit/6500cf88348b4b2b11f1e355dc063496bc924c1a))


### Documentation

* add MIT license and minimal README ([b1f65e4](https://github.com/freire19/Alpha_Code/commit/b1f65e44a025274204a91c985ad1f2742228e38e))
* correct ALTO pendentes counter (2 -&gt; 4) ([eca5fbb](https://github.com/freire19/Alpha_Code/commit/eca5fbb7aaac1e30fa687e4ff0d2ee20865ecc3d))
* **readme:** add Update section after Install ([78b566d](https://github.com/freire19/Alpha_Code/commit/78b566d1af28098e15e2f818e80b6f760830f53e))
* refresh STATUS to reflect verified state (0 critical, 7 ALTO open) ([7708deb](https://github.com/freire19/Alpha_Code/commit/7708debb43786fb132f4fb89d8945e6897af2469))
* **user-guide:** add Skills section explaining locations and authoring ([7e0a9ee](https://github.com/freire19/Alpha_Code/commit/7e0a9ee7c033732d8dd20ea90653c1376cb397af))
