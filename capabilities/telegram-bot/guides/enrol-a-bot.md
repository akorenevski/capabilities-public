# Enrolling a bot

How one project gets its own Telegram bot: create it in BotFather, let it hear people and other bots, add it to the chat, give the project the connection, and start the service. One bot answers for one project; a chat may hold several bots, people and other agents.

## 1. Create the bot (BotFather)

1. In Telegram, open @BotFather and send `/newbot`. Give it a display name and a username ending in `bot`. BotFather answers with the token, `<bot id>:<secret>`. The part before the colon is the bot id.
2. `/setjoingroups` → the bot → Enable, so it can be added to groups.
3. `/setprivacy` → the bot → **Disable**. In privacy mode a bot only gets commands meant for it (`/command@bot`) and replies to its own messages, not plain @mentions, and it never sees the rest of the room, so it would have no context. Privacy off means it receives every message; the service still answers only what is addressed to it. A bot that is already in a group must be removed and added again for this to take effect. Making the bot a group admin has the same effect.
4. Turn on **Bot-to-Bot Communication Mode** for the bot in @BotFather (the bot's settings). Without it the bot never hears other bots. With it on and privacy off, it receives every message other bots post in its groups; it answers a bot only when that bot mentions it, replies to it is not enough (see the loop guard below).

Keep the token out of chats, commits, command lines and the project's folder; in step 3 it goes through stdin to a file outside every project.

## 2. Add it to the chat

Add the bot to the group as a member. In a forum the bot joins the whole group; which topics it answers in is decided by the settings, not by Telegram.

## 3. Give the project the connection

Run these in the project's own folder. Enabling the capability for a project is the owner's decision (`capabilities enable telegram-bot --project`).

```sh
read -rs BOT_TOKEN    # paste the token, press Enter; nothing is shown or kept in history
printf '%s' "$BOT_TOKEN" | telegram-bot token set <id>
unset BOT_TOKEN
telegram-bot connection add <id> --project --default \
  --field username=<bot_username> --field expected_bot_id=<bot id> \
  --env secret_env=TELEGRAM_BOT_<ID>_TOKEN
telegram-bot doctor
```

The token is never typed into the command itself: the shell keeps `"$BOT_TOKEN"` in its history, not the value, and `printf` is a shell builtin, so the value is on no process's command line either.

`<id>` is a short name for the connection (the project's name works); `<ID>` is the same name upper-cased, with every other character an underscore (`token set` prints the exact key and the `connection add` line).

**Where the token lives.** `token set` writes it to `~/.config/telegram-bot/credentials.env` (mode 0600), outside every project, and the connection only names its key. On a server the same key comes from the process environment instead. A token never sits in the project's `.env.local` or `.env`: the bot's agent works inside the project and can read those files, so `doctor` and the service refuse a token found there, and a project `connection add --stdin`, which would write it there, is refused too.

The daemon also starts each agent turn without the token in its environment. That guards against accidental leaks only (a tool printing its environment, a log); it does not stop an agent turn that sets out to find the token, because the turn runs as the same user and could read the credentials file or the daemon's own environment. Keeping the token outside the project is what keeps it out of the project's own files; nothing on the machine hides it from that user.

`doctor` asks Telegram `getMe` and refuses when the token belongs to another bot; it also reports `privacy_mode` (want `false`) and warns when this machine's clock is more than a minute off Telegram's.

The bot answers by sending, which is a write, so the connection needs a grant: `capabilities set telegram-bot grant <id> '{"enabled": true, "allow_write": true}'`, the owner's decision like enabling.

## 4. Settings

```sh
telegram-bot service init --connection <id>
telegram-bot service start
```

`init` seeds the settings in the project's records and the context prompt in the project's store (a `capabilities/telegram-bot/service/context.md` the project kept before is what it seeds, else the template); nothing is allowed yet. To learn the ids, mention the bot once in the topic it should serve, then read `telegram-bot service logs`: the first message from a place not yet allowed is logged once with its key (`<chat id>#topic:<topic id>`) and the sender's id, `[bot]` marking a bot.

Then allow the place, the people and the bots, and reload:

```sh
telegram-bot settings set --scope project allowed_groups '{"<chat id>": {"topics": {"<topic id>": {"name": "<topic>", "brief": "<what this bot does here>"}}, "members": {"<other bot id>": {"kind": "agent"}}}}'
telegram-bot settings set --scope project allowed_users '{"<person id>": {"name": "<name>", "role": "supervisor"}}'
telegram-bot settings set --scope project defaults.profile telegram-claude
telegram-bot service reload
telegram-bot service doctor
```

The settings live at two scopes (project and global), so `settings set` names the scope with `--scope project`; without it a key the CLI does not declare (`allowed_groups`, `allowed_users`) stops with `scope_required`.

- A group without `topics` admits the whole chat; with `topics` only the topics listed (`1` is General), and none for an empty `topics` map.
- **An assistant on a personal account is an agent.** Telegram marks a bot as a bot, but not an agent that speaks from a personal Telegram account (an assistant signed in as a person, not through BotFather). List such an account with `"kind": "agent"`, in `allowed_users` or the group's `members`: `{"<account id>": {"name": "<assistant>", "kind": "agent"}}`. Listed as a person - worse, as a `supervisor` - every one of its messages gets an unrestricted turn, answered as a reply, with no dialogue cap. The settings check warns about every supervisor entry without a `kind`, since an assistant can be called anything; mark each person `"kind": "human"`.
- `members` is per group, `allowed_users` everywhere; a bot must be named in one of them to be answered, also in direct messages with `direct_messages.mode` `anyone`. Only what allowed people and bots say reaches the bot's working context; anyone else's messages are dropped, and so is the text of a stranger's message an allowed sender replies to.
- A chat is named by its Bot API id, or a supergroup also by its number without `-100`.
- `triggers` on a group or topic narrows what counts as addressing: `username`, `reply`, `command`.
- `defaults.profile` is the harness profile a supervisor's turn runs on: `stub` (no model, for testing), `telegram-claude` or `telegram-codex`, or a project profile named `telegram-*`. A topic or group may name its own `profile`. Everyone else's turns run on a restricted profile (below).
- `TELEGRAM_BOT_SERVICE_PROFILE` in the service's environment outranks `defaults.profile`, not a room's own.
- `debounce` (seconds, 0 to 300; seeded 5, 3 where none is set), on `defaults`, a group or a topic: a turn waits this long after the last message addressed to the bot there, so a request sent as "@bot look:" and then the details is read whole. Each addressed message restarts the wait, as upstream's assistant does.
- The settings check is strict: an unknown key (a typo such as `may_adress`), a number out of its range, or `"false"` where a boolean belongs is refused, at `settings set`, at start and at reload, with the keys that are supported. Every profile the settings name must resolve before a start or a reload is adopted - a daemon whose settings name one that does not, or a restricted profile that does not fit (below), stays up without polling in the state `profile_refused` until `service reload` brings settings that do - and `connection` changes only with a restart.
- A role is the global `allowed_users` entry's, then the group member's, then `direct_messages.default_role` in a direct chat or the group's `member_role`/`role`; a sender's configured `name` is how the bot sees them.

## The context prompt

The context prompt (`context`) is the bot's standing instructions, read for every turn. It lives in the project's store as versions, as upstream keeps its prompts, so every node that runs the service reads the version the store master saved, whatever its own copy of the project holds:

```sh
telegram-bot prompts show context > context.md      # the version in force (JSON: body, id)
telegram-bot prompts put context context.md --note "why"   # a new version, read at the next turn
telegram-bot prompts versions context                # its history; show --version ID reads one
telegram-bot prompts export                          # the file form, into capabilities/telegram-bot/service/
telegram-bot prompts import                          # that folder's files back in, one version each
```

`put --base ID` refuses (`conflict`) when another version became active after the one you read. A file is an optional export: the service reads `service/context.md` only while the store holds no version, or where the project keeps no store. A store that does not answer is never replaced by the file: `service start` and `service reload` keep the daemon's own copy from its last start or reload and say so, or refuse with the store's error.

## On a server

The master is the machine that holds the project's records and its store: the records (connection, grant, settings) and the context prompt are written there and read from the store on the node. The node runs from its own body (its copy of the project) and holds the token in its environment alone.

1. **Token in the node's environment.** In Coolify, on the agent's application for that node, add `TELEGRAM_BOT_<ID>_TOKEN` as a secret environment variable (Coolify's Environment Variables, not the build), and redeploy. It is never written on the master.
2. **Connection and grant, from the master,** in the project's folder there. The key is not set on the master, so the add records it unchecked with `--remote`:

   ```sh
   telegram-bot connection add <id> --project --default --remote \
     --field username=<bot_username> --field expected_bot_id=<bot id> \
     --env secret_env=TELEGRAM_BOT_<ID>_TOKEN
   capabilities set telegram-bot grant <id> '{"enabled": true, "allow_write": true}'
   ```

   Without `--remote` the add stops with `env_key_unset`. The manager's own record write does the same: `capabilities set telegram-bot connection <id> '{"username": "<bot_username>", "expected_bot_id": <bot id>, "secret_env": "TELEGRAM_BOT_<ID>_TOKEN"}'`.
3. **Settings and context, from the master:** `telegram-bot service init --connection <id>`, then the `settings set` lines of section 4 (no `service reload` yet: nothing runs on the master). `telegram-bot prompts show context` shows the prompt every node will read.
4. **Place the service on the node:** `capabilities place telegram-bot --node <node id>`. The placement is exclusive (one `getUpdates` consumer per token), so this takes it off every other node; `capabilities place telegram-bot` says where it is.
5. **Prove it on the node** (in the node's shell, in the project's body): `telegram-bot doctor` (the token resolves from `env`, `getMe` is the bot), `telegram-bot service doctor` and `telegram-bot service status`. A daemon at `waiting` says what it waits for in `service status`; it tries again every minute.

Later changes are made on the master the same way - `settings set` then `service reload` on the node, `prompts put context` with no reload at all.

## What a sender may use

Each turn runs with the sender's `authority`, as upstream's assistant gives it: the daemon writes it to a private file handed to the worker as `CAPABILITIES_AUTH_CONTEXT`, and every managed capability refuses itself to a turn that is not allowed it. Seeded by `service init` (and in force when the settings carry no `authority` at all):

- `supervisor`: every capability.
- `direct_user`: none.
- `group_member` (other bots included): `telegram-bot send`, limited to the current chat and topic, and `routine`'s read-only verbs (`list`, `show`, `validate`, `render`, `history`, `diff`).

A sender, group or member entry may carry its own `allowed_capabilities` or `authority`; `authority.roles.<role>` changes a role everywhere. Give a capability to anyone but a supervisor with a verb list (`{"verbs": ["send"]}`): a capability granted without one is granted with every verb, `settings`, `connection`, `token` and `service` included, and the settings check warns when a role holds `telegram-bot` that way. With the current-chat limit, the worker's `telegram-bot send` to another chat or connection is refused (exit 4) by the worker's shim and again by the CLI; inside a turn `--topic` is refused and the turn's topic is filled in, and `--reply-to` may name only the request the turn answers.

## Restricted turns

Every turn but a supervisor's runs on a restricted profile: a `direct_user`'s, a `group_member`'s, and any bot's, whatever role its entry gives it. It is a claude profile whatever harness serves the supervisor - the room's or `defaults`' `restricted_profile`, else `telegram-claude-restricted`. Codex has no restricted profile: its read-only sandbox still reads the whole disk, the bot's token file included.

`telegram-claude-restricted` has the file tools that read (`Read`, `Grep`, `Glob`) and nothing else, so no shell, no web, no writes, no subagents and no MCP servers; hooks are off and the user's Claude settings do not load. In Claude Code's `default` permission mode a read outside the project asks for permission, which a headless turn cannot be given, so it is refused. The project's own Claude settings do load, and Claude Code writes an allow rule into `.claude/settings.local.json` whenever someone answers "always allow"; such a rule, or an extra directory, would open a folder outside. So every restricted turn also carries deny rules, which outrank any allow: `~/.config`, `~/.ssh`, `~/.local/state`, `/proc`, the bot's own state folder, and every folder beside the project and beside each folder above it, up to the root (a name with glob characters included); and `.env`, `.env.*`, `.envrc`, `*.env` and `.git/config` files inside the project. Memory is off, and git status (the user's name, recent commits) stays out of the turn's context.

A restricted turn has no `progress` line, and if its profile cannot be resolved the turn is refused with a failure note; it never falls back to the profile in force. A policy may set `"restricted": false` (or `true`) in `authority.roles.<role>`, or in a user's, group's or member's `authority`, to change that for its senders. A project's own restricted profile (`restricted_profile: telegram-<name>`) is held to an allowlist, and the daemon will not start on one that leaves it: claude; `tools` listed and only `Read`, `Grep`, `Glob`; permission mode `default` or `dontAsk`; `setting_sources` only `project` and `local`; `strict_mcp` true and no MCP server; settings with `disableAllHooks: true`, deny rules and `autoMemoryEnabled`/`includeGitInstructions` false alone; and no other knob (`add_dirs`, `allowed_tools`, `env_set`, extra arguments and the like are refused).

## What is hard and what is soft

Hard, enforced by something the turn cannot switch off:

- Who gets a turn at all: the gate, the loop guard and the rate limit run in the daemon, outside every turn.
- Where an answer goes: the daemon sends the final answer into the request's own chat and topic.
- A restricted turn: no shell and no file outside the project, enforced by Claude Code (checked on 2.1.310: an outside path, a link to one and a `Bash` call are each refused; with an allow rule and an extra directory for an outside folder in the project's `settings.local.json`, the deny rules still refuse it).

Soft, holding only for a turn that does not set out to get round it:

- The authority gate (`CAPABILITIES_AUTH_CONTEXT`) and the worker shim. Each managed capability checks the variable itself, so a turn with a shell - a supervisor's, or any turn whose policy lifts `restricted` - can unset it, call the real binary, read the bot's token file or other projects' files, or reach the network with `curl`, `gh`, `git` or `ssh`: the bundled unrestricted profiles run with every permission, as upstream's do. Even under the gate, the contract leaves `ids set` and its other discovery verbs open to every grant.
- A restricted turn reads everything inside the project but the denied env files (a deny Claude Code applies to `Grep` as best it can), and may say it in its answer. A folder created beside the project after the turn started is not in its deny rules (the permission mode still refuses it).
- What the turn says: the conversation is quoted material and the context document says so, but a model can still be talked into things.

## Chat commands

An addressed `/command` is answered by the daemon, never by the agent: `/status`, `/profiles`, `/stop [N]` (the running turn and everything waiting, or request N alone), `/reload`, `/set profile|tail|worker-timeout|debounce <value|default>` (this conversation; kept across a restart in the service's state), `/help`. A message is a command only when it opens with a command addressed to this bot (`/status@<bot_username>`), or with any command in a direct chat; `/tmp/build.log is failing @<bot_username>` or another bot's `/status@other_bot` is an ordinary message. Seeded, a supervisor may use all of them, a `channel_admin` status and set, everyone else status and help; `control.roles.<role>.commands` changes that. A bot may use `/status` and `/help` only, whatever role its entry gives it. A request someone else stopped tells its asker who stopped it. `/set profile` is a supervisor's alone, whatever the control settings say, and changes the profile of unrestricted turns only: restricted turns stay on their restricted profile. `/status` shows a supervisor the request numbers and the `/set` overrides, and anyone else only whether a turn runs and how many wait.

## Tags between agents

- A message whose last line is `#noreply` is never answered.
- A request whose last line is `#external` is answered, and everything sent for it carries `#noreply`, so the asking side's own bot stays out of the exchange.

## The loop guard

- Another bot's bare reply is context, never a turn; it has to mention this bot. An answer to a bot goes out top-level, not as a reply, so it reaches that bot only when it mentions it.
- A forwarded message whose origin is not an allowed sender is material, never a request: only the forwarder's own message asks.
- `agent_dialogue.max_turns` (default 3) caps the turns other bots start in one topic; a person's message resets it. `0` lifts the cap.
- `rate_limit.replies_per_minute` (default 6) per topic and `replies_per_minute_total` (default 20) per bot, counting answers (not the pieces a long one is sent in) and the requests still running or waiting; a request over either is dropped and logged, and a person is told so once a minute. Whatever the limits, a topic holds at most 20 requests running or waiting, and the bot 200. A turn relays at most three progress lines beside its answer.
- Redelivered updates and repeated messages are answered once.

## Checks

- `telegram-bot service status` shows the daemon and its health; `service logs` shows every turn and refusal.
- A message the bot should have answered and did not: the log line says why (`not_addressed`, `sender_not_allowed`, `bot_not_allowed`, `topic_not_allowed`, `loop_guard`, `rate_limited`).
- `conflict` in status means another process polls the same token: one bot runs in one place. After five conflicts in a row (about two minutes) the daemon stops polling and stays up in that state, so a supervisor does not restart it in a loop; `service reload` makes it try again.
- `token_refused` (at start or while running) means the token was revoked or replaced, and `bot_mismatch` that it is another bot's: the daemon stops polling at once and stays up in that state, with the reason in `service status` and `service doctor`. Put the current token in its home (`token set`) and restart the service. `service start` run by hand reports either at once and stops the daemon.
- After downtime or sleep, messages older than five minutes (by Telegram's clock) are kept as context but not answered; each conversation gets one note that the bot was offline and to ask again. A request the bot was stopped before answering gets a note too, since Telegram does not deliver it again.
- `bot_locked` means a daemon of another project on this machine already runs this bot: one bot answers for one project. `stale` in status means the daemon says healthy but has not polled for longer than it should.
- A send that timed out after leaving is not sent again (a rare loss rather than a duplicate): the log says "not sure it arrived", and `service doctor` counts them. A daemon killed without a stop (SIGKILL, out of memory) takes up its unanswered requests at the next start; `service doctor` says the previous process was killed.
- Started with `service start`, the daemon keeps `service.log` under 10 MB, with three older files beside it; under a supervisor the log is the supervisor's.
- `direct_messages.mode` `anyone` gives every Telegram user who messages the bot an agent turn in the project, restricted and with the `direct_user` authority; the settings check warns about it in the log, `service status` and `service doctor`.
- The service's state folder (health, journal, log, `/set` overrides, the turns' authority files) lives outside the project, under `~/.local/state/telegram-bot/projects/<project>-<hash>/` (`$XDG_STATE_HOME` where set), so no turn can read another chat's unanswered text; it is the owner's alone (0700, files 0600), and authority files a killed process left are removed at the next start. `service status` names it.
- `service run` (under a supervisor) with nothing to run on yet - no credentials, settings that do not pass, no context prompt in the store, the store down - does not exit: it says why in the log and in a health record at `waiting`, and tries again every minute, so the supervisor does not restart it in a loop.
- An answer that stops part way (Telegram refuses a piece) is followed by one note saying how many parts arrived.
- The bot answers `/status@bot` but stays silent on plain @mentions: it was added to the group while privacy mode was still on, and Telegram keeps that for the group even after `/setprivacy` is disabled (and `doctor` reports `privacy_mode` false). Remove the bot from the group and add it again.
- Answers go out with Telegram formatting: the model's Markdown (bold, italic, code, code blocks, links) is sent as Telegram HTML, everything else escaped, and a long answer is cut so no tag spans two messages. A piece Telegram refuses as badly formed is sent again as plain text, and the log says "formatting refused".
