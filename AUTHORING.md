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
