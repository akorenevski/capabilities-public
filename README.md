# my-capabilities

Capabilities by Alexander Korenevski: self-contained command-line tools for AI agents, installed and gated by the [capabilities manager](https://github.com/ai-cluster-one/capabilities).

| Capability | What it does |
|---|---|
| [`my-notion`](capabilities/my-notion) | Notion over the REST API: pages and databases. Publish markdown, create and upsert pages; create databases, add, query and update rows on the 2026-03-11 data-source model; views, users, comments, attachments and archive. Several workspaces through named connections. |

## Install

With the capabilities manager installed:

```sh
capabilities source add my-capabilities https://github.com/akorenevski/my-capabilities.git
capabilities install my-notion --source my-capabilities
```

Then `my-notion help` for the full usage contract and `my-notion doctor` to prove the connection. Updates arrive with `capabilities update my-notion`.

Each capability reads its own credentials from the machine, never from this repository. For `my-notion`, create an internal integration at [notion.so/my-integrations](https://www.notion.so/my-integrations), connect it to the pages it should reach, and put its token in `~/.config/notion/credentials.env` as `NOTION_TOKEN=...`; `my-notion help` describes connections to more than one workspace.

## Authoring

See [AUTHORING.md](AUTHORING.md). Licensed under [MIT](LICENSE).
