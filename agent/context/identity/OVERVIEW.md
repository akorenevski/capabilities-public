---
title: my-capabilities
description: Load first in this repository — what it is (a managed capability source that is also this project's home), where its folders live and why, the authoring rules, and what may be committed to it.
load: inline
order: 100
---

# my-capabilities

This repository is our own managed capability source, registered with the capabilities manager as `my-capabilities`, and it is also this project's home. Its capabilities are ours and independent; the official catalogue (`ai-cluster-one/capabilities`) is installed from, never forked.

The real folder is `~/dev/my-capabilities`. The manager keeps every source workspace under `~/.capabilities/sources/`, so it reaches this one through the link `~/.capabilities/sources/my-capabilities`. Work, commit and push here. Installed payloads under `~/.capabilities/<name>/` and caches under `~/.cache/capabilities/` are never source.

The project body lives under `agent/` (`body.root`), so this project's capability envelope is `agent/capabilities/`. The root `capabilities/` folder holds capability source bundles only, as `capabilities/<name>/bin/<name>`: the manager's indexer reads every folder there as a capability, so nothing else goes in it.

Read `AUTHORING.md` before editing a capability. Create one only with `capabilities new <name> --source my-capabilities`. Never hand-edit generated contract regions. Stage the intended files and run `capabilities source index my-capabilities --staged` before committing; verify the commit and publish through the manager's source verbs described in `AUTHORING.md`.

This repository is meant to be public. Commit nothing that cannot be: no keys or tokens, no chat or group ids, no personal names or machine paths in shipped code, and English only in shipped code. A capability that cannot be made public stays in the private source `house` until it is cleaned.
