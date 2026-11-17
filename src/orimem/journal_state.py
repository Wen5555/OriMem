"""Metadata and verified receipts for the OriMem vault. Never reads transcripts."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import time
import uuid

from orimem import config as _config
ZONE = _config.zone()
ROOT = _config.vault()
DEFAULT = _config.home() / "state.json"
AUTHORIZED = _config.journal_from()


def dt(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required.")
    return parsed


def _trusted_roots():
    # The vault and ORIMEM_HOME themselves (and anything above them) may be reached through links:
    # a symlinked home folder or a vault on a junction is the user's choice. Links are rejected only
    # inside them, where a link could redirect a write outside the folder.
    return {Path(os.path.abspath(_config.vault())), Path(os.path.abspath(_config.home()))}


def plain_path(path):
    """Check lexical ancestors BEFORE resolve, including Windows junctions, up to the vault/home root."""
    path = Path(os.path.abspath(path))
    roots = _trusted_roots()
    for item in [path, *path.parents]:
        if item in roots:
            break
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Links and junctions are not allowed.")
    return path


def note_path(relative, root=ROOT):
    root = plain_path(root)
    if not isinstance(relative, str) or not relative:
        raise ValueError("A vault-relative note path is required.")
    parts = relative.replace("\\", "/").split("/")
    if any(p in {"", ".", ".."} or ":" in p for p in parts):
        raise ValueError("Invalid note path.")
    if any(p.casefold() in {".obsidian", ".codex", ".git"} for p in parts):
        raise ValueError("Configuration is not a journal destination.")
    path = plain_path(root.joinpath(*parts))
    if not path.is_relative_to(root) or path.suffix.lower() != ".md":
        raise ValueError("Only Markdown notes inside the vault are allowed.")
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, data):
    path = plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(state_path=DEFAULT, timeout=10):
    """One OS lock for the complete note/state transaction; crash releases it."""
    lock_path = plain_path(state_path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        end = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise ValueError("Journal is busy; retry this batch later.") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)


def load(path=DEFAULT):
    path = plain_path(path)
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "version": 2, "authorized_from": AUTHORIZED, "timezone": _config.zone_name(),
        "last_successful_scan": None, "processed_events": {}, "pending_sessions": []}
    if value.get("version") not in {1, 2} or value.get("authorized_from") != AUTHORIZED:
        raise ValueError("Unexpected state or changed authorization.")
    value["version"] = 2
    for key, default in {"receipts": {}, "facts": {}, "daily_scans": {},
                         "pending_weeks": [], "weekly_runs": {}, "scan_windows": []}.items():
        value.setdefault(key, default)
    return value


def fingerprint(event):
    allowed = {"session_id", "event_index", "turn_id", "ts", "role", "content_hash"}
    if not isinstance(event, dict) or set(event) - allowed:
        raise ValueError("Only event metadata is accepted, without message text.")
    if not all(k in event for k in ("session_id", "ts", "role")):
        raise ValueError("Event metadata is incomplete.")
    if not isinstance(event["session_id"], str) or not 1 <= len(event["session_id"]) <= 128:
        raise ValueError("Invalid session ID.")
    if "event_index" in event:
        if type(event["event_index"]) is not int or event["event_index"] < 0:
            raise ValueError("Invalid event index.")
        locator = str(event["event_index"])
    elif isinstance(event.get("turn_id"), str) and 1 <= len(event["turn_id"]) <= 128:
        locator = event["turn_id"]
    else:
        raise ValueError("Event index or actual turn ID required.")
    if event.get("content_hash") and not re.fullmatch(r"[0-9a-f]{64}", event["content_hash"]):
        raise ValueError("content_hash must be SHA-256.")
    if event["role"] not in {"user", "assistant"}:
        raise ValueError("Only user/assistant messages are eligible.")
    stamp = dt(event["ts"])
    if stamp < dt(AUTHORIZED):
        return None
    if stamp > datetime.now(ZONE) + timedelta(minutes=5):
        raise ValueError("Event timestamp is in the future.")
    basis = (stamp.astimezone(timezone.utc).isoformat(), event["role"],
             event.get("content_hash") or event["session_id"] + ":" + locator)
    return digest(json.dumps(basis, ensure_ascii=False).encode("utf-8"))


def normalize_project_scope(value):
    """None means the complete authorized source scope; paths are vault relative."""
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 50:
        raise ValueError('Project scope must be a nonempty bounded path list or omitted.')
    result = []
    for path in value:
        if not isinstance(path, str):
            raise ValueError('Project scope paths must be strings.')
        path = path.replace('\\', '/').strip().rstrip('/')
        parts = path.split('/')
        if not path or path.startswith('/') or ':' in path or any(p in {'', '.', '..'} or p.casefold()=='.obsidian' for p in parts) or any(ord(c) < 32 for c in path):
            raise ValueError('Project scope needs safe vault-relative paths.')
        result.append(path.casefold())
    return sorted(set(result))


def scope_covers(covered, requested):
    if covered is None:
        return True
    if requested is None:
        return False
    return any(requested == p or requested.startswith(p + '/') for p in covered)


def scopes_overlap(left, right):
    if left is None or right is None:
        return True
    return any(scope_covers(left, p) or scope_covers(right, q) for p in right for q in left)


def merge_pending(current, items, resolved):
    pending = {p["session_id"]: p for p in current["pending_sessions"]}
    for session in resolved:
        pending.pop(session, None)
    for item in items:
        if not {"session_id", "first_ts", "reason"} <= set(item) or set(item) - {"session_id", "first_ts", "reason", "project_scope"}:
            raise ValueError("Pending entries accept session_id, first_ts, reason and optional project_scope.")
        scope = normalize_project_scope(item.get('project_scope'))
        if scope is not None:
            item = dict(item, project_scope=scope)
        if dt(item["first_ts"]) < dt(AUTHORIZED) or len(item["reason"]) > 160:
            raise ValueError("Invalid pending metadata.")
        previous = pending.get(item["session_id"])
        if previous:
            if dt(previous["first_ts"]) < dt(item["first_ts"]):
                item = dict(item, first_ts=previous["first_ts"])
            old_scope = normalize_project_scope(previous.get('project_scope'))
            # A second failed pass cannot silently discard the first pass's
            # unresolved scope. Explicit resolution is required to narrow it.
            if old_scope is None or scope is None:
                item = dict(item, project_scope=None)
            else:
                item = dict(item, project_scope=sorted(set(old_scope + scope)))
        pending[item["session_id"]] = item
    current["pending_sessions"] = list(pending.values())


def _receipt_envelope(receipt_path, state_path=DEFAULT, root=ROOT):
    receipt_path = plain_path(receipt_path)
    if receipt_path.parent != plain_path(state_path).parent / "receipts":
        raise ValueError("Receipt must come from the local writer receipt directory.")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("version") != 1 or receipt.get("state_path") != str(plain_path(state_path)):
        raise ValueError("Receipt does not belong to this journal.")
    if receipt.get("root") != str(plain_path(root)):
        raise ValueError("Receipt belongs to a different vault.")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", receipt.get("batch_id", "")):
        raise ValueError("Invalid receipt batch ID.")
    queue_path = plain_path(state_path).parent / "queue" / (receipt["batch_id"] + ".json")
    queued = json.loads(plain_path(queue_path).read_text(encoding="utf-8"))
    plan = queued["plan"]
    if digest(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")) != queued["plan_sha256"]:
        raise ValueError("Queued plan hash differs.")
    for key, default in {"events": [], "through": None, "scan_kind": "task", "scan_complete": False,
                         "scan_from": None, "pending": [], "resolved_sessions": [], "week": None, 'period': None}.items():
        if receipt.get(key, default) != plan.get(key, default):
            raise ValueError("Receipt differs from its queued plan.")
    if len(receipt["notes"]) != len(plan["changes"]):
        raise ValueError("Receipt does not cover every planned note.")
    if not receipt["notes"] and not (receipt.get("scan_kind") == "daily" and receipt.get("scan_complete") and not receipt["events"]):
        raise ValueError("Only a completed empty daily scan may omit note checks.")
    from orimem.journal_writer import check_note
    changes = {c["path"]: c for c in plan["changes"]}
    eligible = {fingerprint(e) for e in receipt["events"]}
    if len(changes)!=len(plan['changes']) or len({n['path'] for n in receipt['notes']})!=len(receipt['notes']) or set(changes)!={n['path'] for n in receipt['notes']}:
        raise ValueError('Receipt note identities differ from the immutable plan.')
    return receipt,plan,queued,eligible


def verify_receipt(receipt_path, state_path=DEFAULT, root=ROOT):
    # First commits and all ordinary verification still require current bytes.
    receipt,plan,queued,eligible=_receipt_envelope(receipt_path,state_path,root)
    from orimem.journal_writer import check_note
    changes={c['path']:c for c in plan['changes']}
    for note in receipt["notes"]:
        path = note_path(note["path"], root)
        if not path.is_file() or digest(path.read_bytes()) != note["sha256"]:
            raise ValueError("Written note changed; reread and prepare a fresh batch.")
        if not note.get("checks"):
            raise ValueError("Receipt lacks paragraph checks.")
        if check_note(path.read_text(encoding="utf-8"), changes[note["path"]]["checks"], eligible) != note["checks"]:
            raise ValueError("Receipt paragraph verification differs.")
    return receipt


def verify_committed_receipt(receipt_path, state_path=DEFAULT, root=ROOT):
    """Verify a completed historical write, never advance a scan or commit it.

    A retained-source correction may replay its already resolved disposition
    after unrelated proof-page additions. Its caller must additionally verify
    the current original sources, proof paragraph and index. This function is
    not a substitute for verify_receipt during any first commit/closure.
    """
    receipt,plan,queued,eligible=_receipt_envelope(receipt_path,state_path,root)
    raw=plain_path(receipt_path).read_bytes();record=load(state_path)['receipts'].get(receipt['batch_id'])
    if not record or record.get('path')!=str(plain_path(receipt_path)) or record.get('sha256')!=digest(raw) or queued.get('status')!='committed' or plan.get('batch_id')!=receipt['batch_id']:
        raise ValueError('Historical receipt must match its immutable committed state and plan.')
    from orimem.journal_writer import check_note
    changes={c['path']:c for c in plan['changes']}
    for note in receipt['notes']:
        change=changes[note['path']]
        if digest(change['content'].encode('utf-8'))!=note['sha256'] or not note.get('checks') or check_note(change['content'],change['checks'],eligible)!=note['checks']:
            raise ValueError('Historical receipt does not verify its original planned postimage.')
    return receipt


def commit_receipt(receipt_path, current, state_path=DEFAULT, root=ROOT):
    # Caller holds locked(state_path), covering note verification and state save.
    receipt = verify_receipt(receipt_path, state_path, root)
    ident = receipt["batch_id"]
    if ident in current["receipts"]:
        return {"committed_events": 0, "already_committed": True, "batch_id": ident}
    bound = dt(receipt["through"])
    if not dt(AUTHORIZED) <= bound <= datetime.now(ZONE) + timedelta(minutes=5):
        raise ValueError("Invalid receipt scan bound.")
    events = receipt["events"]
    keys = {fingerprint(e) for e in events}
    if None in keys or any(dt(e["ts"]) > bound for e in events):
        raise ValueError("Receipt contains events outside the authorized window.")
    checked = {key for note in receipt["notes"] for check in note["checks"]
               for key in check["events"]}
    if not keys <= checked:
        raise ValueError("Every event needs a verified paragraph binding.")
    fresh = keys - current["processed_events"].keys()
    for event in events:
        key = fingerprint(event)
        current["processed_events"].setdefault(key, {"ts": event["ts"], "receipt": ident})
    for note in receipt["notes"]:
        for check in note["checks"]:
            current["facts"][note["path"] + "|" + check["fact_hash"]] = {"receipt": ident}
    merge_pending(current, receipt.get("pending", []), receipt.get("resolved_sessions", []))
    scan_scope = normalize_project_scope((receipt.get('period') or {}).get('project_scope'))
    if receipt.get("scan_complete") and receipt.get("scan_kind") == "daily" and scan_scope is None:
        old = current["last_successful_scan"]
        current["last_successful_scan"] = max(bound, dt(old) if old else bound).isoformat()
    if receipt.get("scan_kind") == "daily" and receipt.get("scan_complete"):
        windows = current.setdefault('scan_windows', [])
        if not any(w['receipt'] == ident for w in windows):
            windows.append(dict(from_time=receipt['scan_from'],through=bound.isoformat(),receipt=ident,project_scope=scan_scope))
        if scan_scope is None:
            day = bound.astimezone(ZONE).date().isoformat()
            previous = current["daily_scans"].get(day)
            if not previous or bound >= dt(previous["through"]):
                current["daily_scans"][day] = {"through": bound.isoformat(), "from_time": receipt["scan_from"], "receipt": ident}
    if receipt.get("scan_kind") != "weekly":
        for event in events:
            day = dt(event["ts"]).astimezone(ZONE).date()
            week = (day - timedelta(days=day.weekday())).isoformat()
            if week in current["weekly_runs"] and week not in current["pending_weeks"]:
                current["pending_weeks"].append(week)
    if receipt.get("scan_kind") == "weekly":
        week = receipt["week"]
        coverage = weekly_ready(current, week, **(receipt.get('period') or {}))
        if not coverage['ready']:
            raise ValueError("Daily prerequisite for the weekly summary is not met.")
        run = {"receipt": ident, "at": datetime.now(ZONE).isoformat(),
               'from_time':coverage['from_time'],'through':coverage['through'],
               'project_scope':coverage['project_scope'], 'complete_period':coverage['complete_period'],
               'complete_scope_period':coverage['complete_scope_period']}
        previous = current['weekly_runs'].get(week, {})
        if coverage['project_scope'] is None:
            if previous.get('scoped_runs'):
                run['scoped_runs'] = previous['scoped_runs']
            current['weekly_runs'][week] = run
            current["pending_weeks"] = [w for w in current["pending_weeks"] if w != week]
        else:
            previous.setdefault('complete_period', False)
            previous.setdefault('scoped_runs', {})[digest(json.dumps(coverage['project_scope']).encode('utf-8'))] = run
            current['weekly_runs'][week] = previous
    current["receipts"][ident] = {"path": str(receipt_path), "sha256": digest(Path(receipt_path).read_bytes())}
    save(state_path, current)
    return {"committed_events": len(fresh), "batch_id": ident,
            "pending_count": len(current["pending_sessions"])}


def weekly_ready(current, week, from_time=None, through=None, timezone_name=None, project_scope=None):
    timezone_name = timezone_name or _config.zone_name()
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(timezone_name)
    monday = date.fromisoformat(week)
    if monday.weekday() != 0:
        raise ValueError("Week must be identified by its Monday date.")
    start = max(datetime.combine(monday,datetime.min.time(),zone),dt(AUTHORIZED))
    finish = datetime.combine(monday+timedelta(days=7),datetime.min.time(),zone)
    lo = dt(from_time) if from_time else start
    hi = dt(through) if through else min(datetime.now(zone),finish)
    if not start <= lo <= hi <= finish:
        raise ValueError('Period must lie in this week and authorized source range.')
    requested = normalize_project_scope(project_scope)
    windows = list(current.get('scan_windows', [])) + list(current.get('daily_scans', {}).values())
    gaps = []
    # Each requested project needs its own complete time coverage. Combining A's
    # first half with B's second half cannot prove either project is covered.
    for project in requested or [None]:
        ranges = sorted((max(lo,dt(w['from_time'])),min(hi,dt(w['through']))) for w in windows
                        if w.get('from_time') and dt(w['through'])>=lo and dt(w['from_time'])<=hi
                        and scope_covers(normalize_project_scope(w.get('project_scope')), project))
        cursor = lo
        for a,b in ranges:
            if a > cursor:
                gap = dict(from_time=cursor.isoformat(),through=a.isoformat())
                if project is not None: gap['project_scope'] = project
                gaps.append(gap)
            cursor = max(cursor,b)
        if cursor < hi:
            gap = dict(from_time=cursor.isoformat(),through=hi.isoformat())
            if project is not None: gap['project_scope'] = project
            gaps.append(gap)
    pending = [p for p in current.get('pending_sessions', []) if dt(p['first_ts'])<=hi
               and scopes_overlap(normalize_project_scope(p.get('project_scope')), requested)]
    ready = not gaps and not pending and hi>lo
    complete_scope_period = ready and lo==start and hi==finish
    return dict(week=week,ready=ready,
                from_time=lo.isoformat(),through=hi.isoformat(),timezone=timezone_name,
                project_scope=requested,complete_period=complete_scope_period and requested is None,
                complete_scope_period=complete_scope_period,
                gaps=gaps,pending_sessions=pending)


def due_weeks(current):
    today = datetime.now(ZONE).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    monday = sunday - timedelta(days=6)
    due = set(current["pending_weeks"])
    first = dt(AUTHORIZED).astimezone(ZONE).date()
    while sunday >= first:
        week = monday.isoformat()
        if not current['weekly_runs'].get(week,{}).get('complete_period',False):
            due.add(week)
        sunday -= timedelta(days=7)
        monday -= timedelta(days=7)
    return [weekly_ready(current, week) for week in sorted(due)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["init", "window", "seen", "commit", "health", "weekly-ready", "defer-week", "weekly-due"])
    parser.add_argument("--state", type=Path, default=DEFAULT)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--week")
    parser.add_argument('--from-time')
    parser.add_argument('--through')
    parser.add_argument('--timezone', default=_config.zone_name())
    parser.add_argument('--project-scope', nargs='+')
    args = parser.parse_args()
    with locked(args.state):
        current = load(args.state)
        if args.mode == "init":
            save(args.state, current)
            result = {"initialized": True, "version": 2, "state": str(args.state)}
        elif args.mode == "window":
            start = dt(AUTHORIZED)
            if current["last_successful_scan"]:
                start = max(start, dt(current["last_successful_scan"]) - timedelta(days=2))
            for pending in current["pending_sessions"]:
                start = min(start, max(dt(AUTHORIZED), dt(pending["first_ts"])))
            result = {"from_time": start.isoformat(), "to_time": datetime.now(ZONE).isoformat(),
                      "pending_sessions": current["pending_sessions"]}
        elif args.mode == "seen":
            if not args.events:
                raise ValueError("--events is required.")
            events = json.loads(args.events.read_text(encoding="utf-8"))
            if not isinstance(events, list):
                raise ValueError("Events must be a metadata list.")
            seen = set(current["processed_events"])
            fresh = []
            for event in events:
                key = fingerprint(event)
                if key and key not in seen:
                    seen.add(key)
                    fresh.append(dict(event, fingerprint=key))
            result = {"new_events": fresh, "new_count": len(fresh)}
        elif args.mode == "commit":
            if not args.receipt:
                raise ValueError("--receipt from journal_writer.py is required; note paths alone are insufficient.")
            result = commit_receipt(args.receipt, current, args.state)
        elif args.mode in {"weekly-ready", "defer-week"}:
            if not args.week:
                raise ValueError("--week is required.")
            result = weekly_ready(current, args.week, args.from_time,args.through,args.timezone,args.project_scope)
            if args.mode == "defer-week" and not result["ready"]:
                if args.week not in current["pending_weeks"]:
                    current["pending_weeks"].append(args.week)
                save(args.state, current)
        elif args.mode == "weekly-due":
            result = {"weeks": due_weeks(current)}
        else:
            queued = []
            foreign = []
            folder = plain_path(plain_path(args.state).parent / "queue")
            if folder.exists():
                for path in sorted(folder.glob("*.json")):
                    entry = json.loads(plain_path(path).read_text(encoding="utf-8"))
                    if not isinstance(entry, dict) or not {"status", "batch_id", "attempts"} <= entry.keys():
                        foreign.append(path.name)
                        continue
                    if entry["status"] not in {"committed", "superseded"} and entry["batch_id"] not in current["receipts"]:
                        queued.append({"batch_id": entry["batch_id"], "status": entry["status"],
                                       "attempts": entry["attempts"], "error": entry.get("error")})
            result = {"version": current["version"], "last_successful_scan": current["last_successful_scan"],
                      "daily_scans": current["daily_scans"], "processed_events": len(current["processed_events"]),
                      "receipts": len(current["receipts"]), "pending_sessions": current["pending_sessions"],
                      "pending_weeks": current["pending_weeks"], "pending_batches": queued,
                      "foreign_queue_files": foreign}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit("Journal state error: " + str(error))

