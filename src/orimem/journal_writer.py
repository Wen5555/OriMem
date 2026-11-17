"""Compare, back up, write, reread and commit selected Markdown changes."""
from pathlib import Path
from datetime import datetime
import argparse
import json
import os
import re
import uuid
from orimem import journal_state as state
from orimem import config as _config
# Machine-specific one-off maintenance authorizations are not shipped; they stay empty.
ARCHIVE_REFERENCE_NOTES = set()
LEGACY_REFERENCE_NOTES = set()
LEGACY_RETIREMENT_NOTES = set()
LEGACY_NAVIGATION_NOTES = set()
RETIREMENT_NOTICE = '这些整理稿保留为历史快照，供回查来源编号、读取限制和当时的候选问题；当前进展、决定与知识以项目、日记和知识页为准。原文继续保留，退役不表示旧内容全部错误或已经完成逐会话全文核验。'

def body_after_properties(content):
    match=re.match(r'\A---\r?\n.*?\r?\n---(?:\r?\n|$)',content,re.S)
    if not match:raise ValueError('Explicit legacy maintenance requires existing frontmatter')
    return content[match.end():]

def validate_legacy_retirement(path,content,through):
    before=path.read_bytes().decode('utf-8-sig');old=properties(before);new=properties(content)
    added={'retrieval_basis','legacy_retired_at','legacy_retirement_reason'}
    if set(new)-set(old)-added or set(old)-set(new) or any(new[k]!=v for k,v in old.items()):
        raise ValueError('Retirement must preserve all existing YAML properties')
    if new.get('retrieval_basis')!='historical-only' or not isinstance(new.get('legacy_retirement_reason'),str) or not new['legacy_retirement_reason'].strip():
        raise ValueError('Retirement requires explicit historical-only disposition and reason')
    if state.dt(str(new.get('legacy_retired_at'))).date()!=state.dt(through).date():
        raise ValueError('Retirement date must be its actual writer batch date')
    if body_after_properties(before)!=body_after_properties(content):
        raise ValueError('Retirement cannot rewrite any legacy body, link or statistic')

def validate_legacy_navigation(path,content):
    before=path.read_bytes().decode('utf-8-sig')
    if properties(before)!=properties(content):raise ValueError('Navigation must preserve all metadata')
    if before==content:return
    comparable=content
    if path.name=='归档索引.md':
        notice=RETIREMENT_NOTICE+'\n\n'
        if comparable.count(notice)!=1:raise ValueError('Archive navigation needs the exact retained-history notice')
        comparable=comparable.replace(notice,'',1)
    pattern=r'\[\[[^\]\n]+\]\]';a=re.split('('+pattern+')',before);b=re.split('('+pattern+')',comparable)
    if len(a)!=len(b):raise ValueError('Only existing selected navigation links may be changed')
    old_target='90 归档/整理依据/整理依据索引'
    for i,(old,new) in enumerate(zip(a,b)):
        if old==new:continue
        if i%2==0:raise ValueError('Navigation prose is immutable outside the exact archive notice')
        target,sep,label=old[2:-2].partition('|')
        expected='[[90 归档/归档索引#整理依据'+sep+label+']]'
        if target.removesuffix('.md')!=old_target or new!=expected:
            raise ValueError('Only the reviewed legacy index to existing archive entry mapping is allowed')


def validate_archive_references(path, content, root):
    """Maintain selected snapshot references without rewriting any historical prose."""
    if not path.is_file():
        raise ValueError('Archive reference maintenance requires an existing note.')
    before = path.read_bytes().decode('utf-8-sig')
    pattern = r'(?:\[\[[^\]\n]+\]\])+'
    old_parts, new_parts = re.split('('+pattern+')', before), re.split('('+pattern+')', content)
    if len(old_parts) != len(new_parts):
        raise ValueError('Reference-only maintenance cannot add or remove reference positions.')
    for i, (old, new) in enumerate(zip(old_parts, new_parts)):
        if old == new:
            continue
        if i % 2 == 0 or not re.fullmatch(r'\[\[90 归档/整理依据/[^\]\n#|]+聊天来源索引#S\d{3}(?:\\?\|[^\]\n]+)?\]\]',old):
            raise ValueError('Archive historical prose and unrelated references are immutable here.')
        links = re.findall(r'\[\[([^\]\n]+)\]\]',new)
        if not links or ''.join('[['+x+']]' for x in links) != new:
            raise ValueError('Invalid replacement reference sequence.')
        visible = lambda x: x.split('|',1)[1] if '|' in x else x
        suffix = r'（旧编号(\d+)→当前位置(\d+)；原文支持核对）$'
        labels = [visible(x) for x in links]
        if ''.join(re.sub(suffix,'',x) for x in labels) != visible(old[2:-2]):
            raise ValueError('Reference labels must preserve the old visible wording.')
        old_events = re.search(r'事件\s*(\d+(?:[、,，]\s*\d+)*)', visible(old[2:-2]))
        expected_events = [int(x) for x in re.findall(r'\d+',old_events[1])] if old_events else []
        for position,link in enumerate(links):
            target = link.split('|',1)[0].rstrip('\\')
            note, marker, heading = target.partition('#')
            if not marker or not heading or not _config.under(note, _config.layout_path('evidence')):
                raise ValueError('Archive replacements require a precise Codex evidence heading.')
            annotation = re.search(suffix, labels[position])
            current_event = re.match(r'^(\d+) · ', heading)
            if annotation:
                if not current_event or int(annotation[2])!=int(current_event[1]) or int(annotation[1])==int(annotation[2]):
                    raise ValueError('Relocated citation label must match the actual current heading.')
                if len(expected_events)!=len(links) or int(annotation[1])!=expected_events[position]:
                    raise ValueError('Relocated citation label must match the explicit original event.')
            elif len(expected_events)==len(links) and current_event and expected_events[position]!=int(current_event[1]):
                raise ValueError('Different numeric locators require an explicit reviewed mapping label.')
            dest = state.note_path(note if note.endswith('.md') else note+'.md', root)
            if not dest.is_file() or len(re.findall(r'(?m)^#{1,6} '+re.escape(heading)+r'\s*$',dest.read_text(encoding='utf-8-sig'))) != 1:
                raise ValueError('Replacement evidence heading is missing or ambiguous.')



def properties(content):
    """Validate frontmatter with the already installed PyYAML; no installation."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}
    end = normalized.find("\n---", 4)
    if end < 0 or normalized[end + 4:end + 5] not in {"", "\n"}:
        raise ValueError("Frontmatter closing delimiter is missing.")
    try:
        import yaml
    except ImportError:
        raise ValueError("Existing YAML parser is unavailable; preserve this batch for verification.") from None
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError("Duplicate YAML property.")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        result = yaml.load(normalized[4:end], Loader=UniqueLoader)
    except (yaml.YAMLError, TypeError):
        raise ValueError("Invalid YAML frontmatter.") from None
    if not isinstance(result, dict):
        raise ValueError("Frontmatter must be a property mapping.")
    if "tags" in result and (not isinstance(result["tags"], list) or not all(isinstance(t, str) for t in result["tags"])):
        raise ValueError("Tags must be a list of strings.")
    return result


def section_span(content, address):
    if not isinstance(address, list) or not address or not all(isinstance(h, str) and h for h in address):
        raise ValueError("Checks need the full heading address as a nonempty list.")
    headings = []
    stack = []
    fence = None
    offset = 0
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            heading = re.match(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$", line.rstrip("\r\n"))
            if heading:
                level = len(heading.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading.group(2)))
                headings.append((level, [h for _, h in stack], offset, offset + len(line)))
        offset += len(line)
    matches = [h for h in headings if h[1] == address]
    if len(matches) != 1:
        raise ValueError("Heading address is missing or ambiguous.")
    level, _, start, body = matches[0]
    end = next((h[2] for h in headings if h[2] > start and h[0] <= level), len(content))
    return body, end


def section_text(content, address):
    body,end=section_span(content,address)
    return content[body:end]


def check_note(content, checks, eligible):
    if not isinstance(checks, list) or not checks:
        raise ValueError("Every note needs an actual paragraph check.")
    verified = []
    for check in checks:
        if set(check) != {"section", "text", "events"}:
            raise ValueError("A paragraph check requires section, text, events.")
        text = check["text"]
        if not isinstance(text, str) or not text.strip() or text not in section_text(content, check["section"]):
            raise ValueError("Expected fact is missing from its target section.")
        if not isinstance(check["events"], list) or not set(check["events"]) <= eligible:
            raise ValueError("Paragraph binding references unknown event metadata.")
        normalized = " ".join(text.split()).casefold()
        verified.append({"section": check["section"], "text_sha256": state.digest(text.encode("utf-8")),
                         "fact_hash": state.digest(normalized.encode("utf-8")), "events": check["events"]})
    return verified


def validate_plan(plan, root, authorized_control_notes=(), authorized_area_notes=(), authorized_archive_reference_notes=(), authorized_legacy_reference_notes=(), authorized_legacy_retirement_notes=(), authorized_navigation_notes=(), authorized_legacy_cleanup_notes=()):
    EXPLICIT_NOTES = set()

    def validate_cleanup(*_args):
        raise ValueError("Legacy cleanup maintenance is not available in OriMem")
    if set(authorized_legacy_cleanup_notes)-EXPLICIT_NOTES:
        raise ValueError('Only the explicitly authorized old-draft transfer and two reference notes are supported')
    if set(authorized_control_notes) - {'AGENTS.md'}:
        raise ValueError('Only the explicitly authorized vault AGENTS control note is supported.')
    if any(not _config.under(n, _config.layout_path('areas')) for n in authorized_area_notes):
        raise ValueError('Only explicitly selected research/study area entry maintenance is supported.')
    if set(authorized_archive_reference_notes) - ARCHIVE_REFERENCE_NOTES:
        raise ValueError('Only the explicitly selected legacy snapshots support reference maintenance.')
    if set(authorized_legacy_reference_notes) - LEGACY_REFERENCE_NOTES:
        raise ValueError('Only the named legacy reference notes support explicit maintenance.')
    if set(authorized_legacy_retirement_notes)-LEGACY_RETIREMENT_NOTES or set(authorized_navigation_notes)-LEGACY_NAVIGATION_NOTES:
        raise ValueError('Only the exact reviewed legacy retirement/navigation notes support maintenance.')
    required = {"batch_id", "through", "events", "changes"}
    optional = {"scan_kind", "scan_complete", "scan_from", "week", "pending", "resolved_sessions", "period"}
    if not isinstance(plan, dict) or not required <= plan.keys() or set(plan) - required - optional:
        raise ValueError("Unexpected or missing plan fields.")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", plan["batch_id"]):
        raise ValueError("Invalid batch ID.")
    bound = state.dt(plan["through"])
    if not state.dt(state.AUTHORIZED) <= bound <= datetime.now(state.ZONE) + state.timedelta(minutes=5):
        raise ValueError("Invalid scan bound.")
    if not isinstance(plan["events"], list) or not isinstance(plan["changes"], list):
        raise ValueError("Plan needs event metadata and selected note changes.")
    if not plan["changes"] and not (plan.get("scan_kind") == "daily" and plan.get("scan_complete") and not plan["events"]):
        raise ValueError("Only a complete empty daily scan may have no note changes.")
    keys = {state.fingerprint(event) for event in plan["events"]}
    if None in keys or any(state.dt(event["ts"]) > bound for event in plan["events"]):
        raise ValueError("Plan events fall outside the scan window.")
    if plan.get("scan_kind", "task") not in {"task", "daily", "weekly"}:
        raise ValueError("Invalid scan kind.")
    if plan.get('scan_kind')=='weekly':
        period = plan.get('period')
        if not isinstance(period,dict) or not {'from_time','through','timezone_name'} <= period.keys() or set(period)-{'from_time','through','timezone_name','project_scope'}:
            raise ValueError('Weekly write requires its explicit actual period and timezone.')
        if not state.dt(period['from_time']) <= state.dt(period['through']) <= bound:
            raise ValueError('Summary period must be within the actual scan bound.')
        state.normalize_project_scope(period.get('project_scope'))
    elif plan.get('period') is not None:
        period = plan['period']
        if plan.get('scan_kind') != 'daily' or not isinstance(period, dict) or not {'from_time','through','timezone_name'} <= period.keys() or set(period)-{'from_time','through','timezone_name','project_scope'}:
            raise ValueError('Explicit daily period requires its range, timezone and optional project scope.')
        from zoneinfo import ZoneInfo
        ZoneInfo(period['timezone_name'])
        state.normalize_project_scope(period.get('project_scope'))
        if not plan.get('scan_from') or state.dt(period['from_time']) != state.dt(plan['scan_from']) or state.dt(period['through']) != bound:
            raise ValueError('Daily period must match the actual scan_from and through bounds.')
    if type(plan.get("scan_complete", False)) is not bool:
        raise ValueError("scan_complete must be a boolean.")
    if plan.get("scan_kind") == "daily" and plan.get("scan_complete"):
        if not plan.get("scan_from") or not state.dt(state.AUTHORIZED) <= state.dt(plan["scan_from"]) <= bound:
            raise ValueError("A complete daily scan requires its actual scan_from bound.")
    if plan.get("scan_kind", "task") != "daily" and plan.get("scan_complete"):
        raise ValueError("Only daily scans may advance the source scan watermark.")
    state.merge_pending({"pending_sessions": []}, plan.get("pending", []), plan.get("resolved_sessions", []))
    paths = set()
    verified = []
    covered = set()
    for change in plan["changes"]:
        if set(change) != {"path", "expected_sha256", "content", "checks"}:
            raise ValueError("Each change requires path, expected_sha256, content, checks.")
        path = state.note_path(change["path"], root)
        parts = path.relative_to(root).parts
        control = change['path'] in authorized_control_notes and parts == ('AGENTS.md',)
        lay = _config.layout()
        rel = '/'.join(parts)
        area = change['path'] in authorized_area_notes and _config.under(rel, lay['areas']) and len(parts) == len(lay['areas'].split('/'))+1
        archive_reference = change['path'] in {*authorized_archive_reference_notes,*authorized_legacy_reference_notes}
        retirement=change['path'] in authorized_legacy_retirement_notes
        navigation=change['path'] in authorized_navigation_notes
        cleanup=change['path'] in authorized_legacy_cleanup_notes
        maintenance=archive_reference or retirement or navigation or cleanup
        if not any(_config.under(rel, folder) for folder in _config.writable()) and not control and not area and not maintenance:
            raise ValueError("Automatic writes are limited to projects, courses, knowledge, daily/weekly journals, "
                             "AI drafts, the AI workspace and conversation evidence (see `orimem config get layout`).")
        if _config.under(rel, lay['courses']) and (len(parts) != len(lay['courses'].split('/'))+2 or parts[-1] != lay['course_home']+'.md'):
            raise ValueError('Automatic course writes are limited to the course homepage; original materials are protected.')
        if cleanup:
            validate_cleanup(path,change['content'],root)
        if archive_reference:
            if change['expected_sha256'] is None:
                raise ValueError('Archive reference maintenance cannot create a snapshot.')
            validate_archive_references(path, change['content'], root)
        if retirement or navigation:
            if change['expected_sha256'] is None or not path.is_file():raise ValueError('Legacy maintenance cannot create or remove notes')
            if retirement:validate_legacy_retirement(path,change['content'],plan['through'])
            if navigation:validate_legacy_navigation(path,change['content'])
        if _config.under(rel, lay['journal']) and not (_config.under(rel, lay['daily']) or _config.under(rel, lay['weekly'])):
            if not cleanup:
                raise ValueError("Journals belong in the daily or weekly subfolder.")
        if path in paths:
            raise ValueError("Duplicate note path in plan.")
        paths.add(path)
        expected = change["expected_sha256"]
        if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("expected_sha256 must be the original byte hash or null for a new note.")
        if not isinstance(change["content"], str) or "\ufffd" in change["content"] or "\x00" in change["content"]:
            raise ValueError("Invalid Markdown text.")
        properties(change["content"])
        checks = check_note(change["content"], change["checks"], keys)
        covered.update(k for c in checks for k in c["events"])
        verified.append((path, change, checks))
    if not keys <= covered:
        raise ValueError("Every source event must be bound to a real fact paragraph.")
    return verified


def apply_plan(plan, state_path=state.DEFAULT, root=state.ROOT, authorized_control_notes=(), authorized_area_notes=(), authorized_archive_reference_notes=(), authorized_legacy_reference_notes=(), authorized_legacy_retirement_notes=(), authorized_navigation_notes=(), authorized_legacy_cleanup_notes=()):
    root = state.plain_path(root)
    state_path = state.plain_path(state_path)
    checked = validate_plan(plan, root, authorized_control_notes=authorized_control_notes,authorized_area_notes=authorized_area_notes,authorized_archive_reference_notes=authorized_archive_reference_notes,authorized_legacy_reference_notes=authorized_legacy_reference_notes,authorized_legacy_retirement_notes=authorized_legacy_retirement_notes,authorized_navigation_notes=authorized_navigation_notes,authorized_legacy_cleanup_notes=authorized_legacy_cleanup_notes)
    from orimem.core.lifecycle import register_write
    register_write(state_path.parent / 'orimem.db', plan)
    ident = plan["batch_id"]
    plan_hash = state.digest(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    queue_path = state.plain_path(state_path.parent / "queue" / (ident + ".json"))
    receipt_path = state.plain_path(state_path.parent / "receipts" / (ident + ".json"))
    with state.locked(state_path):
        current = state.load(state_path)
        previous = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else None
        if previous and previous["plan_sha256"] != plan_hash:
            raise ValueError("Batch ID already has different content; prepare a fresh batch.")
        if ident in current["receipts"]:
            if previous and previous["status"] != "committed":
                previous.update(status="committed", error=None, receipt=str(receipt_path))
                state.save(queue_path, previous)
            return {"batch_id": ident, "already_committed": True, "written": [], "committed_events": 0}
        entry = previous or {"batch_id": ident, "plan_sha256": plan_hash, "plan": plan, "attempts": 0}
        entry.update(status="pending", attempts=entry["attempts"] + 1, error=None)
        state.save(queue_path, entry)
        try:
            if plan.get("scan_kind") == "weekly":
                week = plan.get("week")
                if not week or not state.weekly_ready(current, week, **(plan.get('period') or {}))["ready"]:
                    raise ValueError("Daily prerequisite for this weekly summary has not succeeded.")
                expected_week = _config.weekly_note(week)
                if not any(c["path"].replace("\\", "/") == expected_week for _, c, _ in checked):
                    raise ValueError("Weekly receipt must verify the matching weekly note.")
            # Check ALL originals before changing ANY notes.
            for path, change, _ in checked:
                old = state.digest(path.read_bytes()) if path.exists() else None
                after = state.digest(change["content"].encode("utf-8"))
                if old not in {change["expected_sha256"], after}:
                    raise ValueError("Note changed since read: " + change["path"])
                if path.exists():
                    original_properties = properties(path.read_text(encoding="utf-8"))
                    if original_properties.keys() - properties(change["content"]).keys():
                        raise ValueError("An existing YAML property would be removed: " + change["path"])
            backup = _config.home() / "backups" / (datetime.now(state.ZONE).strftime("%Y%m%d") + "-" + ident)
            manifest_path = state.plain_path(backup / "manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
            written = []
            for path, change, _ in checked:
                data = change["content"].encode("utf-8")
                if path.exists() and path.read_bytes() == data:
                    continue
                before = path.read_bytes() if path.exists() else None
                if (state.digest(before) if before is not None else None) != change["expected_sha256"]:
                    raise ValueError("Note changed immediately before write: " + change["path"])
                snapshot = state.plain_path(backup / "snapshot" / path.relative_to(root))
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                if before is not None:
                    if snapshot.exists() and snapshot.read_bytes() != before:
                        raise ValueError("Backup already differs; preserve and prepare a fresh batch.")
                    if not snapshot.exists():
                        snapshot.write_bytes(before)
                    if snapshot.read_bytes() != before:
                        raise ValueError("Backup readback failed.")
                if not any(item["path"] == change["path"] for item in manifest):
                    manifest.append({"path": change["path"], "before_sha256": change["expected_sha256"],
                                     "after_sha256": state.digest(data), "new": before is None})
                state.save(backup / "manifest.json", manifest)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    with temporary.open("xb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    state.plain_path(path)
                    latest = state.digest(path.read_bytes()) if path.exists() else None
                    if latest != change["expected_sha256"]:
                        raise ValueError("Concurrent edit detected: " + change["path"])
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                written.append(change["path"])
            notes = []
            for path, change, _ in checked:
                data = path.read_bytes()
                if data != change["content"].encode("utf-8"):
                    raise ValueError("Write readback differs: " + change["path"])
                checks = check_note(data.decode("utf-8"), change["checks"],
                                    {state.fingerprint(e) for e in plan["events"]})
                notes.append({"path": change["path"], "sha256": state.digest(data), "checks": checks})
            receipt = {"version": 1, "batch_id": ident, "state_path": str(state_path), "root": str(root),
                       "through": plan["through"], "events": plan["events"], "notes": notes,
                       "verified_at": datetime.now(state.ZONE).isoformat(), "backup": str(backup) if manifest else None,
                       "scan_kind": plan.get("scan_kind", "task"), "scan_complete": plan.get("scan_complete", False),
                       "pending": plan.get("pending", []), "resolved_sessions": plan.get("resolved_sessions", [])}
            if plan.get("scan_from"):
                receipt["scan_from"] = plan["scan_from"]
            if plan.get("week"):
                receipt["week"] = plan["week"]
            if plan.get('period'):
                receipt['period'] = plan['period']
            state.save(receipt_path, receipt)
            result = state.commit_receipt(receipt_path, current, state_path, root)
            entry.update(status="committed", error=None, receipt=str(receipt_path))
            state.save(queue_path, entry)
            return dict(result, written=written, receipt=str(receipt_path), backup=receipt["backup"])
        except (ValueError, OSError, KeyError, TypeError) as error:
            entry.update(status="failed", error=str(error))
            state.save(queue_path, entry)
            raise


def supersede_batch(batch, replacement, state_path=state.DEFAULT, root=state.ROOT):
    for ident in (batch, replacement):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", ident):
            raise ValueError("Invalid batch ID.")
    with state.locked(state_path):
        current = state.load(state_path)
        if replacement not in current["receipts"]:
            raise ValueError("Replacement batch must already be committed.")
        receipt_path = state.plain_path(state_path).parent / "receipts" / (replacement + ".json")
        receipt = state.verify_receipt(receipt_path, state_path, root)
        path = state.plain_path(state_path.parent / "queue" / (batch + ".json"))
        queued = json.loads(path.read_text(encoding="utf-8"))
        if queued["status"] == "committed":
            raise ValueError("Committed batches cannot be superseded.")
        eligible = {state.fingerprint(e) for e in queued["plan"]["events"]}
        covered = {state.fingerprint(e) for e in receipt["events"]}
        if not eligible <= covered:
            raise ValueError("Replacement does not cover the original source events.")
        for change in queued["plan"]["changes"]:
            note = state.note_path(change["path"], root)
            check_note(note.read_text(encoding="utf-8"), change["checks"], eligible)
        queued.update(status="superseded", replacement=replacement, error=None)
        state.save(path, queued)
        return {"batch_id": batch, "superseded_by": replacement}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["apply", "retry", "supersede"])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--batch")
    parser.add_argument("--replacement")
    args = parser.parse_args()
    if args.mode == "supersede":
        if not args.batch or not args.replacement:
            raise ValueError("--batch and --replacement are required.")
        print(json.dumps(supersede_batch(args.batch, args.replacement), ensure_ascii=False, indent=2))
        return
    if args.mode == "apply":
        if not args.plan:
            raise ValueError("--plan is required.")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    else:
        if not args.batch or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", args.batch):
            raise ValueError("A valid --batch is required.")
        path = state.plain_path(state.DEFAULT.parent / "queue" / (args.batch + ".json"))
        plan = json.loads(path.read_text(encoding="utf-8"))["plan"]
    print(json.dumps(apply_plan(plan), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit("Journal writer error: " + str(error))
