"""Native historical API, served from the merged vault corpus and one backend."""

from datetime import datetime, timezone
import json, base64, os, sqlite3
from .backend import normalized_cwd, digest
from .migration import epoch
from .content_policy import local_content as sanitize


def continuous_storage(backend,c,session_id):
    try:return _continuous_storage(backend,c,session_id)
    except (OSError,ValueError,TypeError,UnicodeError,sqlite3.Error) as error:
        return dict(capture_state='unverified',projection_state='unverified',projection_current=False,metadata_error_type=type(error).__name__),{},{},False


def _continuous_storage(backend,c,session_id):
    """Report canonical save/projection separately from the legacy page state.

    A pending compatibility page can already have a current Markdown
    projection. Filesystem verification must never prevent raw DB pagination.
    Returned maps contain only observation versions, not message text.
    """
    capture=c.execute('SELECT * FROM capture_sessions WHERE session_id=?',(session_id,)).fetchone()
    state=dict(capture_state='not-observed',capture_version=None,projection_state='not-observed',projection_path=None,projection_current=False)
    current={};projected={};integrity=False
    if not capture:return state,current,projected,integrity
    state.update(capture_state='saved',capture_version=capture['snapshot_sha'],observed_at=capture['observed_at'],projection_path=capture['path'])
    snapshot=c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(session_id,capture['snapshot_sha'])).fetchone()
    if snapshot:current={int(i):v for i,v in json.loads(snapshot[0])}
    else:state['capture_state']='snapshot-metadata-missing'
    job=c.execute("SELECT state,error FROM lifecycle_jobs WHERE kind='projection' AND subject=? AND version=?",(session_id,capture['snapshot_sha'])).fetchone()
    state['projection_task_state']=job['state'] if job else 'not-requested'
    projection=c.execute('SELECT * FROM source_projections WHERE session_id=?',(session_id,)).fetchone()
    if not projection:
        state['projection_state']=job['state'] if job else 'not-requested'
        return state,current,projected,integrity
    state.update(projected_capture_version=projection['snapshot_sha'],projected_at=projection['projected_at'],projection_path=projection['path'])
    prior=c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(session_id,projection['snapshot_sha'])).fetchone()
    if prior:projected={int(i):v for i,v in json.loads(prior[0])}
    try:
        if not projection['path'].startswith('90 归档/Codex佐证/'):raise ValueError('Projection is outside authorized evidence')
        from .continuous import managed
        _,_,body=managed(backend.path(projection['path']).read_text(encoding='utf-8-sig'))
        integrity=digest(body)==projection['managed_sha']
        state['projection_integrity']='verified-managed-region' if integrity else 'mismatch'
        state['projection_state']=('current' if projection['snapshot_sha']==capture['snapshot_sha'] else 'stale') if integrity else 'unverified'
    except FileNotFoundError:
        state.update(projection_state='missing',projection_integrity='missing')
    except (OSError,ValueError,UnicodeError) as error:
        state.update(projection_state='unverified',projection_integrity='unreadable',projection_error_type=type(error).__name__)
    state['projection_current']=state['projection_state']=='current'
    return state,current,projected,integrity


def bound(value):
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Explicit timezone is required")
    return int(result.timestamp())


def cursor_encode(kind, filters, offset):
    return base64.urlsafe_b64encode(
        json.dumps(dict(kind=kind, filters=filters, offset=offset), sort_keys=True).encode()
    ).decode()


def cursor_decode(token, kind, filters):
    if not token:
        return 0
    if len(token) > 5000:
        raise ValueError("Invalid cursor")
    try:
        data = json.loads(base64.urlsafe_b64decode(token))
    except Exception:
        raise ValueError("Invalid cursor") from None
    if (
        data.get("kind") != kind
        or data.get("filters") != filters
        or type(data.get("offset")) is not int
        or data["offset"] < 0
    ):
        raise ValueError("Cursor does not belong to these filters")
    return data["offset"]


def list_sessions(
    backend,
    from_time=None,
    to_time=None,
    cwd=None,
    session_kind="conversation",
    archive_kind="all",
    limit=20,
    cursor=None,
):
    if (
        not 1 <= limit <= 50
        or session_kind not in {"conversation", "all", "root", "fork"}
        or archive_kind not in {"all", "active", "archived"}
    ):
        raise ValueError("Only migrated ordinary sessions are available")
    lo, hi = bound(from_time), bound(to_time)
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("Invalid time range")
    filters = dict(
        from_time=from_time,
        to_time=to_time,
        cwd=cwd,
        session_kind=session_kind,
        archive_kind=archive_kind,
    )
    offset = cursor_decode(cursor, "sessions-v2", filters)
    rows = []
    with backend.connect() as c:
        merged = {
            r["session_id"]: dict(r, source_state="archived")
            for r in c.execute("SELECT * FROM history_sessions")
        }
        merged.update(
            {
                r["session_id"]: dict(r, source_state="pending")
                for r in c.execute("SELECT * FROM pending_sessions")
            }
        )
        for row in sorted(
            merged.values(), key=lambda r: (r["updated_at"], r["session_id"]), reverse=True
        ):
            if lo is not None and row["updated_at"] < lo:
                continue
            if hi is not None and row["updated_at"] > hi:
                continue
            if session_kind not in {"conversation", "all"} and row["session_kind"] != session_kind:
                continue
            if archive_kind != "all" and row["archive_kind"] != archive_kind:
                continue
            if cwd:
                a, b = normalized_cwd(row["cwd"]), normalized_cwd(cwd)
                if not (a == b or a.startswith(b + "\\")):
                    continue
            # Discovery uses session timestamps; daily facts MUST use message ts.
            row.pop("source_sha", None)
            row["updated_at_iso"] = datetime.fromtimestamp(
                row["updated_at"], timezone.utc
            ).isoformat()
            rows.append(row)
    page = rows[offset : offset + limit]
    # Verify only the requested bounded page, not every discovered session.
    with backend.connect() as c:
        for row in page:row['continuous_storage']=continuous_storage(backend,c,row['session_id'])[0]
    token = (
        cursor_encode("sessions-v2", filters, offset + len(page))
        if offset + len(page) < len(rows)
        else None
    )
    return sanitize(
        dict(
            sessions=page,
            next_cursor=token,
            backend_pid=os.getpid(),
            historical_notice="历史定位资料；事实按消息ts过滤。source_state仅表示兼容旧快照/暂存；continuous_storage分别报告canonical原文保存与转录投影，不据pending推断尚未同步Markdown。",
        )
    )


def get_messages(backend, session_id, role="all", limit=20, cursor=None):
    if (
        not isinstance(session_id, str)
        or not 1 <= len(session_id) <= 200
        or role not in {"all", "user", "assistant"}
        or not 1 <= limit <= 50
    ):
        raise ValueError("Invalid message request")
    filters = dict(session_id=session_id, role=role)
    position, content_offset, pinned = 0, 0, None
    if cursor:
        try:
            if len(cursor)>5000:
                raise ValueError('Oversized cursor')
            data = json.loads(base64.urlsafe_b64decode(cursor))
            if data.get('kind') != 'messages-v3' or data.get('filters') != filters:
                raise ValueError('Cursor filters changed')
            position,content_offset,pinned = data['offset'],data['content_offset'],data['version']
            if type(position) is not int or type(content_offset) is not int or min(position,content_offset) < 0:
                raise ValueError('Invalid continuation')
        except Exception:
            raise ValueError('Invalid or obsolete message cursor') from None
    with backend.connect() as c:
        c.execute('BEGIN')
        staged = c.execute(
            "SELECT * FROM pending_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        session = (
            staged
            or c.execute(
                "SELECT * FROM history_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        )
        if session is None:
            raise ValueError("Session not migrated")
        version = session['source_sha']
        if pinned and pinned != version:
            raise ValueError('Session changed during pagination; restart this version')
        expected = (
            None
            if staged
            else c.execute(
                "SELECT value FROM meta WHERE key=?", ("import-note:" + session["note_path"],)
            ).fetchone()
        )
        if not staged and (
            not expected or digest(backend.path(session["note_path"]).read_bytes()) != expected[0]
        ):
            raise ValueError(
                "Historical source note changed; reconcile before returning cached message material"
            )
        table = "pending_messages" if staged else "history_messages"
        storage,current_observations,projected_observations,projection_verified=continuous_storage(backend,c,session_id)
        where = 'session_id=?' + (' AND role=?' if role != 'all' else '')
        params = (session_id,role) if role != 'all' else (session_id,)
        total = c.execute(f'SELECT count(*) FROM {table} WHERE {where}',params).fetchone()[0]
        page = [dict(r) for r in c.execute(
            f'SELECT event_index,ts,role,content,note_path,duplicate_of,source_hash FROM {table} WHERE {where} ORDER BY event_index LIMIT ? OFFSET ?',
            (*params,limit,position))]
        budget = 32000
        result = []
        for row in page:
            if row["duplicate_of"]:
                sid, ident = row["duplicate_of"].rsplit(":", 1)
                origin = c.execute(
                    "SELECT content,note_path,source_hash FROM history_messages WHERE session_id=? AND event_index=?",
                    (sid, int(ident)),
                ).fetchone()
                if not origin or origin['source_hash'] != row['source_hash']:
                    raise ValueError('Referenced message version changed; archived snapshot requires reconciliation')
                expected_origin = c.execute(
                    "SELECT value FROM meta WHERE key=?", ("import-note:" + origin["note_path"],)
                ).fetchone()
                if not expected_origin or digest(backend.path(origin["note_path"]).read_bytes()) != expected_origin[0]:
                    raise ValueError("Duplicate historical source changed")
                row["content"] = origin["content"]
            value = sanitize(row["content"])
            size = len(value)
            start = content_offset if not result else 0
            if start > size:
                raise ValueError('Invalid message character offset')
            take = min(size-start,12000)
            if result and take > budget:
                break
            value = value[start:start+take]
            row["content"] = value
            row["source_state"] = "pending" if staged else "archived"
            row['source_version'] = version
            canonical=None
            observation=current_observations.get(row['event_index'])
            if observation:
                canonical=c.execute('SELECT version,content_hash,ts,role,identity_basis FROM capture_versions WHERE session_id=? AND event_index=? AND version=?',(session_id,row['event_index'],observation)).fetchone()
                if not canonical or (canonical['content_hash'],canonical['ts'],canonical['role'])!=(row['source_hash'],row['ts'],row['role']):canonical=None
            row['continuous_message_state']='matches-current-observation' if canonical else 'legacy-view-not-matched'
            row['continuous_message_version']=canonical['version'] if canonical else None
            row['identity_basis']=canonical['identity_basis'] if canonical else 'legacy-view-identity-uncertain'
            row['current_observation_projected']=(projected_observations.get(row['event_index'])==canonical['version']) if canonical and projection_verified else None
            row['content_start'] = start
            row['content_end'] = start+take
            row['content_total'] = size
            row['event_complete'] = start+take == size
            row["truncated"] = not row['event_complete']
            budget -= len(value)
            result.append(row)
            if not row['event_complete']:
                content_offset = row['content_end']
                break
            position += 1
            content_offset = 0
        token = base64.urlsafe_b64encode(json.dumps(dict(kind='messages-v3',filters=filters,
            offset=position,content_offset=content_offset,version=version),sort_keys=True).encode()).decode() if position < total else None
    return sanitize(
        dict(
            session_id=session_id,
            messages=result,
            next_cursor=token,
            source_state="pending" if staged else "archived",
            continuous_storage=storage,
            historical_notice="个人本地来源，保留原文。source_state表示兼容旧快照/暂存，不代表持续转录是否同步；continuous_storage另报canonical保存/投影，current_observation_projected只在角色、时间、内容哈希和当前观察位置匹配时报告。旧替换或截断不能凭空恢复。",
        )
    )
