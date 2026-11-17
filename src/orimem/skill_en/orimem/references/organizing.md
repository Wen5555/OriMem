# Organizing notes and projects

Keeps the scope, organizing and acceptance conventions from before migration; ordinary recall does not pre-read this file. See [history.md](history.md) for the specific tool routing of historical lookups.

- First read the vault-level AGENTS.md and any AGENTS.md that applies to the target folder. When the user has already given the folder, material or operation scope, use it directly without asking again.
- Locate only the notes the task needs. Check file names and indexes first, then search related content with rg, and read the specified pages and the related passages actually needed; do not load the whole vault by default.
- Distinguish "read-only check", "suggestion/plan" and "perform the organizing". The first two write no files; once execution is explicitly requested, carry out ordinary safe reads and writes continuously, without waiting for "continue" at each stage.
- Before writing, look at existing notes and compare content. Prefer incremental updates to the original page; do not create a second copy of the same project, the same journal entry or the same knowledge. On a name collision, keep the original file and choose a clearly named new draft or propose a merge.
- For large changes or batch processing, first back up the original files and a manifest to a location next to the vault (outside it), without following links that point outside the vault. Before writing, check again whether the original file has changed; compare concurrent changes first to avoid losing new content.

## Recording conventions

Keep facts, decisions, personal understanding and AI suggestions separate. Do not infer experiences, commitments, current status, deadlines or results; leave unknown properties empty or omit them. The user confirming a "next step" does not mean confirming status as active.

Project records state results, decisions and concrete gaps directly, with sources at the end. Chat is one kind of evidence, not a precondition for later updates; when new facts, user explanations or specified materials arrive, update the original page. Process and technical acceptance go into the relevant existing records in `95 AI Workspace`; do not repeat a uniform statement in every note body, and do not create a new log for every small fix.

Do not rewrite source material. Interpretations based on the material, knowledge candidates and unreviewed understanding go into `00 Inbox/AI Drafts`, marked as pending review, with sources kept, and not written as the user's views. When the user explicitly asks for matters to be sorted, confirmed facts can directly update formal projects, journal entries and stage records, without leaving every result as a draft again.

Keep existing unknown YAML fields, wikilinks, attachment paths and source locators. Add only the properties the task needs; tags are a list. Date rules:

- Daily note: `50 Journal/Daily/YYYY-MM-DD.md`; the file name is the actual event date.
- Weekly review: `50 Journal/Weekly/<Monday date> Weekly Review.md`, Monday to Sunday; for an unfinished week, state up to which day it is recorded.
- created is the actual note creation date, updated is a supported modification date; backfilling does not fake journal entries as written in the past. Without support, omit optional dates.
- This week's and next week's priorities are confirmed by the user, at most three; do not backdate historical plans or create overdue items from past dates.
- Cross-period summaries go into `90 Archive` per existing conventions; the `50 Journal` root keeps navigation.

## Work by goal

### Sorting matters and updating projects

From the chats, files or explanations the user specified, extract results, decisions, blockers and unfinished items that actually happened, put them into daily records by event date, then summarize them into the corresponding weekly review. Prefer updating existing projects; finished stages go into the existing archive; archiving a stage does not mean the whole project is complete.

status, next_action and confirmed deadlines are maintained only on the project page; indexes only link, and the home page shows them through views. Write a clear next step into next_action; content that needs a user decision stays on the to-be-confirmed page. Paused projects keep their original status unless the user explicitly changes the decision; do not start engineering work, experiments or remote jobs because of it.

### Organizing materials and knowledge drafts

Process only the selected material. Material pages keep the real source entry point and supported metadata such as author/date; excerpts, paraphrases and interpretations are presented separately. For large local files, record the location first; do not copy project source code, full datasets or all PDFs by default.

Knowledge centers on one clear question and states the basis, method or explanation, applicability limits and sources. By default, reusable knowledge with sufficient evidence and no important unresolved conflict can be written automatically into `30 Knowledge`; record it as automatically summarized and do not fake that the user has reviewed it. Other valuable content lacking evidence goes into candidates, stating the conditions for promotion. Mark illustrative examples with their purpose and do not pass them off as real project implementations; do not generate articles from sources not actually read. Details in [lifecycle.md](lifecycle.md).

### Periodic review

Prefer reading the daily notes of the selected period and necessary project records, summarizing results, reasons for lack of progress and trade-offs; chats are used to fill in evidence, organized by actual date before summarizing. Keep an existing weekly review and add to it incrementally; do not use the source index as the summary body.

Clearly distinguish existing decisions from suggestions. Unconfirmed priorities are listed only as candidates, not written as formal tasks. Stage archiving and merging list the scope first; deletion, bulk moves/renames or restructuring require a concrete plan and user confirmation; when matching authorization already exists, do not ask again.

### In-vault checks

Check only the specified folders or notes for duplicate topics, broken file/heading links, property conflicts, stale navigation and source gaps. Do not treat illustrative links in template code blocks as broken. Report preset links-to-be-created and confirmed broken links separately; do not fix automatically unless requested.

When modifying .base files, check the current official syntax and actual fields, and keep existing filters and unknown configuration. Unknown status, AI drafts and examples must not mix into real active-project views; do not invent completion rates. File parsing and filter checks are not in-app rendering verification.

## Tool choice

Use local file tools for ordinary notes. When Obsidian Markdown or Bases formatting is complex, read the installed obsidian-markdown / obsidian-bases skill as needed; existing vault rules decide properties and content; do not copy project status and dates from upstream examples.

- Zotero: look up only the specified topic or item; read-only by default; do not index the whole library or automatically import attachments.
- Historical sources: use native history pagination and in-vault evidence per [history.md](history.md); source and execution boundaries stay unchanged.
- Public documentation search: send only public technical questions, not private notes, unpublished code or credentials.
- Obsidian API/MCP: use only when the current file, in-app search, opening notes or commands are explicitly needed; first confirm what operations are allowed this turn; do not expand scope because an interface exists.
- Do not install/configure services, enable sync, upload material or send messages on your own. Do not read or write .obsidian unless the current request explicitly involves that settings scope.

## Completion and delivery

Check UTF-8, YAML, core file/heading links, dates and target paths after modification. Existing lightweight tools may be used; do not install a new toolchain for acceptance; re-running the same input should skip already recorded items.

Deliver a concrete list of added/modified/skipped/conflicting items, the main differences, backup paths and how to restore. Distinguish file checks, real tool calls and app UI verification; describe unfinished parts concretely, and do not claim verification that was not performed has passed.

Skill files and AGENTS.md are behavioral guidance, not security isolation. Follow the current user authorization; do not modify or read unrelated private data.

## Ready-to-use requests

When invoking the OriMem skill, tasks can be described like this:

- Read-only check of a specified folder, listing duplicate topics, broken links and property conflicts, without modifying anything.
- Write this round's confirmed results into today's journal entry and incrementally update the specified project page, with sources at the end.
- Organize the specified material into one AI draft, keeping sources, for me to review.
- Summarize the daily records of a specified week, update the weekly review, list priorities only as candidates, and list the scope first before archiving.

Source evidence goes in `90 Archive/Conversations`, located by event month; AI drafts keep only actual knowledge candidates and interpretations awaiting organization. Folder migration, deletion or bulk restructuring requires explicit authorization for that scope; do not ask again for the same scope already confirmed. Do not write "source archived / indexed" as "knowledge has been summarized".

The knowledge index is organized by stable question topics and reading paths; stage/batch notes go into existing implementation records in `95 AI Workspace`, rather than appending historical batches indefinitely to knowledge navigation. Do not merge merely because topics or vectors are similar; relations such as requires/complements/contrasts must state the concrete fit or difference. Method references for projects/courses/long-term areas do not mean they have been adopted.

When splitting a registered claim, keep the mapping from original paragraph to target paragraph, source SHA and exact quote range. First write the target body text and the relocation note on the original page through the same writer, then bind the same receipt with revise_assessment, retire old dependencies item by item via retirements and check the current index; do not modify dependency SQL directly, and do not delete original evidence. Unchecked paragraphs stay; do not use subheading grouping to pass off a mixed-topic split as complete.

The daily writer's scope does not automatically expand to `20 Areas`; ordinary MCP/automatic recording interfaces refuse area writes. Maintenance of areas or other folders must be carried out separately under explicit authorization at that time, still using original hashes, backup, read-back and receipts.
