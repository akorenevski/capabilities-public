# Authoring managed capabilities

This repository is the sole editable source for `capabilities-public`. Its canonical
workspace is `~/.capabilities/sources/capabilities-public` (the folder itself is `~/dev/capabilities-public`). Never edit installed payloads under `~/.capabilities/`,
manager caches under `~/.cache/capabilities/`, or a consuming project's
`capabilities/` envelope as capability source code.

Create a capability only with:

```sh
capabilities new <name> --source capabilities-public
```

The manager stamps generated contract regions from `contract/preamble.py`.
Never edit text between `# >>> contract:` and `# <<< contract:` markers.
Stamp them only with `bin/sync-contract`, then `bin/sync-contract --check`: besides the regions it aligns each script's `capabilities-contract==` pin with the installed manager's, which `capabilities sync-contract` and `capabilities audit` never look at, so new region code can otherwise run against an old library, or none.
Before every commit, stage the intended source files and derive the catalogue
from that exact index:

```sh
git add <intended-files>
capabilities source index capabilities-public --staged
```

Install for a local smoke test with `capabilities install <name> --source
capabilities-public`. After committing, run `capabilities source verify capabilities-public
--ref HEAD`, then publish only with `capabilities source release capabilities-public
--ref HEAD`. There is no conformance override: a managed capability must pass
the contract and audit. A free-form CLI remains usable directly, outside this
manager.
