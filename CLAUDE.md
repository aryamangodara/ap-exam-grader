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
- Three PDFs in `data/` (all gitignored):
  - `questions.pdf` — typed exam questions (OCR **context only**, not transcribed)
  - `answers.pdf` — student's scanned handwriting (this is what gets transcribed)
  - `marking-scheme.pdf` — typed rubric / model answers

## Architecture & data flow

The notebook (`grader.ipynb`) is thin orchestration; all real logic lives in
modules so cells stay readable and primitives stay reusable.

```
config.py    SUBJECT_SLUG, SUBJECT_GRADING_ADDENDA, rubric_filename()
schemas.py   all Pydantic models (also serve as Gemini response_schema)
helpers.py   render_pdf_to_images, get_gemini_client, ocr_submission,
             load_rubric, flatten_rubric_by_subpart, grade_question,
             render_html_report, character_error_rate
prompts/     ocr.txt, rubric_extract.txt, grade_question.txt  (editable, read at call time)
```

Pipeline (notebook cell order):

1. **OCR** (`ocr_submission`): question + answer images go in **one** Gemini
   call so it labels each transcript with the canonical question IDs from the
   question PDF — no separate segmentation pass. Returns `ParsedSubmission`
   (per-answer `confidence`, `source_pages`).
2. **Rubric parse** (`load_rubric`): renders marking-scheme PDF → Gemini →
   `ParsedRubric`, cached as a `{pdf}.parsed.json` **sidecar** next to the PDF.
   Subsequent runs read the sidecar and skip Gemini. Force a fresh parse by
   deleting the sidecar or passing `force_reparse=True`.
3. **Grade** (`grade_question`, one call per question, `temperature=0`): emits
   `QuestionScorecard` with per-point awarded/denied, quoted
   `transcript_evidence`, and `grading_confidence`.
4. **Render** (`render_html_report`): self-contained HTML — answer page image
   (base64-embedded) on the left, graded rubric points with evidence on the
   right. Also writes `.json` and a slim `.md` to `out/`.

### The critical granularity invariant

The parsed rubric's top-level `QuestionRubric`s are keyed at **question** level
(`"1"`, `"2"`) but their `rubric_points` carry **sub-part** ids (`"1a"`,
`"1b"`), and OCR labels answers at sub-part granularity too. Matching answers
to the rubric at the top level yields an **empty intersection → 0/0 score**.
`flatten_rubric_by_subpart()` regroups the rubric to sub-part granularity so
the keys line up. The grade-all cell prints a diagnostic (rubric sub-parts /
answer sub-parts / intersection) and raises if the intersection is empty —
this is the first thing to check when a score looks wrong. Only the
intersection of `(rubric sub-parts ∩ answer sub-parts)` is graded.

## Conventions specific to this repo

- **Editing the notebook:** use `NotebookEdit` (not `Edit`). You must `Read`
  the notebook first, and because `%autoreload 2` is on, re-`Read` after each
  edit before the next one. The `.ipynb` is ~28 MB because answer-page images
  are embedded in cell outputs — dump cell *sources* with a small Python script
  rather than reading the whole file when you only need the code.
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
- **Never commit** `data/*.pdf`, `rubrics/*.pdf`, `*.parsed.json`, `out/*`,
  `.env`, or anything under `.secrets/` — all gitignored.
