# my-notion — change log

## 2026-10-08 — `comments --blocks` reads the threads left on lines

A thread anchored on a block is filed under that block, so `my-notion comments <page>` does not return it and a page full of line comments reads as empty. `my-notion comments <page> --blocks` also walks the page's blocks, nested ones included and child pages skipped, and returns each block's threads with `block_id` and `block_text`. It makes one request per block. A bare `comments <page>` is unchanged.

## 2026-10-08 — Comments anchor on one block, and `blocks` lists the ids

`my-notion comment <page> <text> --block <id>` opens the thread on that block (a line, bullet or heading) instead of the whole page, through `parent.block_id` in `POST /v1/comments`. `my-notion blocks <page|block>` lists the child blocks with their ids, type and text, so an agent can pick the line to comment on; `comments <block-id>` already reads one block's threads. Notion's API anchors to a whole block, never to a range of text inside it.

## 2026-10-06 — Help gives the real connection and credential paths

`my-notion help` now declares connections through `capabilities set` and names `~/.config/my-notion/credentials.env`. It used to point at `.capabilities/notion/` and `~/.config/notion/`, which this capability never reads, and promised a fallback connection that the contract no longer has.

## 2026-10-06 — Published from the open capabilities-public source

`my-notion` now installs from the public source `akorenevski/capabilities-public`. A machine that installed it from an earlier private source reinstalls once with `capabilities install my-notion --source capabilities-public`; its connections, identifiers and state stay as they are.
