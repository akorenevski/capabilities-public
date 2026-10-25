# Upstream

`telegram-bot` is derived from the `telegram` capability of [ai-cluster-one/capabilities](https://github.com/ai-cluster-one/capabilities), `capabilities/telegram`, under the MIT licence; its notice is kept whole in `LICENSE-UPSTREAM` (Copyright (c) 2026 Cluster One). Upstream signs a personal account in over MTProto; this capability speaks the Bot API as one bot per project, so it carries the parts of upstream that do not depend on being an account.

## Taken from

Upstream commit `0cc217bf1dc34daafb34264683268606727e8173` (2026-10-24, "A command deployment runs that fails is named, with its own error").

## What was taken

- **Verbatim** - byte for byte, and held so by `tests/test_upstream.py` (each file's sha256 below): the job register the profile resolver loads by path to reach the project's store (`service/jobs.py`: `profiles.py` takes `store_setting` and `JobError` from it; the bot keeps no job register of its own), the worker knowledge (`service/workers.py`), and the two bundled turn profiles. Profile names keep upstream's `telegram-` prefix, so a project's stored `telegram-*` profiles serve its bot as they serve its account.
- **Adapted** - upstream's behaviour carried into Bot API terms, each file naming what it took in its docstring:
  - `service/profiles.py` - the harness profile resolver (it pins `callva-harness-runner`), upstream's whole but for its `save` verb and the `/set` role check `save` read out of upstream's `daemon.py` (`CONTROL_DEFAULTS`), which this bot's daemon does not keep; the bot never saves a profile.
  - `service/gate.py` - triggers (`username`, `reply`, plus `command`), allowed groups, topics and members, agent peers (`kind: agent`), the rule that an agent's bare reply is context, and upstream's reading of forum topics (General as topic 1, the `#topic:` channel key).
  - `service/authority.py` - per-sender authority and the chat-command gate: upstream's `_authority_policy_for`, `_normalize_allowed_capabilities`, `_authority_summary`, `write_authority_context`, `_control_policy_for` and `_control_commands_allow`, taking the settings as an argument; a document without `authority` is held to the seeded template rather than granted everything.
  - `service/worker-bin/telegram-bot` - upstream's `service/worker-bin/telegram`, cut to this bot's verbs: the `current_chat` scope (`chat_scope_denied`), no other topic or connection, and the `progress` outbox.
  - `service/guard.py` - `agent_dialogue` (`max_turns`, `reset_on_human_message`) as upstream's `_agent_dialogue_policy`, `_reset_agent_dialogue_for_human` and `_admit_agent_turn` count it; the default cap, dedup and rate limit are this capability's.
  - `service/prompt.py` - `build_prompt`'s shape, `_message_display_time` (Tallinn date and time per row) and `_format_conversation`'s date lines, and `REPLY_MARKER_LINE`, `REPLY_MARKER`, `cut_at_reply_marker`, `_quoted` and `CONVERSATION_END` unchanged; `format_conversation` frames the tail as `_format_conversation` does; `split_text` falls back to a space as `message_chunks` does.
  - `service/settings_check.py` - the settings shapes upstream's `settings_schema.py` reads, for the keys both mean the same by, checked as strictly (`_unknown`, numbers with bounds, booleans, id signs), and the seeded `control` and `authority` of `templates/settings.json`.
  - `service/gate.py` also carries `_marker_line`, `_with_marker`, `NO_REPLY_MARKER` and `EXTERNAL_REQUEST_MARKER` unchanged, and `_reply_target`, `sender_role_for` and `_sender_profile`'s naming in Bot API terms.
  - `service/daemon.py` - the service process pattern: launch nonce, health record, SIGHUP reload adopted only when valid, the stub worker, the profile order (room, `TELEGRAM_BOT_SERVICE_PROFILE` in place of `TELEGRAM_SERVICE_PROFILE`, `defaults.profile`, stub), worker turns through `callva-harness-runner` with upstream's worker environment scrub and `worker_env_parts`' overlay (the shim on PATH, CAPABILITIES_AUTH_CONTEXT, the progress outbox); `now_display`, `_vanished_runtime` and `ProgressThrottle` unchanged; `report_unclean_exit`, the control commands of `handle_command`, every addressed message its own job, the per-conversation `debounce` that each addressed message restarts (`arm`), and the requeue of unfinished work after a kill, in a bot's terms.
  - `service/templates/context.md` - upstream's `templates/context.md`, cut to what a bot can do.
  - `service/prompts.py` - upstream's `service/prompts.py` whole, on the bot's own table (`telegrambot_prompts`, created by its own `STORE_STEPS`, upstream's step `0005-prompts` renamed, under the ledger owner `telegrambot`), with `open_store` and `document_key` in place of the register's connection and `settings_schema.document_key`; the CLI's `prompts` verbs are upstream's `cmd_prompts`.
  - `bin/telegram-bot` - the service lifecycle verbs and settings records (`setting` collection) and the `context` prompt kept in the store (`prompts` verbs) as upstream's `bin/telegram` keeps them; connections with an identity field like `expected_account_id`.
- **New** - `service/botapi.py` (Bot API client), `service/markup.py` (Markdown to Telegram HTML, cut into messages), `service/connection.py` (bot connection resolution), the restricted turn profile `service/profiles/telegram-claude-restricted.toml` (upstream runs every turn on its full profiles), the tests, the guide.

The carry script reads this block:

```json
{
  "upstream": "https://github.com/ai-cluster-one/capabilities.git",
  "path": "capabilities/telegram",
  "commit": "0cc217bf1dc34daafb34264683268606727e8173",
  "verbatim": {
    "service/workers.py": "fa0a2f7ffb9f9893d78fb43216221133936466985cfe556ce64a900ccd2af430",
    "service/jobs.py": "02743764d6758544be621954593ef96190c975b90a42cd81ffd89bdb5689aede",
    "service/profiles/telegram-claude.toml": "75b40935b63f2ed844393d8e9a4b6c8e708644baa1bd369aaafcc326a28943b3",
    "service/profiles/telegram-codex.toml": "7cf35cd49350f978c5d18c919a261c23b212d30d32d98329b2a590b41a6df6d5"
  },
  "adapted": {
    "service/profiles.py": ["service/profiles.py"],
    "service/gate.py": ["service/daemon.py", "service/forum.py"],
    "service/authority.py": ["service/daemon.py"],
    "service/worker-bin/telegram-bot": ["service/worker-bin/telegram"],
    "service/guard.py": ["service/daemon.py"],
    "service/prompt.py": ["service/daemon.py"],
    "service/settings_check.py": ["service/settings_schema.py", "service/templates/settings.json"],
    "service/daemon.py": ["service/daemon.py"],
    "service/templates/context.md": ["service/templates/context.md"],
    "service/prompts.py": ["service/prompts.py"],
    "bin/telegram-bot": ["bin/telegram"]
  }
}
```

## Carrying a new upstream release

`bin/carry-upstream telegram-bot [--ref REF] [--apply]` at the root of this source does the mechanical half; nothing schedules it.

1. It fetches upstream at `REF` (default `main`; a branch, tag or commit) into a scratch clone and compares the recorded commit with it.
2. A verbatim file upstream changed is copied over ours under `--apply`, and its new hash written into the block above; without `--apply` it is only listed.
3. For every adapted file it prints upstream's diff of the origin files between the two commits. Those need a person (or an agent working in this source): read the diff, carry what applies into our file, or record why not.
4. With `--apply` it then runs the suite under uv (`TEST_RUNNER` in the script) and moves the recorded commit, unless an adapted origin moved: then it keeps the old commit and records `pending` (the new commit and the adapted files), so the next run shows the same diffs; `--apply --reviewed`, once they are carried, moves it. It commits nothing.
5. The answer is one JSON object: `verbatim_changed`, `adapted_to_review` (with the diff stat of each origin; each origin's diff goes to stderr once), `missing_modules` (files a verbatim file loads by path, `HERE / "x.py"`, that this capability does not carry), `tests`, and `needs_person`, a one-line note for whoever carries it whenever an adapted origin moved, a module is missing or the suite failed. It exits 1 when the suite fails or cannot run, or a module is missing.

After a carry: `bin/sync-contract`, stage the named files, `capabilities source index capabilities-public --staged`, commit, push, `capabilities source verify capabilities-public --ref HEAD`; releasing is the source owner's call.
