"""MCP record preparation/commit delegates exclusively to the shared writer."""

from pathlib import Path
from datetime import datetime
import json, re, difflib, uuid
from orimem import journal_writer, journal_state
from orimem import config as _config
from .backend import digest
from .content_policy import local_content as sanitize


def prepare(backend, plan):
    if len(json.dumps(plan, ensure_ascii=False)) > 2_000_000 or len(plan.get("changes", [])) > 30:
        raise ValueError("Record batch exceeds budget")
    journal_writer.validate_plan(plan, backend.vault)
    folder = backend.db.parent / "prepared"
    folder.mkdir(exist_ok=True)
    content = json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2)
    sha = digest(content)
    ident = plan["batch_id"]
    file = folder / (ident + ".json")
    if file.exists() and file.read_text(encoding="utf-8") != content:
        raise ValueError("Prepared batch ID already has different content")
    file.write_bytes(content.encode("utf-8"))
    previews = []
    for change in plan["changes"]:
        p = backend.path(change["path"])
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        diff = "\n".join(
            difflib.unified_diff(old.splitlines(), change["content"].splitlines(), n=2)
        )
        previews.append(
            dict(path=change["path"], diff=diff[:2000], diff_truncated=len(diff) > 2000)
        )
    return dict(batch_id=ident, prepared_sha256=sha, changes=previews)


def commit(backend, batch_id, prepared_sha256):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", batch_id):
        raise ValueError("Invalid batch ID")
    file = backend.db.parent / "prepared" / (batch_id + ".json")
    if digest(file.read_bytes()) != prepared_sha256:
        raise ValueError("Prepared batch changed")
    try:
        result = journal_writer.apply_plan(
            json.loads(file.read_text(encoding="utf-8")),
            state_path=backend.state_path,
            root=backend.vault,
        )
        backend.lifecycle.health_stage('write')
    except Exception as error:
        backend.lifecycle.health_stage('write',error)
        raise
    # A successful receipt precedes indexing; failure leaves a recoverable pending index.
    try:
        result["index"] = backend.sync()
    except Exception as e:
        backend.last_error = sanitize(str(e))
        backend.lifecycle.health_stage('index',e)
        result["index_pending"] = backend.last_error
    return result


TEMPLATE = {
    "zh": ("初始化", "已创建知识入口，尚未填写成果或项目状态。", "OriMem初始化", "OriMem自动创建"),
    "en": ("Setup", "Entry created; no results or project status recorded yet.", "OriMem-setup", "OriMem-auto"),
}


def _entry_paths(props):
    from .task_context import entry_local_paths
    return entry_local_paths(props)


def _set_frontmatter_field(content, key, value):
    """Replace or insert one single-line YAML field (JSON-style value) in the frontmatter."""
    markers = list(re.finditer(r'(?m)^---\r?$', content))
    if len(markers) < 2 or markers[0].start() != 0:
        raise ValueError('Existing entry requires valid frontmatter')
    newline = '\r\n' if '\r\n' in content else '\n'
    head, rest = content[:markers[1].start()], content[markers[1].start():]
    line = key + ': ' + json.dumps(value, ensure_ascii=False)
    pattern = re.compile(r'(?m)^' + re.escape(key) + r':[^\r\n]*(?:\r?\n(?:[ \t]+-[^\r\n]*))*')
    if pattern.search(head):
        head = pattern.sub(lambda m: line, head, count=1)
    else:
        head = head + line + newline
    return head + rest


def _verified_bind(backend, rel, raw, content, path, check):
    headings = [line[2:] for line in content.splitlines() if line.startswith('# ')]
    if not headings:
        return None, 'Existing entry has no verifiable note heading'
    heading = headings[0]
    body = journal_writer.section_text(content, [heading])
    anchor = next((line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith('#')), None)
    if not anchor:
        return None, 'Existing entry has no paragraph for verified binding'
    plan = dict(batch_id='orion-bind-' + uuid.uuid4().hex, through=datetime.now(journal_state.ZONE).isoformat(), events=[],
                changes=[dict(path=rel, expected_sha256=digest(raw), content=content,
                              checks=[dict(section=[heading], text=anchor, events=[])])])
    # Existing user text remains byte-for-byte in the body. The check
    # anchors the entry heading while YAML validation checks the field.
    staged = prepare(backend, plan)
    record = commit(backend, staged['batch_id'], staged['prepared_sha256'])
    if not check(journal_writer.properties(path.read_text(encoding='utf-8-sig'))):
        raise ValueError('Initialized workspace metadata failed readback')
    return record, None


def _similar_entries(backend, name, rel):
    """Existing entries whose title/alias matches the requested name (case/width-insensitive)."""
    from .task_context import resolve_task_context
    try:
        found = resolve_task_context(backend.db, backend.vault, '', name, authorized_scope=_config.project_scopes(),
                                     budget_chars=256)
    except ValueError:
        return []
    if found.get('status') == 'matched':
        hits = [dict(path=found['entry']['path'], title=found['entry'].get('title'))]
    else:
        hits = [dict(path=c['path'], title=c.get('title')) for c in found.get('candidates', [])]
    return [h for h in hits if h['path'] != rel]


def initialize(backend, name, category="project", workspace=None, also_bind=False, force_new=False, origin="manual"):
    from .task_context import initialize_integration_hook, _normal_windows_path
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 100
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or name.endswith((".", " "))
    ):
        raise ValueError("Valid note name required")
    if category not in {"project", "course", "other"}:
        raise ValueError("Invalid category")
    if workspace and (not isinstance(workspace, str) or len(workspace) > 500):
        raise ValueError("Invalid workspace metadata")
    lay = _config.layout()
    rel = f"{lay['courses']}/{name}/{lay['course_home']}.md" if category == 'course' else f"{lay['projects']}/{name}.md"
    path = backend.path(rel)
    if path.exists():
        raw=path.read_bytes()
        content=raw.decode('utf-8-sig')
        props=journal_writer.properties(content)
        if props.get('type')!=category:
            return dict(path=rel,existing=True,binding_conflict='Existing note has a different category')
        bound = _entry_paths(props)
        record = None
        if workspace and bound and not any(_normal_windows_path(workspace) == _normal_windows_path(p) for p in bound):
            if not also_bind:
                return dict(path=rel, existing=True, bound_paths=bound,
                            binding_conflict='Workspace differs from the existing binding',
                            hint='If the user confirms this folder belongs to the same project, call again with also_bind=true.')
            extra = [p for p in bound if p != props.get('local_path')] + [workspace]
            content = _set_frontmatter_field(content, 'local_paths', extra)
            record, conflict = _verified_bind(backend, rel, raw, content, path,
                                              lambda pr: workspace in _entry_paths(pr))
            if conflict:
                return dict(path=rel, existing=True, binding_conflict=conflict)
        elif workspace and not bound:
            content = _set_frontmatter_field(content, 'local_path', workspace)
            record, conflict = _verified_bind(backend, rel, raw, content, path,
                                              lambda pr: pr.get('local_path') == workspace)
            if conflict:
                return dict(path=rel, existing=True, binding_conflict=conflict)
        index=backend.sync() if record is None else record.get('index')
        context=initialize_integration_hook(backend.db,backend.vault,name,category,workspace,entry_path=rel)
        return dict(path=rel,existing=True,index=index,record=record,context=context)
    similar = [] if force_new else _similar_entries(backend, name, rel)
    if similar:
        return dict(created=False, similar_entries=similar[:5],
                    hint='An entry with a matching name or alias exists. Ask the user: bind this folder to it '
                         '(initialize with that entry name and also_bind=true) or create a new one (force_new=true).')
    section, placeholder, tag, auto_tag = TEMPLATE.get(_config.layout()['language'], TEMPLATE['en'])
    tag = auto_tag if origin == 'auto' else tag
    content = (f"---\ntype: {category}\n"
               + 'title: '+json.dumps(name,ensure_ascii=False)+'\n'
               + 'orion_id: '+uuid.uuid4().hex+'\n'
               + f"created: {datetime.now(journal_state.ZONE).date()}\ntags:\n  - {tag}\n")
    if workspace:
        content += 'local_path: '+json.dumps(workspace,ensure_ascii=False)+'\n'
    content += f"---\n\n# {name}\n\n## {section}\n\n{placeholder}\n"
    plan = dict(
        batch_id="orion-init-" + uuid.uuid4().hex,
        through=datetime.now(journal_state.ZONE).isoformat(),
        events=[],
        changes=[
            dict(
                path=rel,
                expected_sha256=None,
                content=content,
                checks=[
                    dict(
                        section=[name, section],
                        text=placeholder,
                        events=[],
                    )
                ],
            )
        ],
    )
    staged = prepare(backend, plan)
    record=commit(backend, staged["batch_id"], staged["prepared_sha256"])
    return dict(path=rel,record=record,context=initialize_integration_hook(
        backend.db,backend.vault,name,category,workspace,entry_path=rel))
