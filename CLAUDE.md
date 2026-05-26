# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Jupyter-notebook AP free-response auto-grader. It OCRs a student's scanned
handwritten exam answers, parses the official marking-scheme PDF into a
structured rubric, grades each answer against that rubric, and emits a
self-contained HTML scorecard with per-rubric-point evidence and reasoning.
Single-vendor stack (Google Gemini 3.x): **Gemini 3.1 Pro** does handwriting
OCR and **Gemini 3.5 Flash** does rubric extraction and grading — all via
structured output (Pydantic `response_schema`).

## Commands

```bash
# Install deps (Windows-friendly; PyMuPDF avoids the Poppler binary dep)
pip install -r requirements.txt        # or: uv pip install -r requirements.txt

# Run the pipeline: open grader.ipynb and "Run All".
# There is no CLI entrypoint and no test suite — the notebook IS the app.
```

There are no lint/build/test commands configured. `grader2.ipynb` and
`grader3.ipynb` are backup snapshots — `grader.ipynb` is the live notebook.

## Setup contract (what the notebook expects on disk)

- `.env` with **one** auth method (see `.env.example`):
  - `GEMINI_API_KEY=...` (AI Studio — preferred, simplest), **or**
  - `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` (Vertex AI).
  - `get_gemini_client()` prefers the API key and falls back to Vertex.
- One sub-folder per exam under `data/`, named with the subject **slug**
  (e.g. `data/calculus-bc/`), each holding three PDFs (all gitignored):
  - `questions.pdf` — typed exam questions (OCR **context only**, not transcribed)
  - `answers.pdf` — student's scanned handwriting (this is what gets transcribed)
  - `marking-scheme.pdf` — typed rubric / model answers (filename matched loosely)

  Every folder with all three PDFs is discovered and graded in one "Run All";
  each writes `out/<slug>_ai_scorecard.{html,json,md}`.

## Architecture & data flow

The notebook (`grader.ipynb`) is thin orchestration; all real logic lives in
modules so cells stay readable and primitives stay reusable.

```
config.py    SUBJECT_SLUG, SUBJECT_GRADING_ADDENDA, rubric_filename()
schemas.py   all Pydantic models (also serve as Gemini response_schema)
helpers.py   render_pdf_to_images, get_gemini_client, ocr_submission,
             load_rubric, flatten_rubric_by_subpart, grade_question,
             grade_questions_parallel, build_unattempted_scorecards,
             discover_exam_folders, grade_exam, assemble_scorecard,
             render_html_report, character_error_rate
prompts/     ocr.txt, rubric_extract.txt, grade_question.txt  (editable, read at call time)
```

Pipeline: the notebook calls `discover_exam_folders(data/)` to find every
subject folder with a full PDF set, then loops calling `grade_exam(...)` per
folder (a folder that errors is reported and skipped, not fatal). `grade_exam`
runs these four phases and returns a `Scorecard` plus the
`submission`/`answer_images` needed to render:

1. **OCR** (`ocr_submission`): question + answer images go in **one** Gemini
   call so it labels each transcript with the canonical question IDs from the
   question PDF — no separate segmentation pass. Returns `ParsedSubmission`
   (per-answer `confidence`, `source_pages`).
2. **Rubric parse** (`load_rubric`): renders marking-scheme PDF → Gemini →
   `ParsedRubric`, cached as a `{pdf}.parsed.json` **sidecar** next to the PDF.
   Subsequent runs read the sidecar and skip Gemini. Force a fresh parse by
   deleting the sidecar or passing `force_reparse=True`.
3. **Grade** (`grade_questions_parallel` → `grade_question`, one call per
   question, `temperature=0`): emits `QuestionScorecard` with per-point
   awarded/denied, quoted `transcript_evidence`, and `grading_confidence`.
   Rubric sub-parts with no transcribed answer are scored 0/max via
   `build_unattempted_scorecards`, then `assemble_scorecard` totals everything.
4. **Render** (`render_html_report`): self-contained HTML — answer page image
   (base64-embedded) on the left, graded rubric points with evidence on the
   right, plus an "Unattempted" section for the 0/max sub-parts. The notebook
   writes `out/<slug>_ai_scorecard.{html,json,md}`.

### The critical granularity invariant

The parsed rubric's top-level `QuestionRubric`s are keyed at **question** level
(`"1"`, `"2"`) but their `rubric_points` carry **sub-part** ids (`"1a"`,
`"1b"`), and OCR labels answers at sub-part granularity too. Matching answers
to the rubric at the top level yields an **empty intersection → 0/0 score**.
`flatten_rubric_by_subpart()` regroups the rubric to sub-part granularity so
the keys line up. `grade_exam` grades the intersection of `(rubric sub-parts ∩
answer sub-parts)`, scores rubric-only sub-parts 0/max, and raises if the
overlap is empty — that empty-overlap error is the first thing to check when a
score looks wrong (usually a question-ID formatting mismatch).

Even after `flatten_rubric_by_subpart`, the rubric and OCR can disagree on the
*depth* of sub-parts (rubric collapses `3a-i/ii/iii` under `3a`; rubric splits
`4` into `4a/4b/4c/4d`). Two helpers bridge that in `grade_exam` before
grading, and **both flag the bridged qids for human review** (their evidence
attribution is generated, not literal student labels):

1. `_synthesize_subpart_answers_from_parents` — rubric expects sub-parts but
   OCR returned a single parent block (student wrote one continuous response
   without sub-part labels). Each missing sub-part gets a copy of the parent
   transcript so it can be graded; the parent's now-redundant orphan card is
   suppressed in the HTML in favour of the per-sub-part cards.
2. `_synthesize_parent_answers_from_subparts` — rubric expects a parent qid
   but OCR returned per-sub-part blocks (student wrote labeled `(i)`, `(ii)`,
   `(iii)` for what the rubric grades as a single `3a`). Orphan children
   (OCR'd qids that aren't rubric entries) are folded into their most-specific
   rubric ancestor: a missing parent gets a synthesized answer from
   concatenated children; an existing parent gets the orphans appended after
   its own transcript (e.g. rubric `1e` keeps its sentence + appends the
   orphan `1e-ii` graph). Each sub-part block is prefixed with `[3a-i]`
   inside the merged transcript so the grader can attribute evidence per
   rubric point. The orphan child cards are suppressed in the HTML and the
   parent card carries a "merged from sub-parts" tag.

When debugging a low score, the order to check is: (a) qid casing mismatch
(both ingestion points lower-case via `_normalize_qid`); (b) granularity
mismatch (look for either recovery message in the run log: `Recovered N
sub-part(s)…` or `Merged N parent answer(s)…`); (c) genuine rubric/OCR drift
that neither helper covers — those land in `missing_qids` and score 0/max.

## Conventions specific to this repo

- **Editing the notebook:** the `.ipynb` is large because answer-page images
  are embedded in cell outputs, and `NotebookEdit`/`Read` both pull all of that
  into context. Instead, edit cell *sources* with a small Python script that
  loads the JSON, replaces the target cell's `source` (write new code to a
  `.txt` first to avoid `\n`-escaping hazards in f-strings), and writes back
  with `json.dump(..., indent=1, ensure_ascii=True)` + a trailing newline so
  untouched cells stay byte-identical. The `nbstripout` git filter strips
  outputs on commit, so the committed diff shows only source changes.
- **Subject extensibility:** the grader is subject-agnostic. To add a subject,
  extend `SUBJECT_SLUG` and (optionally) `SUBJECT_GRADING_ADDENDA` in
  `config.py`. The addendum text is injected into the grading prompt verbatim;
  this is where subject-specific rules live (e.g. Calc follow-through credit,
  CS-A functional-equivalence, Psych AAQ/EBQ dual-requirement). V1 target is AP
  Calculus BC; CS-A / Human Geography / Psychology are the generalization path.
- **Schema field descriptions are prompt-engineering surface.** `schemas.py`
  field `description=`s are sent to Gemini and materially affect output
  quality — edit them deliberately, not just for documentation.
- **Prompts are plain text files** read at call time, so you can iterate on
  `prompts/*.txt` without touching Python.
- **Fully automated, no human checkpoint.** OCR errors propagate silently into
  grades, so confidence is surfaced instead: per-question OCR confidence below
  `CONFIG["low_confidence_threshold"]` (default 0.75) flags every point with
  `review_recommended`, and low per-point `grading_confidence` adds a
  `review_flags` banner item in the report.
- **Never commit** `data/**/*.pdf`, `rubrics/*.pdf`, `*.parsed.json`, `out/*`,
  `.env`, or anything under `.secrets/` — all gitignored.
