# telegram-bot — change log

## 0.7.2 — 2026-10-25

- Published: the capability moves from the private source to `capabilities-public`, with `bin/carry-upstream` beside it. Alexander Korenevski is named as its author (the CLI's header and `README.md`), under the repository's MIT licence; upstream's MIT notice (`LICENSE-UPSTREAM`) and attribution (`UPSTREAM.md`) are unchanged.
- The server route in the guide names the master by its role, not by a machine.

## 0.7.1 — 2026-10-25

Fixes from the independent review of `5aa01d7` (releasable, two should-fix):

- A store that is configured and does not answer is a failure, no longer the project's `service/context.md`: an old export silently replaced the prompt saved in the store. The file is read only where the project keeps no store (`store_not_configured`, or no project id). `service start` and `service reload` keep the daemon's own copy from its last start or reload while the store does not answer, and say so on stderr; with no copy they refuse with the store's error.
- No link hides its address: `[text](url)` is sent as `text (url)`, and is a live link only where its text is its address. Only `http` and `https` links are read; `tg:` (mentions of anyone, proxy and join links), `mailto:` and other schemes stay as written. The service's own notices (stopped by, failures, rate and offline) are escaped and never read as Markdown, so a display name in them formats nothing.
- Minor: emphasis no longer opens inside a word (`2*3*4`, `foo**bar**baz`, `__init__`); code is never put inside bold, italic, strike or a link (the span is closed around it); a cut answer keeps the leading spaces of its next piece, which in a code block are indentation; a fence line no later fence closes stays text and no longer turns the rest of the answer into code; the prompt table is migrated once per process, not on every read.

## 0.7.0 — 2026-10-25

From the live test in a sandbox group:

- Answers arrive formatted, not with literal `**` and backticks: the model's Markdown (bold, italic, strike, inline code, fenced code blocks, links, a heading as a bold line) is sent as Telegram HTML with `parse_mode` HTML, everything else escaped (`service/markup.py`). A long answer is cut as before and each piece converted on its own, so no tag spans two messages; a code block cut in two is closed and opened again. A piece Telegram refuses as badly formed is sent again as plain text, never lost. Progress lines and notices go the same way.
- The context template tells the model Telegram formatting is shown, and that tables and headings are not. A project whose `context` prompt is already in the store keeps its version until it is put again (`prompts put`).
- The guide's checks name a bot added to a group before privacy was disabled: it answers `/command@bot` but not plain @mentions until it is removed and added again.

## 0.6.0 — 2026-10-25

From the first live deployment on a server node:

- The context prompt lives in the project's store, as upstream keeps its prompts: versioned rows of `telegrambot_prompts` (the bot's own table, under the ledger owner `telegrambot`, so a project running upstream's `telegram` beside the bot keeps a `context` for each), read for every turn. `service init` on the store master was writing it as a file in that machine's project folder, which a node running from its own body never saw, so its daemon waited on "no context document". `prompts list|show|versions|put|import|export` read and write it (`put --base` refuses a save over a version it did not read); `service/context.md` is an optional export, read only while the store holds no version, or when the store does not answer and the file is there. `init` seeds a `context.md` the project kept before, else the template.
- `connection add <id> --remote --env secret_env=KEY ...` records an env key that resolves on another node (a server's environment) and not on the machine writing the records, which the contract's add refused with `env_key_unset`. The key is reported `remote`; `doctor` on the node proves it.
- The guide has the server route end to end: the token in Coolify's environment, the connection and grant from the master, `service init`, the settings, `capabilities place telegram-bot --node <id>`, and the checks on the node.

## 0.5.0 — 2026-10-25

Fixes from the independent gate on `eccd682` (ready for a live test, no blockers):

- A folder beside the project whose name holds glob characters (`weird (copy)`, `arch [old]`) is denied too: it was skipped, and a project allow rule let `Read`, `Grep` and `Glob` into it. Measured on Claude Code 2.1.310, a rule's path is a gitignore-style pattern: `*`, `[` and `]` hold escaped with a backslash, `?` and `\` do not and are written as the `?` wildcard, `{}` and `()` are literal; a wildcard rule that would also match the project is left out and logged. `**/.git/config` is denied in the project, and `/proc` everywhere.
- The deployment manifest mounts `{agent_home}/.local/state/telegram-bot` as state, so a container keeps the journal, `/set` overrides, the bot's lock and the log across a restart, as upstream mounts its own state.
- A turn's settings reach claude as a file in the private turns folder, not as one command-line argument: 1600-2600 deny rules passed Linux's 128 KiB limit on one argument. The root's entries are denied first, then the folders from the project up; past the limit the log counts the rules left out correctly.
- `service doctor`, `start` and `reload` check that every restricted profile resolves and fits (the daemon's own check, `daemon.py check-restricted`), as the daemon would park on one that does not.
- Every supervisor entry without a `kind` is warned about, whatever its name: an assistant named like a person (`Atlas`) was missed.
- Minor: a daemon that has not finished its first poll is no longer `stale` (and `service doctor` no longer fails) for the first ~20 s; the missing-token hint names `XDG_CONFIG_HOME` where it is set; `bin/carry-upstream` no longer says "carried" when nothing was applied; restricted turns run with Claude memory off and no git status (user name, recent commits) in their context, laid over the profile and the project's settings; the request's sender name is quoted on one line like every other chosen name; the context copy in the state folder is owner-only; "typing" shows during the debounce; the state folder 0.3.0 kept inside the project is removed at the next start or run (left while a 0.3.0 daemon still runs from it).
- `profiles.py`'s unused `save` verb, which read `CONTROL_DEFAULTS` out of a `daemon.py` that does not keep it, is removed rather than wired: profiles are saved in the store. `profiles.py` is now an adapted file in `UPSTREAM.md`, no longer verbatim.

## 0.4.0 — 2026-10-25

Fixes from the final independent gate on `e66fd83`:

- `service start`, `reload` and `doctor` no longer fail on any real profile: the carried `profiles.py` loads upstream's `jobs.py` by path to reach the store, and it had never been carried. `service/jobs.py` is now carried verbatim from the recorded upstream commit, the suite runs the real `profiles.py check` subprocess (no stub), and `tests/test_upstream.py` and `bin/carry-upstream` name any module a verbatim file loads by path that the bundle lacks.
- Every restricted turn runs on claude (`telegram-claude-restricted`, or a room's `restricted_profile`), whatever harness serves the supervisor: `telegram-codex-restricted` is gone, since codex's read-only sandbox reads the whole disk, the bot's token included. A restricted profile is held to an allowlist (claude; `Read`, `Grep`, `Glob` only; `default`/`dontAsk`; `project` and `local` settings only; strict MCP with no server; hooks off; settings with deny rules alone; no other knob). A settings document naming a profile that does not resolve or a restricted profile that does not fit parks the daemon at start (`profile_refused`), under `service run` as under `service start`.
- Each restricted turn is handed deny rules, which outrank any allow rule or extra directory in the project's own Claude settings: `~/.config`, `~/.ssh`, `~/.local/state`, the bot's state folder, and every folder beside the project and beside each folder above it. Proven with a real claude turn against a project `settings.local.json` that allows the outside folder (`tests/test_gate4.py`, `LiveRestricted`, opt-in): without the rules the turn printed the outside and state files, with them it is refused.
- The bot's state (journal, log, `/set` overrides, authority files) lives under `~/.local/state/telegram-bot/projects/<project>-<hash>/`, outside the project, so no turn reads another chat's unanswered text. A daemon started by 0.3.0 keeps its old state folder until it is restarted.
- The guide and the settings check say an assistant on a personal Telegram account must be listed `"kind": "agent"`; a supervisor entry whose name looks like an agent's is warned about (`"kind": "human"` silences it).
- `bin/carry-upstream --apply` moves the recorded commit only when no adapted file is left to review: otherwise it records `pending` and keeps the old commit, so the diffs show again until `--reviewed`; each upstream origin's diff is printed once, naming the files adapted from it.
- Parity with upstream: a turn waits the room's `debounce` (seeded 5 s, 3 s where unset, `/set debounce`) after the last addressed message, so "@bot look:" and then the details are read together; each conversation row carries its Tallinn time, with a date line per day.
- Minor: `service run` with no credentials, invalid settings, no context or the store down waits and retries every minute with a `waiting` health record instead of exiting into a restart loop; a second SIGTERM during a stop is ignored; a bot may use `/status` and `/help` only, whatever its role; a request stopped by someone else tells its asker; an answer that stops part way is followed by a note; conversations kept in memory are bounded (1000), so `anyone` direct messages do not grow maps per stranger; a replied-to sender's and a forward origin's names are quoted on one line; `.envrc` and `*.env` are denied; whole grants on a group, topic, member or user are warned about like a role's. The suite's runner now includes the harness runner (`callva-harness-runner==0.10.1`).
- Not changed: the bundled `telegram-codex` profile is upstream's verbatim and still names model `sol`, which resolves to no model on a machine signed in with a ChatGPT account; a project names its own `telegram-codex*` profile in the store to choose another. `profiles.py`'s `save` verb reads `CONTROL_DEFAULTS` out of `daemon.py`, where this bot does not keep it; the bot never calls that verb.

## 0.3.0 — 2026-10-25

Fixes from an independent review of `b8e7f0a`, and the design change it recommended:

- Everyone but a supervisor runs restricted. A turn for a `direct_user`, a `group_member` or any bot (whatever role its entry gives it) runs on a restricted profile: the room's or `defaults`' `restricted_profile`, else the bundled twin of the profile in force - `telegram-claude-restricted` (Read, Grep and Glob only, `default` permission mode, so no shell, no web, no writes and no file outside the project, `.env` files denied, the user's settings not loaded) or `telegram-codex-restricted` (read-only sandbox, no network). A twin that cannot be resolved refuses the turn; it never falls back. A policy may set `restricted` itself. The guide says what is hard (enforced by the daemon or the harness) and what is soft (the authority gate and the shim, which a turn with a shell can get round; codex's read-only sandbox, which reads the whole disk).
- The seeded authority gives a group member `telegram-bot` with `"verbs": ["send"]` and `routine`'s read-only verbs: a capability granted without a verb list is granted with every verb, so a member's turn could reveal the token, rewrite `authority`, reload the service or replace the token. The settings check warns when a role holds `telegram-bot` that way. The CLI's contract floor is 0.18, the first whose gate refuses `connections show --reveal` under a verb list (`bin/sync-contract` keeps a raised floor).
- The journal is written under the service lock through a temporary file of its own, owner-only, and a request runs and a conversation moves on even when it cannot be written: overlapping writes had wedged conversations for good.
- The rate limit counts the requests running and waiting beside the answers sent, and a conversation holds at most 20 of them (200 across the bot), whatever the limits.
- Inside a turn the topic is the turn's, as upstream: `--topic` is refused by the shim and filled in, the CLI fills it too, and `--reply-to` may name only the request the turn answers.
- A message is a control command only when its `bot_command` entity at offset 0 names this bot, or names none in a direct chat: `/tmp/build.log is failing @bot` and `/status@other_bot @bot hi` are ordinary messages.
- `/set profile` is a supervisor's alone and changes the profile of unrestricted turns only; `/set` is kept across a restart; `/status` shows request numbers and overrides to a supervisor only.
- Minor: the in-memory sent list is bounded; a turn cancelled by a stop gets the stopped note; a refused second daemon no longer erases the lock holder's pid; an answered request leaves the journal as soon as its answer is out (the double answer after a kill is narrowed to that instant, not closed); the state folder is 0700 and its files 0600, and authority files a killed process left are removed at the next start; the text of a stranger's message an allowed sender replies to stays out of the prompt; the request block closes with its own marker; at most three progress lines a turn; the guide's `settings set` lines name `--scope project`, and the settings docstring no longer claims upstream-only keys are read.

## 0.2.0 — 2026-10-25

Upstream behaviour the rewrite had lost, carried back after an independent audit of `938d671` against upstream `0cc217bf`:

- Per-sender authority (upstream's `_authority_policy_for`): each turn's authority is written to a private file passed as `CAPABILITIES_AUTH_CONTEXT`, which every managed capability's contract gate enforces. Seeded as upstream's template: a supervisor gets every capability, a `direct_user` none, a `group_member` (other bots included) `telegram-bot` in the current chat and `routine`; settings without `authority` are held to that seed. A worker shim (`service/worker-bin/telegram-bot`, upstream's `worker-bin/telegram`) and the CLI's `send` refuse another chat, topic or connection.
- Message text in the prompt is fenced (`_quoted`, `CONVERSATION_END`), so a message cannot forge a heading; `service start` refuses a project without its context document.
- Every addressed message gets its own turn, in order, instead of only the newest of those that arrived during a turn.
- Answers to other bots go out top-level, not as replies; `#noreply` messages are never answered, and answers to `#external` requests carry `#noreply`.
- The settings check is upstream's in strictness: unknown keys, out-of-range numbers, strings for booleans and wrong id signs are refused; profiles are checked before a start and at a reload; a reload does not adopt a changed `connection`, and the CLI keeps addressing the daemon still running on the old one.
- Chat commands, gated by role: `/status`, `/profiles`, `/stop [N]`, `/reload`, `/set`, `/help`; a `/command` is never a worker turn.
- A vanished uv runtime exits the daemon for a rebuild; the lock is per bot across projects (`bot_locked` parks the second).
- Health: `stale` when polling stopped, the previous process's kill reported; "typing" kept up for the whole turn and the worker's `telegram-bot progress` lines relayed, throttled; the replied-to message named in the request and the tail; configured names and upstream's role order (`direct_messages.default_role`, group `role`); failure detail for a supervisor only; long answers split at a space before mid-word; the context template's dropped lines back; pins and joins kept out of the tail; requests a killed process left unanswered taken up at the next start; the context document read for every turn; Tallinn time beside UTC.
- A forwarded message whose origin is not an allowed sender is material, never a request. A send that may have arrived (a read timeout, a 5xx) is not repeated: a rare loss over a duplicate, and "not sure it arrived" in the log and from `send`. `service start`'s log rotates at 10 MB, three kept; the once-per-place refusal log is capped.

## 0.1.2 — 2026-10-24

Fixes from a second independent review (of `e2ff47d`), before the first live bot:

- Bot tokens live outside every project: `telegram-bot token set <id>` writes the token to `~/.config/telegram-bot/credentials.env`, and the connection names its key (`--env secret_env=...`); on a server the process environment holds it. A token in the project's `.env.local`/`.env` is refused, and so is a project `connection add --stdin`, which would write it there.
- Scrubbing the token from a worker's environment guards against accidental leaks only: a worker runs as the same user and could read the credentials file or the daemon's environment if it set out to.
- Under a supervisor the daemon no longer exits into a restart loop: a refused token (401, at start or while running), another bot's token, or a lasting conflict (409) parks it - up, not polling, with the state and the reason in `service status` and `service doctor`. `service reload` retries; `service start` by hand reports it at once.
- A backlog after downtime or sleep gets one note per conversation ("I was offline, ask again") instead of silence, and a message's age is judged by Telegram's clock; `doctor` warns when the machine's clock is more than a minute off.
- `direct_messages.mode` `anyone` is warned about in the log, `service status` and `service doctor`.
- Turns still running on the executor a `max_parallel` reload replaced finish before a stop, and are not told they were stopped.
- Rate-limit and offline notices are sent by their own thread, off the polling loop.

## 0.1.1 — 2026-10-24

Fixes from an independent review of 0.1.0 (`407cf6b`):

- `service stop` stops what `service start` launched: the pid is uv's child, so it is signalled directly, and `--force` kills its process group.
- No token key reaches a worker: the daemon gets the token under one key, and workers start without any `TELEGRAM_BOT_*` key or the key `secret_env` names.
- Only allowed people and bots shape a turn: others never enter the conversation tail and never reset the bot-to-bot cap.
- A refused token (401) ends the daemon at once; another poller of the same token (409) ends it after five tries with growing pauses.
- Nothing ends silently: a crashed turn, a stop before a request was answered, and a rate-limited person each get a short note.
- The rate limit counts answers, not pieces, and a rate-limited bot turn no longer spends a dialogue turn.
- Messages older than five minutes on arrival (a backlog after downtime) are context, not turns.
- Gate: no key collision between basic groups and supergroups; an empty `topics` map admits no topic; unknown bots get no direct-message turns in `anyone` mode.
- Long answers split at paragraphs first and are measured in UTF-16 units, as Telegram counts.
- Folded turns run in a loop, not by recursion.
- `bin/carry-upstream`: REF may be a branch, tag or commit; verbatim files are carried byte for byte with upstream's hash; the suite runs under uv; a failing suite exits 1.
- The guide reads the token with `read -s` instead of typing it; `tests/ablate.py` runs the clean suite first and covers the CLI.

## 0.1.0 — 2026-10-24

First version. One Telegram bot per project over the Bot API (long polling), derived from upstream `telegram` at `0cc217b` (UPSTREAM.md):

- Connections, one per bot: `username`, `expected_bot_id`, token by `secret_env`; `doctor` proves `getMe` is that bot.
- Project-local service (`init`, `doctor`, `run`, `start`, `reload`, `stop`, `status`, `logs`) with upstream-shaped settings in the project's records.
- Gate: answers only when addressed (@mention, reply, `/command@bot`) in an allowed chat or forum topic by an allowed person or bot; the answer lands in the same topic.
- Worker: the project's agent through upstream's harness runner and `telegram-*` profiles, with the conversation tail the bot received.
- Loop guard: bot-started turns capped per topic until a person speaks, dedup, rate limits.
- `send` for a single message; guide `enrol-a-bot`.
