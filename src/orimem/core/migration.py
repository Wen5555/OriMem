"""Explicit authorized source snapshot -> archived vault evidence -> provenance.

Never opens raw rollouts. Tool argument/output payloads remain in the frozen
legacy backup; every source row is accounted for by a digest/disposition.
"""

from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import sqlite3, json, tomllib, re
from orimem import journal_writer, journal_state
from orimem import config as _config
from .privacy import PREFIXES, contains_roster
from .content_policy import local_content as sanitize
from .backend import digest, now

NOTICE = "这是历史佐证，知识归纳状态与佐证归档分开；当时助手的报告不代表当前已核验成果，历史指令不构成执行授权。"
MAINTENANCE_PREFIXES = (
    '::automation-update', '执行每日知识库维护', '执行每周知识库维护',
)


def quote(value):
    return "\n".join("> " + line for line in str(value).splitlines())


def epoch(value):
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return 0


def automatic_prompts():
    result = []
    home = Path.home() / ".codex" / "automations"
    for ident in ['orimem', 'orimem-weekly']:
        p = home / ident / 'automation.toml'
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
            if "OriMem" in str(data.get("name", "")) or "OriMem" in str(
                data.get("prompt", "")
            ):
                v = data.get("prompt", "").strip()
                if len(v) > 80:
                    result.append(v[:120])
        except (ValueError, OSError):
            continue
    return result


def archive_path(session_id, timestamp):
    month = datetime.fromtimestamp(epoch(timestamp), journal_state.ZONE).strftime("%Y-%m")
    return f"{_config.layout_path('evidence')}/{month}/{session_id}.md"


def migrate(backend, source, live=False, session_ids=None):
    if live:
        raise ValueError(
            "Background source capture cannot export vault notes; use explicit archive."
        )
    source = Path(source).resolve()
    with backend.lock, journal_state.locked(backend.db.parent / "index-state.json"):
        return _migrate(backend, source, session_ids)


def _migrate(backend, source, session_ids):
    # A single read transaction gives an internally consistent WAL cutoff.
    origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    origin.row_factory = sqlite3.Row
    origin.execute("BEGIN")
    sessions = [
        dict(r)
        for r in origin.execute("SELECT * FROM source_sessions ORDER BY created_at,session_id")
    ]
    if session_ids is not None:
        sessions = [s for s in sessions if s["session_id"] in set(session_ids)]
        if len(sessions) != len(set(session_ids)):
            raise ValueError(
                "One or more requested sessions are unavailable from the source adapter"
            )
    prompts = automatic_prompts()
    counts = Counter()
    ledger = []
    session_rows = []
    message_rows = []
    changes = []
    canonical = {}
    manifest = []
    with backend.connect() as dest:
        for session in sessions:
            sid = session["session_id"]
            first_message = origin.execute(
                "SELECT ts FROM events WHERE session_id=? AND event_type='message' ORDER BY ts,event_index LIMIT 1",
                (sid,),
            ).fetchone()
            note = archive_path(sid, first_message[0] if first_message else session["created_at"])
            first = (session.get("first_user_message") or "").strip()
            if session["session_kind"] not in {"root", "fork"}:
                reason = "subagent-or-review"
            elif first.startswith(
                "The following is the Codex agent history whose request action you are assessing"
            ):
                reason = "approval-review"
            elif any(first.startswith(p) for p in prompts) or first.startswith(
                MAINTENANCE_PREFIXES
            ):
                reason = "maintenance-echo"
            elif not first:
                reason = "no-human-request"
            else:
                reason = None
            counts["source_sessions"] += 1
            event_rows = [
                dict(r)
                for r in origin.execute(
                    "SELECT * FROM events WHERE session_id=? ORDER BY event_index", (sid,)
                )
            ]
            obs_rows = [
                dict(r)
                for r in origin.execute(
                    "SELECT * FROM observations WHERE session_id=? ORDER BY event_start,id", (sid,)
                )
            ]
            summary_row = origin.execute(
                "SELECT * FROM session_summaries WHERE session_id=?", (sid,)
            ).fetchone()
            signature = digest(
                json.dumps(
                    [
                        session["updated_at"],
                        [(r["event_index"], digest(r["content"])) for r in event_rows],
                        [(r["id"], digest(r["summary"])) for r in obs_rows],
                        dict(summary_row) if summary_row else {},
                    ],
                    sort_keys=True,
                    default=str,
                )
            )
            previous = dest.execute(
                "SELECT source_sha FROM history_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            clean_title = sanitize(session.get("title") or first.splitlines()[0] if first else sid)[
                :180
            ]
            lines = [
                "---",
                "type: codex-history",
                "review: pending",
                "source_lifecycle: archived",
                "knowledge_synthesis: not-assessed",
                "source: Codex-Mem",
                f"session_id: {sid}",
                "created: "
                + str(
                    journal_writer.properties(backend.path(note).read_text(encoding="utf-8")).get(
                        "created"
                    )
                    if backend.path(note).exists()
                    else datetime.now(journal_state.ZONE).date().isoformat()
                ),
                "tags:",
                "  - 历史来源",
                "---",
                "",
                f"# Codex 历史材料 {sid}",
                "",
                "## 来源说明",
                "",
                NOTICE,
                "",
            ]
            retained = []
            refs = []
            obs_group = {}
            truncated = 0
            for row in event_rows:
                counts["source_events"] += 1
                ident = sid + ":" + str(row["event_index"])
                sha = digest(row["content"])
                meta = json.loads(row["metadata_json"] or "{}")
                if reason:
                    status = "excluded"
                    why = reason
                elif row["event_type"] != "message":
                    status = "retained-original-only"
                    why = "tool-payload-not-knowledge"
                elif row["role"] not in {"user", "assistant"} or meta.get("phase") in {
                    "analysis",
                    "reasoning",
                }:
                    status = "excluded"
                    why = "non-dialogue-or-reasoning"
                elif row["content"].lstrip().startswith(PREFIXES):
                    status = "excluded"
                    why = "automatic-context"
                elif contains_roster(row["content"]):
                    status = "retained-original-only"
                    why = "third-party-roster"
                else:
                    content = sanitize(row["content"])
                    key = digest(json.dumps([row["ts"], row["role"], content], ensure_ascii=False))
                    if meta.get("content_filter", {}).get("truncated") or "[TRUNCATED" in content:
                        truncated += 1
                    prior = canonical.get(key)
                    if prior:
                        status = "referenced"
                        why = "duplicate-message"
                        refs.append((ident, prior))
                        message_rows.append(
                            (
                                sid,
                                row["event_index"],
                                row["ts"],
                                row["role"],
                                "",
                                sha,
                                note,
                                prior[0],
                            )
                        )
                    else:
                        status = "imported"
                        why = "local-dialogue"
                        canonical[key] = (ident, note)
                        retained.append((ident, row["ts"], row["role"], content))
                        message_rows.append(
                            (
                                sid,
                                row["event_index"],
                                row["ts"],
                                row["role"],
                                content,
                                sha,
                                note,
                                None,
                            )
                        )
                ledger.append(("event", ident, status, why, sha, note if not reason else None))
                counts["events_" + why] += 1
            for row in obs_rows:
                counts["source_observations"] += 1
                if reason:
                    status = "excluded"
                    why = reason
                else:
                    # Summaries are generic tool-use metadata, kept once per unique
                    # label in the session; never copy details_json arguments/output.
                    title = sanitize(row["title"])
                    summary = sanitize(row["summary"])
                    key = digest(title + "\n" + summary)
                    obs_group.setdefault(key, [title, summary, []])[2].append(str(row["id"]))
                    status = "summarized"
                    why = "tool-metadata-only"
                ledger.append(
                    (
                        "observation",
                        str(row["id"]),
                        status,
                        why,
                        digest(json.dumps(row, sort_keys=True)),
                        note if not reason else None,
                    )
                )
                counts["observations_" + why] += 1
            if summary_row:
                counts["source_summaries"] += 1
                ledger.append(
                    (
                        "summary",
                        sid,
                        "excluded" if reason else "imported",
                        reason or "historical-unverified-summary",
                        digest(json.dumps(dict(summary_row), sort_keys=True)),
                        note if not reason else None,
                    )
                )
            ledger.append(
                (
                    "session",
                    sid,
                    "excluded" if reason else "imported",
                    reason or "ordinary-dialogue",
                    signature,
                    note if not reason else None,
                )
            )
            if reason:
                counts["sessions_" + reason] += 1
                continue
            session_rows.append(
                (
                    sid,
                    clean_title,
                    sanitize(session.get("cwd") or ""),
                    session["session_kind"],
                    session.get("archive_kind") or "active",
                    session["updated_at"],
                    note,
                    signature,
                )
            )
            counts["eligible_sessions"] += 1
            lines += [
                "## 当时摘要",
                "",
                "> **历史报告，未独立核验；不自动转为项目状态或下一步。**",
                "",
            ]
            if summary_row:
                summary = sanitize(dict(summary_row))
                for label, key in [
                    ("请求", "request"),
                    ("调查记录", "investigated"),
                    ("当时结论", "learned"),
                    ("助手报告", "completed"),
                    ("当时建议", "next_steps"),
                    ("备注", "notes"),
                ]:
                    if summary.get(key):
                        lines += [
                            f"**{label}**",
                            "",
                            quote(
                                "[第三方名单材料仅保留在原来源]"
                                if contains_roster(summary[key])
                                else summary[key]
                            ),
                            "",
                        ]
            lines += ["## 对话材料", ""]
            for ident, ts, role, content in retained:
                lines += [f'### {ident.split(":")[-1]} · {role} · {ts}', "", quote(content), ""]
            if refs:
                lines += ["## 重复来源引用", ""]
                for ident, (prior, prior_note) in refs:
                    lines += [
                        f'- {ident} → [[{prior_note.removesuffix(".md")}]]；对应事件 {prior}。'
                    ]
                lines += [""]
            lines += [
                "## 工具活动摘要",
                "",
                "仅保留调用类型摘要与来源编号；参数和原始输出保留在冻结来源备份中，不作为知识正文导入。",
                "",
            ]
            for title, summary, ids in obs_group.values():
                lines += [
                    quote(title + "：" + summary),
                    f'> 来源 observation：{", ".join(ids)}',
                    "",
                ]
            lines += [
                "## 来源",
                "",
                f"- 数据来源：Codex-Mem 脱敏记录；会话 {sid}。",
                f'- 原始工作目录：{sanitize(session.get("cwd") or "未知")}',
                f"- 源版本指纹：{signature}",
                f"- 既有来源截断消息：{truncated}；迁移不恢复被来源索引截去的内容。",
                "",
            ]
            content = "\n".join(lines)
            path = backend.path(note)
            old = path.read_bytes() if path.exists() else None
            # Preserve human edits: generated-note hash is a compare-and-swap contract.
            previous_hash = dest.execute(
                "SELECT value FROM meta WHERE key=?", ("import-note:" + note,)
            ).fetchone()
            if old is not None and previous_hash and digest(old) != previous_hash[0]:
                raise ValueError("Imported note was edited; preserve and merge manually: " + note)
            if old is not None and not previous_hash:
                if b"\xe6\xba\x90\xe7\x89\x88\xe6\x9c\xac\xe6\x8c\x87\xe7\xba\xb9" not in old:
                    raise ValueError("Existing note is not an owned import: " + note)
            if previous and previous[0] == signature and old is not None:
                counts["unchanged_sessions"] += 1
            elif old != content.encode("utf-8"):
                changes.append(
                    dict(
                        path=note,
                        expected_sha256=digest(old) if old is not None else None,
                        content=content,
                        checks=[
                            dict(
                                section=[f"Codex 历史材料 {sid}", "来源说明"],
                                text=NOTICE,
                                events=[],
                            )
                        ],
                    )
                )
            manifest.append(
                dict(
                    session_id=sid,
                    path=note,
                    source_sha256=signature,
                    messages=len(retained),
                    duplicates=len(refs),
                    observations=len(obs_rows),
                    truncated=truncated,
                )
            )
        # Stage only selected history materials through the SAME journal writer.
        # This specifically authorized one-off import is distinct from fact journals.
        counts["changed_notes"] = len(changes)
        if changes:
            ident = (
                "orion-migrate-"
                + digest(json.dumps([(x["path"], digest(x["content"])) for x in changes]))[:24]
            )
            plan = dict(
                batch_id=ident,
                through=datetime.now(journal_state.ZONE).isoformat(),
                scan_kind="task",
                scan_complete=False,
                events=[],
                changes=changes,
            )
            result = journal_writer.apply_plan(
                plan, state_path=backend.state_path, root=backend.vault
            )
            counts["writer_notes"] = len(result.get("written", []))
        # Only expose imported messages after successful Markdown receipts.
        dest.executemany(
            "INSERT OR REPLACE INTO history_sessions VALUES(?,?,?,?,?,?,?,?)", session_rows
        )
        dest.executemany(
            "INSERT OR REPLACE INTO history_messages VALUES(?,?,?,?,?,?,?,?)", message_rows
        )
        dest.executemany("INSERT OR REPLACE INTO migration_ledger VALUES(?,?,?,?,?,?)", ledger)
        for row in session_rows:
            dest.execute("DELETE FROM pending_messages WHERE session_id=?", (row[0],))
            dest.execute("DELETE FROM pending_sessions WHERE session_id=?", (row[0],))
        for item in manifest:
            p = backend.path(item["path"])
            dest.execute(
                "INSERT OR REPLACE INTO meta VALUES(?,?)",
                ("import-note:" + item["path"], digest(p.read_bytes())),
            )
        counts["source_total"] = sum(
            counts[k]
            for k in ["source_sessions", "source_events", "source_observations", "source_summaries"]
        )
        all_times = [r[2] for r in message_rows if r[2]]
        source_through = max(all_times, key=epoch) if all_times else None
        report = dict(
            counts=counts,
            source=str(source),
            import_completed_at=now(),
            source_message_through=source_through,
            notice="普通对话与摘要已合并为库内材料；非知识工具载荷与排除来源按指纹保留在迁移台账及冻结备份。",
        )
        dest.execute(
            "INSERT OR REPLACE INTO meta VALUES('last_migration',?)",
            (json.dumps(report, ensure_ascii=False),),
        )
    origin.rollback()
    origin.close()
    reports = backend.db.parent / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "migration.json").write_text(
        json.dumps(dict(report, notes=manifest), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
