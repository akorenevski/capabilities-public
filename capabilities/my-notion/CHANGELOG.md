# my-notion — change log

## 2026-10-06 — Help gives the real connection and credential paths

`my-notion help` now declares connections through `capabilities set` and names `~/.config/my-notion/credentials.env`. It used to point at `.capabilities/notion/` and `~/.config/notion/`, which this capability never reads, and promised a fallback connection that the contract no longer has.

## 2026-10-06 — Published from the open capabilities-public source

`my-notion` now installs from the public source `akorenevski/capabilities-public`. A machine that installed it from an earlier private source reinstalls once with `capabilities install my-notion --source capabilities-public`; its connections, identifiers and state stay as they are.
