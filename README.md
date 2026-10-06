# my-capabilities

Capabilities by Alexander Korenevski: self-contained command-line tools for AI agents.

## Built on

This repository runs on two open tools by Konstantin Žilin and [Cluster One](https://aicluster.one):

- [capabilities](https://github.com/ai-cluster-one/capabilities): the manager that installs, gates and updates capabilities, and the official catalogue. This repository is one more source it installs from, and every capability here follows its contract.
- [ContextKit](https://github.com/ai-cluster-one/context-kit): the project context for agents. In a project that uses these capabilities, the manager keeps their settings where ContextKit says, and their summaries reach the agent through its context.

## Capabilities

| Capability | What it does |
|---|---|
| [`my-notion`](capabilities/my-notion) | Notion over the REST API: pages and databases. Publish markdown, create and upsert pages; create databases, add, query and update rows on the 2026-03-11 data-source model; views, users, comments, attachments and archive. Several workspaces through named connections. |

## Install

With the capabilities manager installed:

```sh
capabilities source add my-capabilities https://github.com/akorenevski/my-capabilities.git
capabilities install my-notion --source my-capabilities --allow
```

`--allow` is the machine owner's yes to running a capability from a source outside the official catalogue; without it the capability arrives quarantined until they run `capabilities allow my-notion`. Then `my-notion help` for the full usage contract and `my-notion doctor` to prove the connection. Updates arrive with `capabilities update my-notion`.

Each capability reads its own credentials from the machine, never from this repository. For `my-notion`, create an internal integration for each Notion workspace at [notion.so/my-integrations](https://www.notion.so/my-integrations) and connect it to the pages it should reach. Then, in the project that uses it, declare one connection per workspace and the default one:

```sh
capabilities enable my-notion --project
capabilities set my-notion connection work '{"secret_env": "NOTION_WORK_TOKEN", "allow_write": true}'
capabilities set my-notion setting connection.default work
```

Put each token in the project's `.env.local` (or `~/.config/my-notion/credentials.env`) as `NOTION_WORK_TOKEN=...`, never on the command line, and run `my-notion doctor`.

## Authoring

See [AUTHORING.md](AUTHORING.md). Licensed under [MIT](LICENSE).
