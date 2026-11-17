"""Commit ordinary source observations/checkpoint/jobs without vault writes."""

from pathlib import Path
import sqlite3, json
from contextlib import closing
from .backend import digest, now
from .migration import automatic_prompts,MAINTENANCE_PREFIXES
from .privacy import PREFIXES, contains_roster
from .content_policy import local_content


def eligible(session, prompts):
    first = (session.get("first_user_message") or "").strip()
    return (
        session["session_kind"] in {"root", "fork"}
        and bool(first)
        and not first.startswith(MAINTENANCE_PREFIXES)
        and not first.startswith(
            (
                "The following is the Codex agent history whose request action you are assessing",
                "::automation-update",
                "执行每日知识库维护",
                "执行每周知识库维护",
            )
        )
        and not any(first.startswith(p) for p in prompts)
    )


def stage_sources(backend, source, namespace='authorized-local-adapter-v1'):
    """namespace keeps each adapter's inventory checkpoint separate (Codex default)."""
    from .continuous import observe, checkpoint
    source = Path(source).resolve()
    prompts = automatic_prompts()
    changed = 0
    pending = 0
    manifest = []
    with backend.lock, closing(
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    ) as src, backend.connect() as dest:
        src.row_factory = sqlite3.Row
        src.execute("BEGIN")
        for row in src.execute("SELECT * FROM source_sessions ORDER BY created_at,session_id"):
            session = dict(row)
            if not eligible(session, prompts):
                dest.execute('DELETE FROM pending_messages WHERE session_id=?',(session['session_id'],))
                dest.execute('DELETE FROM pending_sessions WHERE session_id=?',(session['session_id'],))
                continue
            sid = session["session_id"]
            messages = []
            capture_events = []
            for event in src.execute(
                "SELECT * FROM events WHERE session_id=? AND event_type='message' ORDER BY event_index",
                (sid,),
            ):
                meta = json.loads(event["metadata_json"] or "{}")
                body = event["content"]
                if (
                    event["role"] not in {"user", "assistant"}
                    or meta.get("phase") in {"analysis", "reasoning"}
                    or body.lstrip().startswith(PREFIXES)
                    or contains_roster(body)
                ):
                    continue
                messages.append(
                    (
                        sid,
                        event["event_index"],
                        event["ts"],
                        event["role"],
                        local_content(body),
                        digest(body),
                        None,
                        None,
                    )
                )
                capture_events.append(dict(event))
            # Archive truth remains fixed until an explicit archive operation.
            archived = list(
                dest.execute(
                    "SELECT event_index,ts,role,content,duplicate_of FROM history_messages WHERE session_id=? ORDER BY event_index",
                    (sid,),
                )
            )
            archived_messages = []
            for r in archived:
                content = r["content"]
                if r["duplicate_of"]:
                    prior, ident = r["duplicate_of"].rsplit(":", 1)
                    found = dest.execute(
                        "SELECT content FROM history_messages WHERE session_id=? AND event_index=?",
                        (prior, int(ident)),
                    ).fetchone()
                    content = found[0] if found else content
                archived_messages.append((r["event_index"], r["ts"], r["role"], content))
            current = [(m[1], m[2], m[3], m[4]) for m in messages]
            captured_version, _ = observe(dest,session,capture_events,backend.embedder.model_id,
                                          project_required=archived_messages!=current)
            manifest.append(dict(session_id=sid,version=captured_version,messages=len(capture_events)))
            if archived_messages == current:
                dest.execute("DELETE FROM pending_messages WHERE session_id=?", (sid,))
                dest.execute("DELETE FROM pending_sessions WHERE session_id=?", (sid,))
                continue
            signature = digest(json.dumps(current, ensure_ascii=False))
            prior = dest.execute(
                "SELECT source_sha FROM pending_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            pending += 1
            if prior and prior[0] == signature:
                continue
            title = local_content(session.get("title") or session["first_user_message"])[:180]
            dest.execute(
                "INSERT OR REPLACE INTO pending_sessions VALUES(?,?,?,?,?,?,?,?)",
                (
                    sid,
                    title,
                    local_content(session.get("cwd") or ""),
                    session["session_kind"],
                    session.get("archive_kind") or "active",
                    session["updated_at"],
                    None,
                    signature,
                ),
            )
            dest.execute("DELETE FROM pending_messages WHERE session_id=?", (sid,))
            dest.executemany("INSERT INTO pending_messages VALUES(?,?,?,?,?,?,?,?)", messages)
            changed += 1
        result = dict(
            pending_sessions=pending,
            changed_sessions=changed,
            at=now(),
            vault_writes=0,
            content_masking=False,
        )
        dest.execute(
            "INSERT OR REPLACE INTO meta VALUES('last_source_stage',?)", (json.dumps(result),)
        )
        checkpoint(dest, namespace, manifest)
        src.rollback()
    result['lifecycle'] = backend.lifecycle.discover_pending()
    from .message_sources import reconcile
    result['canonical_sources']=reconcile(backend.lifecycle)
    backend.lifecycle.health_stage('capture')
    return result
