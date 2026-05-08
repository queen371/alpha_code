# Skills audit

Total: **53** skills (complete: 22, inactive: 30, skeleton: 0, broken: 1).

- **complete**: ready to use as-is.
- **inactive**: parses fine but the external CLI it wraps (`requires_bins`) isn't installed on this machine. Install the binary and the skill becomes complete.
- **skeleton**: parses but body is a stub (or no description) — needs author content.
- **broken**: parse error or missing frontmatter — author fix required.

| Status | Skill | Body chars | Requires bins | Notes |
|---|---|---:|---|---|
| broken | `canvas` | — | — | parse error: Missing YAML frontmatter in /home/freire/Documents/MeusProjetos/Alpha_Code/skills/canvas/SKILL.md |
| inactive | `1password` | 1938 | op | missing bins on PATH: op |
| inactive | `apple-notes` | 1390 | memo | missing bins on PATH: memo |
| inactive | `apple-reminders` | 2459 | remindctl | missing bins on PATH: remindctl |
| inactive | `bear-notes` | 2092 | grizzly | missing bins on PATH: grizzly |
| inactive | `blogwatcher` | 832 | blogwatcher | missing bins on PATH: blogwatcher |
| inactive | `blucli` | 503 | blu | missing bins on PATH: blu |
| inactive | `camsnap` | 589 | camsnap | missing bins on PATH: camsnap |
| inactive | `clawhub` | 957 | clawhub | missing bins on PATH: clawhub |
| inactive | `eightctl` | 550 | eightctl | missing bins on PATH: eightctl |
| inactive | `gemini` | 434 | gemini | missing bins on PATH: gemini |
| inactive | `gifgrep` | 1408 | gifgrep | missing bins on PATH: gifgrep |
| inactive | `gog` | 4062 | gog | missing bins on PATH: gog |
| inactive | `goplaces` | 812 | goplaces | missing bins on PATH: goplaces |
| inactive | `himalaya` | 3935 | himalaya | missing bins on PATH: himalaya |
| inactive | `imsg` | 2403 | imsg | missing bins on PATH: imsg |
| inactive | `mcporter` | 1065 | mcporter | missing bins on PATH: mcporter |
| inactive | `model-usage` | 1495 | codexbar | missing bins on PATH: codexbar |
| inactive | `nano-pdf` | 430 | nano-pdf | missing bins on PATH: nano-pdf |
| inactive | `obsidian` | 1952 | obsidian-cli | missing bins on PATH: obsidian-cli |
| inactive | `openhue` | 1927 | openhue | missing bins on PATH: openhue |
| inactive | `oracle` | 4442 | oracle | missing bins on PATH: oracle |
| inactive | `ordercli` | 1606 | ordercli | missing bins on PATH: ordercli |
| inactive | `peekaboo` | 5433 | peekaboo | missing bins on PATH: peekaboo |
| inactive | `sag` | 1749 | sag | missing bins on PATH: sag |
| inactive | `songsee` | 758 | songsee | missing bins on PATH: songsee |
| inactive | `sonoscli` | 1927 | sonos | missing bins on PATH: sonos |
| inactive | `summarize` | 1616 | summarize | missing bins on PATH: summarize |
| inactive | `things-mac` | 2752 | things | missing bins on PATH: things |
| inactive | `wacli` | 1595 | wacli | missing bins on PATH: wacli |
| inactive | `xurl` | 13834 | xurl | missing bins on PATH: xurl |
| complete | `bluebubbles` | 2770 | — | Use when you need to send or manage iMessages via BlueBubbles (recommended iMessage integration). Calls go through the g |
| complete | `coding-agent` | 10456 | — | Delegate coding tasks to Codex, Claude Code, or Pi agents via background process. Use when: (1) building/creating new fe |
| complete | `discord` | 3226 | — | Discord ops via the message tool (channel=discord). |
| complete | `gh-issues` | 34062 | curl, git, gh | Fetch GitHub issues, spawn sub-agents to implement fixes and open PRs, then monitor and address PR review comments. Usag |
| complete | `github` | 3102 | gh | GitHub operations via `gh` CLI: issues, PRs, CI runs, code review, API queries. Use when: (1) checking PR status or CI,  |
| complete | `healthcheck` | 10143 | — | Host security hardening and risk-tolerance configuration for OpenClaw deployments. Use when a user asks for security aud |
| complete | `node-connect` | 4239 | — | Diagnose OpenClaw node connection and pairing failures for Android, iOS, and macOS companion apps. Use when QR/setup cod |
| complete | `notion` | 5094 | — | Notion API for creating and managing pages, databases, and blocks. |
| complete | `openai-whisper` | 380 | whisper | Local speech-to-text with the Whisper CLI (no API key). |
| complete | `openai-whisper-api` | 1060 | curl | Transcribe audio via OpenAI Audio Transcriptions API (Whisper). |
| complete | `session-logs` | 3697 | jq, rg | Search and analyze your own session logs (older/parent conversations) using jq. |
| complete | `sherpa-onnx-tts` | 1541 | — | Local text-to-speech via sherpa-onnx (offline, no cloud) |
| complete | `skill-creator` | 18152 | — | Create, edit, improve, or audit AgentSkills. Use when creating a new skill from scratch or when asked to improve, review |
| complete | `slack` | 2217 | — | Use when you need to control Slack from OpenClaw via the slack tool, including reacting to messages or pinning/unpinning |
| complete | `spotify-player` | 884 | — | Terminal Spotify playback/search via spogo (preferred) or spotify_player. |
| complete | `taskflow` | 4546 | — | Use when work should span one or more detached tasks but still behave like one job with a single owner context. TaskFlow |
| complete | `taskflow-inbox-triage` | 2367 | — | Example TaskFlow authoring pattern for inbox triage. Use when messages need different treatment based on intent, with so |
| complete | `tmux` | 3230 | tmux | Remote-control tmux sessions for interactive CLIs by sending keystrokes and scraping pane output. |
| complete | `trello` | 2390 | jq | Manage Trello boards, lists, and cards via the Trello REST API. |
| complete | `video-frames` | 462 | ffmpeg | Extract frames or short clips from videos using ffmpeg. |
| complete | `voice-call` | 881 | — | Start voice calls via the OpenClaw voice-call plugin. |
| complete | `weather` | 1856 | curl | Get current weather and forecasts via wttr.in or Open-Meteo. Use when: user asks about weather, temperature, or forecast |
