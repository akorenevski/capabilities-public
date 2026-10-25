"""The bot's own prompts, kept as versioned rows in the project's store.

Adapted from upstream's `service/prompts.py` (UPSTREAM.md): its functions are
upstream's but for the table, which is this capability's own
(`telegrambot_prompts`, under the ledger owner `telegrambot`, migrated by
`open_store`), so a project that runs upstream's `telegram` beside its bot keeps
two `context` prompts, one for each. The service has one prompt today,
`context`, read for every turn; each save is a new row, exactly one row per
project and key is active, and the store refuses any change to a row but
clearing `active`, so every earlier version stays readable as it was.

The store is the prompt's home, so a node that runs the service from its own
copy of the project reads what the store master saved. A file is the prompt's
optional export: `prompts export` writes it, `prompts import` reads it, and the
CLI reads `capabilities/telegram-bot/service/context.md` only while the store
holds no version of the key. Reference documents (`reference.<x>`) are not
prompts and stay files.

Every function but `open_store` takes an autocommit psycopg connection bound to
the store's schema and the project's id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

TABLE = "telegrambot_prompts"
STORE_OWNER = "telegrambot"
STORE_SCHEMA_MAJOR = 1
STORE_SCHEMA_MINOR = 0
# Where a key's file sits under a service directory: `context.<x>` in
# `context/`, every other key beside settings.json.
CONTEXT_PREFIX = "context."
# The key the records layer gives a reference document, which is not a prompt.
REFERENCE_PREFIX = "reference."


class PromptError(Exception):
    """A save the store refused, as the CLI reports it."""

    def __init__(self, slug: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.slug, self.message, self.hint = slug, message, hint


# Upstream's step `0005-prompts`, under this capability's own names.
STORE_STEPS = (
    ("0001-prompts", """
    CREATE TABLE telegrambot_prompts (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id  text NOT NULL CHECK (project_id <> ''),
        key         text NOT NULL CHECK (key ~ '^[^/[:space:]]+$' AND key !~ '^reference[.]'),
        body        text NOT NULL,
        active      boolean NOT NULL DEFAULT true,
        created_at  timestamptz NOT NULL DEFAULT now(),
        created_by  text NOT NULL CHECK (created_by <> ''),
        note        text
    );
    CREATE UNIQUE INDEX telegrambot_prompts_active ON telegrambot_prompts (project_id, key)
        WHERE active;
    CREATE INDEX telegrambot_prompts_key ON telegrambot_prompts (project_id, key, created_at DESC);
    COMMENT ON TABLE telegrambot_prompts IS
        'Every saved version of a telegram-bot service prompt; immutable but for active, which a save clears as it adds the next.';
    COMMENT ON COLUMN telegrambot_prompts.project_id IS
        'The project''s declared id.';
    COMMENT ON COLUMN telegrambot_prompts.key IS
        'The document key: the service file''s name without .md, lower-cased with _ as -.';
    COMMENT ON COLUMN telegrambot_prompts.body IS
        'The prompt''s whole text, kept exactly.';
    COMMENT ON COLUMN telegrambot_prompts.active IS
        'The version the service reads; at most one per project and key.';
    COMMENT ON COLUMN telegrambot_prompts.created_by IS
        'Who saved the version: user@node, or the verb that seeded it.';
    COMMENT ON COLUMN telegrambot_prompts.note IS
        'Why the version was saved.';
    CREATE FUNCTION telegrambot_prompts_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF tg_op IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'telegram-bot prompt versions are never deleted: save a new version'
                USING ERRCODE = 'check_violation';
        END IF;
        IF (to_jsonb(new) - 'active') IS DISTINCT FROM (to_jsonb(old) - 'active') THEN
            RAISE EXCEPTION 'a telegram-bot prompt version is immutable: save a new version'
                USING ERRCODE = 'check_violation';
        END IF;
        IF new.active AND NOT old.active THEN
            RAISE EXCEPTION 'a telegram-bot prompt version is not made active again: save its text as a new version'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN new;
    END $$;
    CREATE TRIGGER telegrambot_prompts_guard BEFORE UPDATE OR DELETE ON telegrambot_prompts
        FOR EACH ROW EXECUTE FUNCTION telegrambot_prompts_guard();
    CREATE TRIGGER telegrambot_prompts_truncate BEFORE TRUNCATE ON telegrambot_prompts
        FOR EACH STATEMENT EXECUTE FUNCTION telegrambot_prompts_guard();
    """),
)


# The settings whose store this process has migrated: once per process, not
# once per read.
_MIGRATED: set = set()


def open_store(project_root, setting=None):
    """An autocommit connection to the project's store, bound to its schema,
    with this capability's prompt table migrated (once per process and
    setting). The database is the one the
    project resolves (its `.env.local` / `.env`, the process environment, the
    machine's setting), or `setting`. The caller closes it. Raises PromptError
    with the library's slug where the store is not configured or does not
    answer."""
    try:
        from capabilities_contract.db import DbError, connect, migrate, resolve_setting
    except ImportError as exc:
        raise PromptError("store_library_missing",
                          "the shared database library capabilities-contract is not installed",
                          "run the capability through its own script header, which pins it"
                          ) from exc
    try:
        if setting is None:
            setting = resolve_setting(project_root)
        conn = connect(application_name="telegram-bot", setting=setting, transaction=True)
    except DbError as exc:
        raise PromptError(exc.slug, exc.message, exc.hint) from exc
    try:
        if setting not in _MIGRATED:
            migrate(conn, STORE_OWNER, STORE_STEPS, major=STORE_SCHEMA_MAJOR,
                    minor=STORE_SCHEMA_MINOR)
            _MIGRATED.add(setting)
        conn.autocommit = True
    except DbError as exc:
        conn.close()
        raise PromptError(exc.slug, exc.message, exc.hint) from exc
    except Exception:
        conn.close()
        raise
    return conn


def document_key(relative: Path) -> str:
    """The key a service file answers to, as the records layer folds it: the
    name without `.md`, lower-cased, `_` as `-`, and `context.` before a file in
    `context/`."""
    relative = Path(relative)
    stem = relative.stem.replace("_", "-").lower()
    return f"{CONTEXT_PREFIX}{stem}" if relative.parent.name == "context" else stem


def is_prompt_key(key: str) -> bool:
    return bool(key) and not key.startswith(REFERENCE_PREFIX)


_COLUMNS = "id::text, key, body, active, created_at, created_by, note"


def _row(values) -> dict:
    vid, key, body, active, created_at, created_by, note = values
    return {"id": vid, "key": key, "body": body, "active": active,
            "created_at": created_at.isoformat() if created_at else None,
            "created_by": created_by, "note": note}


def active(conn, project_id: str, key: str) -> dict | None:
    """The version of `key` in force for the project, or None where the store
    holds none."""
    found = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} WHERE project_id = %s AND key = %s AND active",
        (project_id, key)).fetchone()
    return _row(found) if found else None


def version(conn, project_id: str, key: str, version_id: str) -> dict | None:
    """One version of `key`, active or not, or None where it is not one."""
    found = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} WHERE project_id = %s AND key = %s "
        "AND id::text = %s", (project_id, key, version_id)).fetchone()
    return _row(found) if found else None


def versions(conn, project_id: str, key: str) -> list[dict]:
    """Every version of `key`, newest first, without their text."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} WHERE project_id = %s AND key = %s "
        "ORDER BY created_at DESC, id", (project_id, key)).fetchall()
    return [{k: v for k, v in _row(r).items() if k != "body"} for r in rows]


def keys(conn, project_id: str) -> list[dict]:
    """Every key the project holds a version of: its active version and how
    many versions it has."""
    rows = conn.execute(
        f"SELECT key, count(*), max(id::text) FILTER (WHERE active), "
        f"max(created_at) FILTER (WHERE active) FROM {TABLE} WHERE project_id = %s "
        "GROUP BY key ORDER BY key", (project_id,)).fetchall()
    return [{"key": key, "versions": count, "active": vid,
             "active_since": since.isoformat() if since else None}
            for key, count, vid, since in rows]


def save(conn, project_id: str, key: str, body: str, created_by: str,
         note: str | None = None, base: str | None = None) -> tuple[dict, bool]:
    """Make `body` the active version of `key`, as a new row that retires the
    one it replaces in the same transaction. A body equal to the active one
    saves nothing. `base`, where given, is the version the writer read: a save
    over any other version is refused as `conflict`, and so is one that lost a
    race for the same key. Answers the active version and whether it is new."""
    if not is_prompt_key(key):
        raise PromptError("input", f"{key!r} is not a prompt key",
                          "a prompt key is a service file's name without `.md`; "
                          "reference documents stay files")
    from psycopg import errors
    try:
        with conn.transaction():
            current = conn.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE project_id = %s AND key = %s "
                "AND active FOR UPDATE", (project_id, key)).fetchone()
            current = _row(current) if current else None
            if base is not None and (current or {}).get("id") != base:
                raise PromptError(
                    "conflict",
                    f"the active version of {key} is {(current or {}).get('id')}, "
                    f"not {base}",
                    f"read it again with `telegram-bot prompts show {key}` and save over it")
            if current is not None and current["body"] == body:
                return current, False
            if current is not None:
                conn.execute(f"UPDATE {TABLE} SET active = false WHERE id = %s::uuid",
                             (current["id"],))
            found = conn.execute(
                f"INSERT INTO {TABLE} (project_id, key, body, created_by, note) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (project_id, key, body, created_by, note)).fetchone()
    except errors.UniqueViolation as exc:
        raise PromptError("conflict", f"another save of {key} landed first",
                          f"read it again with `telegram-bot prompts show {key}`") from exc
    except errors.CheckViolation as exc:
        raise PromptError("input", f"the store refused {key!r}: {exc}") from exc
    return _row(found), True


# --- The file form ---------------------------------------------------------------

def files(directory: Path, key_of: Callable[[Path], str]) -> dict[str, Path]:
    """The prompt files a service directory holds, by key: `*.md` beside
    settings.json and `context/*.md`, the first file a key folds from winning
    as it does for the daemon. `key_of` is `settings_schema.document_key`."""
    found: dict[str, Path] = {}
    for folder in (directory, directory / "context"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.is_file():
                found.setdefault(key_of(path.relative_to(directory)), path)
    return found


def file_for(directory: Path, key: str, key_of: Callable[[Path], str]) -> Path:
    """Where `key`'s file is in `directory`: the file it already folds from,
    else its canonical name."""
    existing = files(directory, key_of).get(key)
    if existing is not None:
        return existing
    if key.startswith(CONTEXT_PREFIX):
        return directory / "context" / f"{key[len(CONTEXT_PREFIX):]}.md"
    return directory / f"{key}.md"


def import_files(conn, project_id: str, directory: Path, key_of: Callable[[Path], str],
                 created_by: str, note: str | None = None) -> list[dict]:
    """Save every prompt file in `directory` whose text is not the active
    version already; answers each key's outcome."""
    report = []
    for key, path in sorted(files(directory, key_of).items()):
        body = path.read_text(encoding="utf-8")
        row, saved = save(conn, project_id, key, body, created_by, note=note)
        report.append({"key": key, "file": str(path), "version": row["id"],
                       "outcome": "saved" if saved else "unchanged"})
    return report


def export_files(conn, project_id: str, directory: Path,
                 key_of: Callable[[Path], str]) -> list[dict]:
    """Write every key's active version into `directory` in the file form; a
    file already equal is left alone."""
    report = []
    for entry in keys(conn, project_id):
        row = active(conn, project_id, entry["key"])
        if row is None:
            continue
        path = file_for(directory, row["key"], key_of)
        if path.is_file() and path.read_text(encoding="utf-8") == row["body"]:
            outcome = "unchanged"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(row["body"], encoding="utf-8")
            outcome = "written"
        report.append({"key": row["key"], "file": str(path), "version": row["id"],
                       "outcome": outcome})
    return report
