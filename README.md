# AP FRQ Auto-Grader

Grade a student's **handwritten** AP free-response answers against the official
College Board marking scheme — automatically, with per-rubric-point evidence
and reasoning.

The whole pipeline runs in a single Jupyter notebook on a single AI vendor
(**Google Gemini 2.5 Pro**), which does the handwriting OCR, the rubric
extraction, and the grading.

```
questions.pdf ─┐
answers.pdf  ──┼─▶ Gemini OCR ─▶ Gemini rubric parse ─▶ Gemini grading ─▶ HTML / JSON scorecard
marking-scheme.pdf ─┘            (cached sidecar)        (per-question)
```

## Features

- **Handwriting OCR** of scanned answers, using the typed question PDF as
  context so each transcript is labeled with the canonical question ID.
- **Structured rubric extraction** from the marking-scheme PDF, cached as a
  `.parsed.json` sidecar so you only pay for it once.
- **Per-rubric-point grading** at `temperature=0`: every point is marked
  awarded/denied with a quoted snippet of evidence, a rationale, and a grading
  confidence.
- **Self-contained HTML report** — each answer page is shown side by side with
  the rubric points scored against it (images embedded as base64, so the file
  is fully portable). Also exports JSON and a slim Markdown summary.
- **Subject-agnostic.** Add a subject by editing two dicts in `config.py`.
  V1 target is AP Calculus BC, with CS-A / Human Geography / Psychology as the
  generalization path.
- **Confidence surfacing.** Because there's no human checkpoint, low OCR
  confidence and low grading confidence are flagged in the report instead.

## Requirements

- Python 3.10+
- A Google Gemini credential (either an [AI Studio API key](https://aistudio.google.com/apikey)
  or a Vertex AI service account)

## Setup

```bash
pip install -r requirements.txt        # or: uv pip install -r requirements.txt
cp .env.example .env                   # then edit .env (see below)
```

Pick **one** auth method in `.env`:

```ini
# Option A (recommended): AI Studio API key
GEMINI_API_KEY=your-key-here

# Option B: Vertex AI service account
# GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/service-account.json
# GOOGLE_CLOUD_PROJECT=your-gcp-project-id
# GOOGLE_CLOUD_LOCATION=us-central1
```

Drop three PDFs into `data/`:

| File | What it is |
|---|---|
| `data/questions.pdf` | The typed exam questions (OCR context only — not transcribed) |
| `data/answers.pdf` | The student's scanned handwritten responses (this gets transcribed) |
| `data/marking-scheme.pdf` | The typed rubric / scoring guidelines |

## Usage

Open `grader.ipynb`, set the `CONFIG` block near the top (subject, year,
optional set, and which questions to grade), and **Run All**. Outputs land in
`out/`:

- `scorecard-<subject>-<year>.html` — the main report
- `scorecard-<subject>-<year>.json` — the canonical machine-readable artifact
- `scorecard-<subject>-<year>.md` — a plaintext summary

```python
CONFIG = {
    "subject":            "AP Calculus BC",
    "year":               2024,
    "set":                None,            # "Set 1" / "Set 2" / None
    "questions":          "all",           # or ["1", "3"]
    "questions_pdf":      Path("data/questions.pdf"),
    "answers_pdf":        Path("data/answers.pdf"),
    "marking_scheme_pdf": Path("data/marking-scheme.pdf"),
    # ... models, DPI, output dir, confidence threshold
}
```

## How it works

| Stage | Function (`helpers.py`) | Notes |
|---|---|---|
| PDF → images | `render_pdf_to_images` | PyMuPDF; 300 DPI for handwriting, 200 for typed rubric |
| OCR | `ocr_submission` | Question + answer PDFs in one call → `ParsedSubmission` |
| Rubric parse | `load_rubric` | Cached as `{pdf}.parsed.json`; `force_reparse=True` to refresh |
| Align granularity | `flatten_rubric_by_subpart` | Regroups rubric to sub-part keys so they match the OCR answer keys |
| Grade | `grade_question` | One call per question → `QuestionScorecard` |
| Render | `render_html_report` | Answer pages beside graded points + evidence |

All Pydantic models live in `schemas.py` and double as Gemini
`response_schema`s. Prompts are plain text in `prompts/` and are read at call
time, so you can iterate on them without touching Python.

### Supported subjects

Set `CONFIG["subject"]` to one of these canonical names (registered in
`config.py`). A misspelled subject fails loudly with the full list.

- AP Calculus AB / BC
- AP Precalculus
- AP Physics C: Mechanics
- AP Physics C: Electricity and Magnetism
- AP Environmental Science
- AP Microeconomics
- AP Macroeconomics
- AP Psychology
- AP World History: Modern
- AP Human Geography
- AP Comparative Government and Politics
- AP English Language and Composition
- AP Computer Science A
- AP Computer Science Principles

Each subject carries its own grading guidance (point structure, follow-through
rules, what earns vs. doesn't earn credit) injected into the grading prompt.

### Adding a subject

Edit `config.py`:

```python
SUBJECT_SLUG["AP Biology"] = "biology"
SUBJECT_GRADING_ADDENDA["AP Biology"] = "Subject-specific grading rules go here."
```

The addendum text is injected verbatim into the grading prompt — this is where
subject-specific scoring conventions live.

## Project layout

```
grader.ipynb     main notebook (orchestration)
config.py        subject slugs + per-subject grading addenda
schemas.py       Pydantic models (also Gemini response schemas)
helpers.py       PDF render, Gemini client, OCR, rubric parse, grading, HTML report
prompts/         ocr.txt, rubric_extract.txt, grade_question.txt
data/            your input PDFs (gitignored)
rubrics/         optional rubric library (gitignored)
out/             generated scorecards (gitignored)
```

See [CLAUDE.md](CLAUDE.md) for deeper architecture notes.

## Notes & caveats

- **Fully automated, no human review checkpoint.** OCR errors can propagate
  into grades; the report flags low-confidence questions but does not block.
- **One student per submission PDF.** Not a batch document.
- Best results need a reasonably clean scan (≥300 DPI, not heavily skewed).
- Never commit your `.env`, service-account JSON, or student PDFs — they're all
  covered by `.gitignore`.

## License

[MIT](LICENSE)
