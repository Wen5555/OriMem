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


def initialize(backend, name, category="project", workspace=None):
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
        if workspace and props.get('local_path') and _normal_windows_path(workspace)!=_normal_windows_path(props['local_path']):
            return dict(path=rel,existing=True,binding_conflict='Workspace differs from the existing binding')
        record=None
        if workspace and not props.get('local_path'):
            markers=list(re.finditer(r'(?m)^---\r?$',content))
            if len(markers)<2:
                raise ValueError('Existing entry requires valid frontmatter')
            newline='\r\n' if '\r\n' in content else '\n'
            field='local_path: '+json.dumps(workspace,ensure_ascii=False)
            content=content[:markers[1].start()]+field+newline+content[markers[1].start():]
            headings=[line[2:] for line in content.splitlines() if line.startswith('# ')]
            if not headings:
                return dict(path=rel,existing=True,binding_conflict='Existing entry has no verifiable note heading')
            heading=headings[0]
            body=journal_writer.section_text(content,[heading])
            anchor=next((line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith('#')),None)
            if not anchor:
                return dict(path=rel,existing=True,binding_conflict='Existing entry has no paragraph for verified binding')
            plan=dict(batch_id='orion-bind-'+uuid.uuid4().hex,through=datetime.now(journal_state.ZONE).isoformat(),events=[],
                changes=[dict(path=rel,expected_sha256=digest(raw),content=content,
                    checks=[dict(section=[heading],text=anchor,events=[])])])
            # Existing user text remains byte-for-byte in the body. The check
            # anchors the entry heading while YAML validation checks the field.
            staged=prepare(backend,plan)
            record=commit(backend,staged['batch_id'],staged['prepared_sha256'])
            if journal_writer.properties(path.read_text(encoding='utf-8-sig')).get('local_path')!=workspace:
                raise ValueError('Initialized workspace metadata failed readback')
        index=backend.sync() if record is None else record.get('index')
        context=initialize_integration_hook(backend.db,backend.vault,name,category,workspace,entry_path=rel)
        return dict(path=rel,existing=True,index=index,record=record,context=context)
    content = (f"---\ntype: {category}\n"
               + 'title: '+json.dumps(name,ensure_ascii=False)+'\n'
               + 'orion_id: '+uuid.uuid4().hex+'\n'
               + f"created: {datetime.now(journal_state.ZONE).date()}\ntags:\n  - OriMem初始化\n")
    if workspace:
        content += 'local_path: '+json.dumps(workspace,ensure_ascii=False)+'\n'
    content += f"---\n\n# {name}\n\n## 初始化\n\n已创建知识入口，尚未填写成果或项目状态。\n"
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
                        section=[name, "初始化"],
                        text="已创建知识入口，尚未填写成果或项目状态。",
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
