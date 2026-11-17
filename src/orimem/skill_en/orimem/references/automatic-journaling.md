# Write-back contract for automatic recording

Read this file only when the task involves automatic recording, gap-filling scans or repairing write-back. Authorization, source scope and daily/weekly responsibilities follow the vault-level AGENTS.md.

Ordinary recall locates knowledge in a targeted way per [retrieval.md](retrieval.md); history gaps use already merged body text and the native history interfaces per [history.md](history.md); do not generate two copies of context in parallel. Historical results cannot replace current verification. When relevant, briefly state in the delivery which lesson was referenced and its applicability limits; do not create a new log for every retrieval.

## Confirmed results after a task ends

First extract actual results, decisions, concrete blockers and confirmed next steps, and compare with the original page. For existing facts, only add necessary sources; do not append duplicates. Project status and next steps are maintained only on the project page; events go into the journal by actual date in the configured timezone; knowledge with clear evidence, clear limits and long-term reuse value can by default be formed or merged automatically, and only conflicts or missing evidence stay as candidates; see [lifecycle.md](lifecycle.md).

Automatic write-back always goes through the shared writer, covering projects, journal, candidates, `30 Knowledge` and authorized evidence: MCP `prepare_record`(plan) returns the diff and prepared_sha256, and `commit_record`(batch_id, prepared_sha256) calls the same writer; the equivalent CLI entry point is `orimem write ...`. The agent reviews the diff within existing authorization, without asking the user for separate confirmation each time. Before writing body text, an index job is persistently registered; after a successful receipt, indexing is incremental; index failures are redone separately and the whole chain cannot be marked complete. The input is a local JSON change batch; do not put whole chats or credentials into it. When reading an old file, compute the SHA-256 of the **raw bytes**, then generate complete new content that keeps existing fields, sources and user paragraphs. Check the incremental diff before writing; do not use ellipses in place of source text, and do not use this tool for unauthorized deletion or restructuring.

Batch structure example (illustration only; use real content and times):

```json
{
  "batch_id": "task-unique-id",
  "through": "YYYY-MM-DDTHH:MM:SS+08:00",
  "scan_kind": "task",
  "scan_complete": false,
  "events": [],
  "changes": [
    {
      "path": "00 Inbox/AI Drafts/Example.md",
      "expected_sha256": null,
      "content": "---\ntype: example\ntags:\n  - example\n---\n\n# Example\n\n## Result\n\nExample, can be deleted.\n",
      "checks": [
        {"section": ["Example", "Result"], "text": "Example, can be deleted.", "events": []}
      ]
    }
  ]
}
```

- batch_id stays the same across retries. The same ID must not change content; a re-merged version uses a new ID.
- expected_sha256 is the 64-character lowercase hash from reading the original page; only new files use null.
- content is the complete new note. A check's text is the real complete sentence to verify; section is the unique full parent-child heading address, not just the H2.
- events accept only session_id, ts, role and event_index or turn_id, optionally content_hash. App turn times are converted from Unix seconds to timezone-aware times; final_answer uses the completed turn time and keeps turn_id. Sources still go at the end of the note.
- Use the writer state's seen query to get fingerprints; fill checks.events with the corresponding fingerprints, and bind each new source event to at least one fact. Fingerprints from seen output must not be put back into the metadata items of events.
- Artifacts already accepted within the task may also use events=[], keeping the real file or current request basis at the end of the note; do not fabricate session locators. Source-scan gap-filling should use real event metadata.
- pending is a list of session_id, first_ts, reason; reason states only a short gap, not body text. resolved_sessions lists only session IDs confirmed as fully handled; other items awaiting retry are kept automatically.

The writer executes within the same OS lock: check all original versions → backup outside the vault → write → read back and check target paragraphs → save receipt → register deduplication. The receipt binds events, note path, heading, paragraph hash and post-write file hash. After writing, use `health` to check queue and index status.

Before writing, frontmatter is parsed; duplicate fields, invalid YAML, non-list tags and merges that lose original properties are rejected. When the parser is unavailable, the batch is kept; dependencies are not installed automatically, and acceptance is not skipped.

state.json and receipts store only metadata/hashes. The queue stores the selected change content, which may include private notes; it stays only on this machine and is not uploaded, committed to Git or used as instructions for future tasks. Backups outside the vault use the batch ID; failed retries reuse the original copy rather than creating duplicates.

## Daily scan and weekly summary

Scheduled daily/weekly organizing is controlled by `daily_summary.enabled`: the default value `"ask"` means ask the user once whether to enable it, treated as off until agreed; it can also be `true` or `false`. View it with `config_get`, change it with `config_set` after the user agrees; once enabled it is executed by `orimem daily run`. When not enabled, organizing can still be done manually on user request, with the same rules.

Project-scoped periodic scans provide `from_time,through,timezone_name,project_scope` in the plan's `period`; `project_scope` is a non-empty list of in-vault relative paths, folders cover their subpaths on path boundaries, and only omitting it means all authorized ordinary sources. A daily `period` start and end must match `scan_from/through`. A complete project-scoped scan saves only that scope's successful window and does not advance the global scan watermark; a weekly review checks the whole time interval of each requested project one by one, and cannot piece together coverage from project A and B each covering half. Pending sources with known scope are recorded with `pending[].project_scope`; pending sources with unknown scope conservatively block, and repeated failures keep the unresolved scope.

Full-week completion for scoped projects and full-week completion for all sources are recorded separately; scoped project results do not clear globally pending weeks. A weekly-ready check can provide from_time, through and project_scope together (e.g. `"10 Projects/Project Page.md"`). Closing a single material task only means that material has a correctly dated note and receipt; it cannot be used to claim the whole day's scan is complete.

1. Use health and window to view batches awaiting retry, windows and pending, then get sources through `list_sessions`/`get_session_messages`. Failed batches are retried at most once per round.
2. Filter events with seen, and compare facts with existing notes by topic; semantically identical duplicate reports reuse existing paragraphs as checks without appending duplicates. Fingerprints only handle message duplication and cannot judge whether different wording is the same fact. Candidate review tasks after source discovery are handled per the candidate_tasks/claim/read/ack flow in [lifecycle.md](lifecycle.md); when new evidence is insufficient, save only the reasoning; when quality conditions are met, promote automatically through the same writer, index and revise_assessment.
3. Daily batches use scan_kind=daily. Set scan_complete=true only when pagination is complete, coverage of the required readable sources has been checked and write acceptance succeeded, and fill in the real scan_from and through. On truncation or tool failure use false, keep pending, and do not advance the scan watermark.
4. When a complete scan finds no new facts, you can use changes=[], events=[] and register only the scan receipt, without creating blank journal entries or "no update" logs. Do not bind ignored small talk etc. to fictional facts.
5. The daily task handles project and journal gap-filling for actual dates, and judges formal knowledge or candidates by reuse value; material with stage significance is registered as periodic review material; facts already written immediately are only referenced, not appended again. Use weekly-due to check weeks that have ended but were missed for summarizing; fill in the summary only when ready=true for the actual interval. The existing weekly task summarizes the journal, necessary projects and targeted evidence, and does not routinely rescan all chats.
6. weekly-ready checks, for this from_time, through and timezone, the union of successful scan windows, in-scope pending sources and gaps. When preconditions are not met, defer-week registers the pending week and keeps the original review; once gaps are covered later the summary can be filled in. For an unfinished period, record only the actual range; do not treat stage coverage as a complete week. A fixed clock time is not a completion condition.
7. Weekly batches use scan_kind=weekly, week=<Monday date>, scan_complete=false, and provide a period consistent with the coverage check; check the corresponding weekly file and source bindings. Write only follow-up priorities that are supported; do not infer personal commitments. When later facts appear for an already summarized week, the write-back marks it pending again, and the same matter reuses the main record and accepted paragraphs.

Status queries such as window, weekly-ready, defer-week, weekly-due, and batch retry and supersede (replacing a conflicting batch with a new successfully merged batch) are provided by the same writer and invoked through the `orimem` CLI; see its help output for exact parameters.

On concurrency conflicts, re-read the original page, merge confirmed facts and generate a new batch. Do not automatically roll back partial writes, to avoid overwriting later modifications; unregistered events can still be gap-filled. When retries fail, sources are unreadable or conflicts cannot be clearly merged, report the specific gap; do not loop. The shared lock only constrains writes through the same writer; Obsidian, sync software or manual edits do not take part in the lock; pre-write hashes and post-write read-back detect changes; do not claim absolute isolation from arbitrary external writes.

Finally, distinguish script tests, real write-back and scheduled triggering. Scan claims are checked by the executing task; receipts prove that specified content entered the note, but cannot replace factual review or prove that all chats were read.

## Period range

weekly-ready supports from_time, through and timezone; provide the actual start and end of the range to organize this time, without relying on a fixed clock time. By default it checks real coverage from the authorized start of this week to the moment of the call; a scan covering only the last moment cannot pass for the whole interval. When the union of successful scan windows has gaps, or there are in-scope pending sources, complete closure is refused.

A weekly write-back plan provides a `period` object with fields `from_time,through,timezone_name,project_scope(optional)`, consistent with the check parameters; the receipt freezes this range. When the current week has not ended, write a stage review for the range actually read, keep recorded_through, and do not register stage coverage as the end of the full week. Later facts update the original date and make the original period pending again. The scheduled time is configured by the user; do not change it on your own.

## Source staging and explicit archiving

Ordinary messages are first saved as observed source versions by `orimem capture` (`capture.enabled`, on by default), and an independent projection job continuously updates the stable transcripts in `90 Archive/Conversations` through the same writer, without waiting for stage or daily/weekly organizing. Projection batches can carry authorized source text; events=[] does not pose as a fact record; checks exactly cover the program body text, and source_projections separately bind source version and receipt. Unchanged input writes no body text; conflicts are retried a bounded number of times and the source is kept; nothing is written as a session page in the drafts folder.

Daily/weekly scans still read archived/pending by actual message time and compare with existing facts. Summarized results are written to projects/journal through the same writer; knowledge with sufficient evidence and boundaries can be created or merged into `30 Knowledge`, while missing evidence or conflicts stay as candidates. Old snapshots keep their historical meaning; whether a continuous transcript is complete is judged by projection receipts, not by compatibility pagination state. Local content is not additionally redacted; credential files, third-party rosters and source authorization boundaries still apply.

## Citation maintenance for authorized historical snapshots

Migrating old index wikilinks to exact message headings in `90 Archive/Conversations` is explicit maintenance, performed only on existing notes listed item by item in the authorization; it does not open whole-folder writes to the inbox, materials or raw sources; ordinary automatic write-back/MCP does not expose this maintenance scope.

Before calling, actually read the corresponding source text and check the cited fact, role, real message time and source version; numeric numbering and same-ID mapping only produce candidates. When an old summary has no frozen complete source gold standard, report per-item support verification; do not claim full-text equivalence of the whole session. The denominator of the original citation list is kept; entries found by expanded scanning are registered separately; a newly added scope cannot replace the original list or hide unmigrated citations.

Maintenance may only change old index wikilinks to message headings that exist and are unique, keeping display text, historical body text and unrelated links, and requires the existing file and its original hash. Multiple events link to their respective messages separately. Deletion, moves or body reorganization still obey current authorization and their own acceptance; citation maintenance cannot be used to modify historical facts.

When the original number does not exist in the current source, the candidate body text must actually be read and the original cited fact checked; choosing a different number also requires explicitly providing the current number, role, real time, message version, source file SHA and the basis for selection. prepare does not guess automatically by last message or nearest neighbor; the writer keeps the original display text and only allows appending a "(old number X → current position Y; source support checked)" note consistent with the original number and the actual target heading. This note indicates a citation migration checked against the body text; it does not prove the original numeric identity or full-text equivalence of the old session; missing sources and the unread list remain in the original denominator.

If an ordinary project page has explicitly listed multiple events next to the same single-source link, after checking each one the display may be supplemented with each message number, keeping the body text, the original event list and project status; this does not expand the explicit maintenance scope for historical snapshots, the inbox or materials. Without explicit events, or when multiple sources are listed side by side, do not guess on that basis.

When an original session-level link has no message number, after actually reading the source you may explicitly select the current message that supports the purpose of that citation, using the same role/time/version/file SHA binding as above. old_event stays null, display text is unchanged, and the target is the exact message heading; do not forge an old number, and do not upgrade support from one message into equivalence with the whole session or all facts. Unread sources such as third-party rosters stay as they are and cannot be migrated automatically by the same session ID.

## In-place historical retirement of old organized drafts

In-place retirement only adds `retrieval_basis: historical-only`, a legacy_retired_at with the actual batch date, and a reason to explicitly listed old organized drafts; the original body text, wikilinks, statistics and all existing YAML property values must stay unchanged. It does not delete or move history, and does not register a semantic retraction. Navigation page maintenance is limited to changing links to the old organizing basis to the corresponding section of the existing archive index, keeping tags/body/properties. Ordinary MCP/automatic interfaces do not expose this maintenance scope, and it cannot be used to open raw materials or arbitrary archives to modification.

Before in-place retirement, actually check the unique content against the current successor page, keeping all historical material and a raw-byte backup; unproven complete-session equivalence, truncation, candidates and unadopted suggestions are still kept. After write-back, accept file by file the source text/links/properties/receipt/index and a zero-write replay, and also test current blocking and historical retention. Physical deletion, bulk moves and folder restructuring still require submitting a concrete list for confirmation first; do not treat in-place retirement as deleted or as an approved move.

When the user explicitly authorizes cleaning up old drafts, first carry over unique body text and original properties, migrate citations, complete the raw-byte backup outside the vault and the receipt, then delete one by one by fixed path; manually retained pages can go into `90 Archive/Legacy`, without wikilinks in the body, keeping only an entry point in the index. Old drafts, old numbers and statistics are not changed into current facts, and raw conversations are not rewritten. Ordinary automation cannot use this to move other material by date, orphan status or similarity. The retention conversion compares against the existing full version and only changes wikilink syntax, the manual-retention note and explicit index properties; other fields/content are kept as in the backup.

Ordinary Markdown can explicitly set `orion_index: false` for manual retention; index reconciliation removes its notes/chunks/edges candidates; the source text is still in Obsidian. Source code and projection are separate; this field cannot be used to claim the capture policy for canonical raw conversations has changed. Writer receipt closure checks the actual source SHA, the explicit exclusion property and absence from the index; deliberate exclusion must not be treated as failure, and an unindexed page cannot complete a write-pending formal knowledge claim. Example/frozen-property wikilinks inside code fences do not create graph relations; a relation-parsing generation upgrade only reconciles derived edges and does not recompute vectors for unchanged text.

The whole `90 Archive/Legacy` folder is also excluded from Markdown retrieval; manual old drafts placed there later are not indexed automatically even if they do not yet have the `orion_index` property. This folder is separate from the ordinary `90 Archive/Conversations` sources. The index page is only a manual Obsidian entry point and does not expand automatic retrieval or summarizing scope.

Ordinary source saving and writer projection are handled by `orimem capture`; the retrieval backend does not capture again. A running capture process only proves saving within the observation window; it cannot be used to claim daily/weekly distillation is complete, nor does it mean login autostart, abnormal restart, sleep and long-term unattended operation have been verified.

Batches still go through the shared lock, backup outside the vault, exact paragraph read-back, receipt and index verification. Failed rewrites use the originally prepared batch and the same maintenance scope; a general retry cannot expand the scope. Re-verification reports are saved to new files; original receipts and backups are kept.
