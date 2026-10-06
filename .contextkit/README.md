# ContextKit Binding

This repository is managed by ContextKit, a local manager for agent project structure, project memory, and runtime context.

The agent body stays in this repository: live context, supporting assets,
repeatable routines, and capability envelopes. ContextKit owns the shape,
compiler, templates, host bindings, guides, and audit tooling that turn that
body into Codex and Claude runtime context.

Common commands:

- `contextkit bootstrap`
- `contextkit init --with-layers`
- `contextkit init --with-template`
- `contextkit build --target codex`
- `contextkit build --target claude`
- `contextkit build --target all`
- `contextkit doctor`
- `contextkit audit`
- `contextkit guide authoring`
- `contextkit guide global-context`
- `contextkit guide memory`

Add durable project context under `agent/context/`. Configure optional cross-project doctrine with `sources.global_context`. Use `contextkit guide memory` before adding project memory, which lives under `agent/memory/`. Put historical or supporting material under `agent/assets/`. Put repeatable procedures under `agent/routines/`. Put installed tool envelopes under `agent/capabilities/`.

Run `contextkit doctor` for the resolved body layout.
