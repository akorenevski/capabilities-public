# my-notion — change log

## 2026-10-08 — Edit a page in place, search, restore, edit a comment, attach anywhere

`publish` rebuilds every block, and a comment left on a line is lost with its block. Three verbs now change a page and keep every other block, its id and its threads: `edit <page> --old <text> --new <md>` replaces text through the markdown endpoint's `update_content` (each match must be unique unless `--all`, otherwise Notion refuses and nothing changes); `insert <page> --after <text>` adds markdown after one line; `block-update <block> <text>` rewrites one block's text by its id. Verified against the live API: the edited lines keep their ids and their comments.

`search <text>` finds pages and databases by title among those shared with the integration; a database hit carries the database id that every `<db>` argument takes. The old note that search is closed to such tokens is replaced: it answers with an internal integration token.

`restore <ref>` takes a page, a database (`--database`) or a deleted block (`--block`) out of the trash. A page returns under its old parent; a block returns at the end of its page.

`comment-edit <comment> <text>` replaces the text of a comment this token wrote, in its thread, under the same id.

`attach` takes a public http(s) URL as well as a local file (Notion fetches it itself), sends a file over 20 MB in parts, and with `--cover`, `--icon` or `--property <column>` puts the file on the page cover, the page icon or a Files column of a database row, keeping the files already there. A free workspace takes files up to 5 MB and refuses multi-part uploads; the refusal says so. The multi-part path is not yet exercised on a paid workspace.

`page-meta` and `link`, which already worked, are now in `my-notion help`.

## 2026-10-08 — `comment-delete` removes a comment

`my-notion comment-delete <comment>` deletes one comment through `DELETE /v1/comments/{id}`; the id comes from `comments`. Notion lets a token delete only the comments it wrote and answers 404 for anyone else's, the same as for an id that does not exist, so the error hint names both causes. The other comments in the thread stay. It is a write verb: a read-only connection refuses it with exit 4.

## 2026-10-08 — `comments --blocks` reads the threads left on lines

A thread anchored on a block is filed under that block, so `my-notion comments <page>` does not return it and a page full of line comments reads as empty. `my-notion comments <page> --blocks` also walks the page's blocks, nested ones included and child pages skipped, and returns each block's threads with `block_id` and `block_text`. It makes one request per block. A bare `comments <page>` is unchanged.

## 2026-10-08 — Comments anchor on one block, and `blocks` lists the ids

`my-notion comment <page> <text> --block <id>` opens the thread on that block (a line, bullet or heading) instead of the whole page, through `parent.block_id` in `POST /v1/comments`. `my-notion blocks <page|block>` lists the child blocks with their ids, type and text, so an agent can pick the line to comment on; `comments <block-id>` already reads one block's threads. Notion's API anchors to a whole block, never to a range of text inside it.

## 2026-10-06 — Help gives the real connection and credential paths

`my-notion help` now declares connections through `capabilities set` and names `~/.config/my-notion/credentials.env`. It used to point at `.capabilities/notion/` and `~/.config/notion/`, which this capability never reads, and promised a fallback connection that the contract no longer has.

## 2026-10-06 — Published from the open capabilities-public source

`my-notion` now installs from the public source `akorenevski/capabilities-public`. A machine that installed it from an earlier private source reinstalls once with `capabilities install my-notion --source capabilities-public`; its connections, identifiers and state stay as they are.
