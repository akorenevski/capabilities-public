# Managed capability source

Read `AUTHORING.md` before editing. Create packages with `capabilities new
<name> --source capabilities-public`. Never hand-edit generated contract regions.
Stage intended files and run `capabilities source index capabilities-public --staged`
before committing; verify the commit and publish through the manager's source
verbs described in `AUTHORING.md`. Source code lives only under
`capabilities/<name>/`; installed and project-envelope copies are not source.

This repository is public. Commit nothing that cannot be: no keys or tokens, no
chat, group or account ids, no personal names or machine paths in shipped code,
and English only.
