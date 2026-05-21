"""Helpers for the AP FRQ Auto-Grader notebook.

Phases 0-2 surface:
    - render_pdf_to_images
    - get_gemini_client            (AI Studio API key OR Vertex AI service account)
    - ocr_submission               (joint OCR over question PDF + answer PDF)
    - load_rubric                  (parse marking-scheme PDF, cached as .parsed.json)
    - grade_question               (one rubric + one transcript -> QuestionScorecard)
    - character_error_rate         (optional, for manual OCR validation)
"""
from __future__ import annotations

import base64
import html
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
from google import genai
from google.genai import types

from schemas import (
    ParsedRubric,
    ParsedSubmission,
    QuestionRubric,
    QuestionScorecard,
    RubricPointScore,
    Scorecard,
    TranscribedAnswer,
)


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def render_pdf_to_images(pdf_path: Path, dpi: int = 300) -> list[Image.Image]:
    """Render every page of a PDF to a PIL Image at the given DPI."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    try:
        scale = dpi / 72.0  # PDF user-space is 72 DPI
        matrix = fitz.Matrix(scale, scale)
        images: list[Image.Image] = []
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)
        return images
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Gemini client — auto-detect AI Studio vs Vertex AI
# ---------------------------------------------------------------------------

def get_gemini_client() -> genai.Client:
    """Build a Gemini client. Prefers GEMINI_API_KEY (AI Studio); falls back to Vertex.

    Vertex AI mode requires:
        GOOGLE_APPLICATION_CREDENTIALS  → path to service-account JSON on disk
        GOOGLE_CLOUD_PROJECT            → GCP project ID
        GOOGLE_CLOUD_LOCATION           → optional, defaults to us-central1
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key)

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS is set but GOOGLE_CLOUD_PROJECT is not. "
                "Add GOOGLE_CLOUD_PROJECT=<your-gcp-project-id> to Grader/.env."
            )
        # Gemini 3.x is served from the "global" endpoint on Vertex AI; regional
        # endpoints like us-central1 return 404 for these models.
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        return genai.Client(vertexai=True, project=project, location=location)

    raise RuntimeError(
        "No Gemini credentials found. In Grader/.env set either:\n"
        "  GEMINI_API_KEY=...                                  (AI Studio, simpler)\n"
        "or:\n"
        "  GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/sa.json   (Vertex AI)\n"
        "  GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>\n"
    )


# ---------------------------------------------------------------------------
# OCR — joint pass over question + answer PDFs
# ---------------------------------------------------------------------------

def ocr_submission(
    client: genai.Client,
    question_images: list[Image.Image],
    answer_images: list[Image.Image],
    prompt_path: Path,
    model: str = "gemini-3.5-flash",
    thinking_level: str | None = None,
) -> ParsedSubmission:
    """OCR the student's handwritten answers, using the question PDF as context.

    Both PDFs go in one call so Gemini uses the canonical question IDs from
    the question PDF when labeling each transcribed answer — no separate
    segmentation step needed.

    `thinking_level` (Gemini 3.x): one of "minimal", "low", "medium", "high".
    Transcription is not a reasoning task, so "low"/"minimal" cuts latency
    sharply. Leave None to use the model's default. Ignored by models that
    predate thinking_level (e.g. 2.5), which use the legacy thinking_budget.
    """
    prompt = Path(prompt_path).read_text(encoding="utf-8")

    contents: list = [prompt, "\n=== QUESTION PDF (typed) — context only, do not transcribe ===\n"]
    for i, img in enumerate(question_images, start=1):
        contents.append(f"[Question PDF page {i}/{len(question_images)}]")
        contents.append(img)

    contents.append("\n=== STUDENT ANSWER PDF (handwritten) — transcribe this ===\n")
    for i, img in enumerate(answer_images, start=1):
        contents.append(f"[Answer PDF page {i}/{len(answer_images)}]")
        contents.append(img)

    config_kwargs: dict = dict(
        response_mime_type="application/json",
        response_schema=ParsedSubmission,
        temperature=0,
    )
    if thinking_level:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**config_kwargs),
    )

    parsed = response.parsed
    if parsed is None:
        raise RuntimeError(
            "Gemini returned no parsed ParsedSubmission. Raw text:\n"
            + (response.text or "<empty>")
        )
    return parsed  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Rubric / marking-scheme parsing (Phase 1) — cached as .parsed.json sidecar
# ---------------------------------------------------------------------------

def load_rubric(
    client: genai.Client,
    marking_scheme_pdf: Path,
    *,
    subject: str,
    year: int,
    set_label: str | None,
    prompt_path: Path,
    model: str = "gemini-3.5-flash",
    dpi: int = 200,
    force_reparse: bool = False,
) -> ParsedRubric:
    """Load and parse a marking-scheme PDF into a ParsedRubric.

    On first call: renders the PDF, asks Gemini to extract structured rubric
    data, and writes the result to `{pdf}.parsed.json` next to the PDF.
    Subsequent calls load the JSON sidecar and skip Gemini entirely.
    """
    pdf_path = Path(marking_scheme_pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Marking scheme PDF not found: {pdf_path}")

    cache_path = pdf_path.with_suffix(pdf_path.suffix + ".parsed.json")
    if cache_path.exists() and not force_reparse:
        return ParsedRubric.model_validate_json(cache_path.read_text(encoding="utf-8"))

    images = render_pdf_to_images(pdf_path, dpi=dpi)
    prompt = Path(prompt_path).read_text(encoding="utf-8")

    context = (
        f"Subject: {subject}\n"
        f"Year:    {year}\n"
        f"Set:     {set_label or 'N/A'}\n"
    )

    contents: list = [prompt, context]
    for i, img in enumerate(images, start=1):
        contents.append(f"[Marking scheme page {i}/{len(images)}]")
        contents.append(img)

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ParsedRubric,
            temperature=0,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        raise RuntimeError(
            "Gemini returned no parsed ParsedRubric. Raw text:\n"
            + (response.text or "<empty>")
        )

    # Backfill metadata from context if model omitted it
    if not parsed.subject:
        parsed.subject = subject
    if not parsed.year:
        parsed.year = year
    if set_label and not parsed.set_label:
        parsed.set_label = set_label

    cache_path.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    return parsed  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Rubric flattening — align rubric granularity with OCR answer granularity
# ---------------------------------------------------------------------------

def flatten_rubric_by_subpart(rubric: ParsedRubric) -> dict[str, QuestionRubric]:
    """Regroup a ParsedRubric so each entry is a sub-part QuestionRubric.

    Why: the parsed rubric's top-level QuestionRubrics are keyed at
    question level ("1", "2", ...) but their rubric_points carry sub-part
    ids ("1a", "1b", ...) — and OCR labels student answers at sub-part
    granularity too. Matching at top level produces an empty intersection;
    matching at sub-part level grades correctly.
    """
    result: dict[str, QuestionRubric] = {}
    for q in rubric.questions:
        groups: dict[str, list] = defaultdict(list)
        for p in q.rubric_points:
            groups[p.question_id].append(p)
        for subpart_id, points in groups.items():
            result[subpart_id] = QuestionRubric(
                question_id=subpart_id,
                prompt_summary=q.prompt_summary,
                rubric_points=points,
                max_points=sum(p.point_value for p in points),
            )
    return result


# ---------------------------------------------------------------------------
# Grading (Phase 2) — one rubric + one transcript -> QuestionScorecard
# ---------------------------------------------------------------------------

def grade_question(
    client: genai.Client,
    question_rubric: QuestionRubric,
    answer: TranscribedAnswer,
    *,
    subject: str,
    prompt_path: Path,
    subject_addendum: str = "",
    model: str = "gemini-3.5-flash",
    review_recommended: bool = False,
) -> QuestionScorecard:
    """Grade one transcribed answer against one question's rubric.

    Returns a QuestionScorecard with per-rubric-point awarded/denied,
    quoted rationale, and grading confidence. If `review_recommended` is
    True (upstream OCR confidence was low), every point score is flagged
    for human review in the final scorecard.
    """
    base_prompt = Path(prompt_path).read_text(encoding="utf-8")
    rubric_json = question_rubric.model_dump_json(indent=2)

    user_message = (
        f"# Subject\n{subject}\n\n"
        f"# Subject-specific guidance\n{subject_addendum or '(none)'}\n\n"
        f"# Rubric for this question\n```json\n{rubric_json}\n```\n\n"
        f"# Student's transcribed answer (question {answer.question_id})\n"
        f"OCR confidence: {answer.confidence:.2f}\n\n"
        f"```\n{answer.transcript}\n```\n"
    )

    response = client.models.generate_content(
        model=model,
        contents=[base_prompt, user_message],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=QuestionScorecard,
            temperature=0,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        raise RuntimeError(
            f"Gemini returned no parsed QuestionScorecard for Q{answer.question_id}. "
            f"Raw text:\n{response.text or '<empty>'}"
        )

    if review_recommended:
        for ps in parsed.point_scores:
            ps.review_recommended = True

    return parsed  # type: ignore[return-value]


def grade_questions_parallel(
    client: genai.Client,
    qids: list[str],
    rubric_by_qid: dict[str, QuestionRubric],
    answer_by_qid: dict[str, TranscribedAnswer],
    *,
    subject: str,
    prompt_path: Path,
    subject_addendum: str = "",
    model: str = "gemini-3.5-flash",
    low_confidence_threshold: float = 0.75,
    max_workers: int = 8,
    verbose: bool = False,
) -> list[QuestionScorecard]:
    """Grade many questions concurrently with a thread pool.

    Each `grade_question` call is an independent, I/O-bound Gemini request, so
    running them on threads gives near-linear speedup over a sequential loop
    without changing any per-question logic. Results are returned in the same
    order as `qids`; any qid missing from the rubric or the answers is skipped
    with a printed note (matching the previous sequential behaviour).
    """
    # Resolve the work list up front, preserving qid order and reporting skips.
    work: list[tuple[str, QuestionRubric, TranscribedAnswer]] = []
    for qid in qids:
        qr = rubric_by_qid.get(qid)
        ans = answer_by_qid.get(qid)
        if qr is None:
            print(f"Skipping {qid}: not in parsed rubric")
            continue
        if ans is None:
            print(f"Skipping {qid}: no student answer found")
            continue
        work.append((qid, qr, ans))

    if not work:
        return []

    import threading
    import time as _time

    t0 = _time.perf_counter()
    timings: dict[str, tuple[float, float]] = {}  # qid -> (start, end) relative to t0

    def _grade(item: tuple[str, QuestionRubric, TranscribedAnswer]):
        qid, qr, ans = item
        start = _time.perf_counter() - t0
        qs = grade_question(
            client=client,
            question_rubric=qr,
            answer=ans,
            subject=subject,
            prompt_path=prompt_path,
            subject_addendum=subject_addendum,
            model=model,
            review_recommended=(ans.confidence < low_confidence_threshold),
        )
        end = _time.perf_counter() - t0
        timings[qid] = (start, end)
        if verbose:
            print(f"  [{threading.current_thread().name}] Q{qid}: "
                  f"start={start:5.1f}s end={end:5.1f}s ({end - start:4.1f}s)")
        return qid, qs

    results: dict[str, QuestionScorecard] = {}
    workers = max(1, min(max_workers, len(work)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_grade, item): item[0] for item in work}
        for fut in as_completed(futures):
            qid = futures[fut]
            try:
                _, qs = fut.result()
            except Exception as exc:  # surface which question failed
                raise RuntimeError(f"Grading failed for Q{qid}: {exc}") from exc
            results[qid] = qs

    # --- Concurrency diagnostic -------------------------------------------
    wall = _time.perf_counter() - t0
    busy = sum(e - s for s, e in timings.values())
    # Peak overlap: max number of requests in flight at any instant.
    events = []
    for s, e in timings.values():
        events.append((s, 1))
        events.append((e, -1))
    events.sort()
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    print(f"\nConcurrency report: wall={wall:.1f}s | sum-of-calls={busy:.1f}s | "
          f"speedup={busy / wall if wall else 0:.1f}x | peak in-flight={peak}/{workers}")
    if peak <= 1:
        print("  ⚠️ Requests ran one-at-a-time (no overlap). Likely server-side "
              "rate-limiting on your Gemini tier — not a threading problem.")

    # Return in the original qid order (as_completed yields out of order).
    return [results[qid] for qid, _, _ in work]


# ---------------------------------------------------------------------------
# Unattempted sub-parts — score 0/max so the denominator is the whole exam
# ---------------------------------------------------------------------------

def build_unattempted_scorecards(
    rubric_by_qid: dict[str, QuestionRubric],
    missing_qids: list[str],
    *,
    mark_review: bool = True,
) -> list[QuestionScorecard]:
    """Synthesize 0/max scorecards for rubric sub-parts that have no answer.

    A sub-part present in the rubric but absent from the OCR'd answers is
    either a genuine blank or an OCR/segmentation miss. Either way it counts
    against the full-exam denominator, so we emit a QuestionScorecard worth
    0/max with one denied point per rubric point. With ``mark_review`` set,
    each point is flagged for human review so an OCR drop is visible rather
    than silently scored zero.
    """
    cards: list[QuestionScorecard] = []
    for qid in missing_qids:
        qr = rubric_by_qid.get(qid)
        if qr is None:
            continue
        point_scores = [
            RubricPointScore(
                point_id=p.point_id,
                awarded=False,
                points_earned=0.0,
                rationale=(
                    "No answer was transcribed for this sub-part "
                    "(blank or missed by OCR), so it earns no credit."
                ),
                transcript_evidence="",
                grading_confidence="high",
                review_recommended=mark_review,
            )
            for p in qr.rubric_points
        ]
        cards.append(
            QuestionScorecard(
                question_id=qid,
                points_earned=0.0,
                points_possible=qr.max_points,
                point_scores=point_scores,
                transcript_used="",
            )
        )
    return cards


# ---------------------------------------------------------------------------
# HTML report — answer pages side-by-side with graded points + evidence
# ---------------------------------------------------------------------------

def _img_to_data_uri(img: Image.Image, max_width: int = 1100, quality: int = 80) -> str:
    """Encode a PIL image as a base64 JPEG data URI, downscaled for file size."""
    im = img.convert("RGB")
    if im.width > max_width:
        ratio = max_width / im.width
        im = im.resize((max_width, int(im.height * ratio)), Image.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


_HTML_STYLE = """
:root {
  --bg: #0f1115; --panel: #181b22; --panel-2: #1f232c; --border: #2a2f3a;
  --text: #e6e8eb; --muted: #9aa3b2; --accent: #6ea8fe;
  --good: #2fbf71; --good-bg: rgba(47,191,113,.12);
  --bad: #f0556b; --bad-bg: rgba(240,85,107,.10);
  --warn: #f5b942; --warn-bg: rgba(245,185,66,.12);
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.55; font-size: 15px;
}
.wrap { max-width: 1500px; margin: 0 auto; padding: 32px 24px 80px; }
header.report-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 24px; flex-wrap: wrap; padding: 28px 32px; margin-bottom: 28px;
  background: linear-gradient(135deg, #1d2330, #14171e);
  border: 1px solid var(--border); border-radius: 18px;
}
header.report-head h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: -.3px; }
header.report-head .meta { color: var(--muted); font-size: 14px; }
.score-badge { text-align: center; min-width: 150px; }
.score-badge .pct { font-size: 46px; font-weight: 700; line-height: 1; letter-spacing: -1px; }
.score-badge .frac { color: var(--muted); font-size: 14px; margin-top: 6px; }
.bar { height: 9px; border-radius: 99px; background: var(--panel-2); overflow: hidden; margin-top: 12px; }
.bar > i { display: block; height: 100%; background: linear-gradient(90deg, var(--bad), var(--warn), var(--good)); }

.flags {
  border: 1px solid var(--warn); background: var(--warn-bg); color: #f7d489;
  border-radius: 12px; padding: 14px 18px; margin-bottom: 28px;
}
.flags strong { color: var(--warn); }
.flags ul { margin: 8px 0 0; padding-left: 20px; }

.page-block {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);
  gap: 24px; margin-bottom: 36px; align-items: start;
}
.page-block .page-title {
  grid-column: 1 / -1; font-size: 13px; text-transform: uppercase;
  letter-spacing: 1.2px; color: var(--muted); border-bottom: 1px solid var(--border);
  padding-bottom: 8px; margin-bottom: 4px;
}
.page-img {
  position: sticky; top: 20px; background: var(--panel); border: 1px solid var(--border);
  border-radius: 14px; padding: 10px; overflow: hidden;
}
.page-img img { width: 100%; display: block; border-radius: 8px; }
.answers-col { display: flex; flex-direction: column; gap: 18px; }

.qcard { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; }
.qcard-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 14px 18px; background: var(--panel-2); border-bottom: 1px solid var(--border);
}
.qcard-head .qid { font-weight: 700; font-size: 17px; }
.qcard-head .qscore { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--accent); }
.transcript {
  margin: 0; padding: 12px 18px; font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 12.5px; color: var(--muted); white-space: pre-wrap; word-break: break-word;
  background: #11141a; border-bottom: 1px solid var(--border); max-height: 220px; overflow: auto;
}
.points { padding: 6px 0; }
.point {
  padding: 12px 18px; border-bottom: 1px solid var(--border);
  display: grid; grid-template-columns: 26px 1fr; gap: 12px;
}
.point:last-child { border-bottom: none; }
.point .icon { font-size: 18px; line-height: 1.4; }
.point.awarded { background: var(--good-bg); }
.point.denied  { background: var(--bad-bg); }
.point .pid { font-weight: 600; }
.point .pts { color: var(--muted); font-weight: 500; font-variant-numeric: tabular-nums; }
.point .rationale { margin: 6px 0 0; }
.point .evidence {
  margin: 8px 0 0; padding: 8px 12px; border-left: 3px solid var(--accent);
  background: #11141a; border-radius: 0 8px 8px 0; font-family: ui-monospace, Consolas, monospace;
  font-size: 12.5px; color: #cdd6e4; white-space: pre-wrap; word-break: break-word;
}
.tag {
  display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px;
  border-radius: 99px; margin-left: 6px; vertical-align: middle;
}
.tag.high { background: var(--good-bg); color: var(--good); }
.tag.medium { background: var(--warn-bg); color: var(--warn); }
.tag.low { background: var(--bad-bg); color: var(--bad); }
.tag.review { background: rgba(110,168,254,.14); color: var(--accent); }
.no-answer { color: var(--muted); font-style: italic; padding: 24px; text-align: center;
  border: 1px dashed var(--border); border-radius: 12px; }
footer.report-foot { margin-top: 40px; color: var(--muted); font-size: 12px; text-align: center; }

.unattempted-block { margin: 0 0 36px; }
.unattempted-title {
  font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px;
  color: var(--bad); border-bottom: 1px solid var(--border);
  padding-bottom: 8px; margin-bottom: 12px;
}
.unattempted-note { color: var(--muted); margin: 0 0 16px; max-width: 980px; }
.unattempted-note strong { color: var(--text); }
.qcard.unattempted-card { border-color: var(--bad); }
.qcard.unattempted-card .qcard-head { background: var(--bad-bg); }
.unattempted-card .ua-note {
  margin: 0; padding: 10px 18px; color: var(--muted); font-size: 13px;
  background: #11141a; border-bottom: 1px solid var(--border);
}
"""


def render_html_report(
    scorecard: Scorecard,
    submission: ParsedSubmission,
    answer_images: list[Image.Image],
    *,
    low_confidence_threshold: float = 0.75,
) -> str:
    """Build a self-contained HTML report.

    Layout: one block per answer-PDF page. Left = the rendered page image
    (sticky); right = every question mapped to that page with its rubric
    points, each showing awarded/denied, points, rationale, and the exact
    transcript evidence quote. Images are embedded as base64 so the file is
    fully portable.
    """
    esc = html.escape
    scorecards_by_qid = {qs.question_id: qs for qs in scorecard.questions}

    # Map each page -> answers appearing on it (in OCR order)
    page_to_answers: dict[int, list[TranscribedAnswer]] = defaultdict(list)
    for ans in submission.answers:
        for p in ans.source_pages:
            page_to_answers[p].append(ans)

    def conf_tag(conf: str) -> str:
        return f'<span class="tag {esc(conf)}">{esc(conf)} confidence</span>'

    def render_point(ps) -> str:
        awarded = ps.awarded
        cls = "awarded" if awarded else "denied"
        icon = "✅" if awarded else "❌"
        review = '<span class="tag review">review</span>' if ps.review_recommended else ""
        evidence = (
            f'<div class="evidence">{esc(ps.transcript_evidence)}</div>'
            if ps.transcript_evidence else ""
        )
        return f"""
        <div class="point {cls}">
          <div class="icon">{icon}</div>
          <div>
            <span class="pid">{esc(ps.point_id)}</span>
            <span class="pts">· {ps.points_earned:g} pt</span>
            {conf_tag(ps.grading_confidence)}{review}
            <div class="rationale">{esc(ps.rationale)}</div>
            {evidence}
          </div>
        </div>"""

    def render_qcard(ans: TranscribedAnswer) -> str:
        qs = scorecards_by_qid.get(ans.question_id)
        if qs is None:
            return f"""
            <div class="qcard">
              <div class="qcard-head"><span class="qid">Q {esc(ans.question_id)}</span>
                <span class="qscore">not graded</span></div>
              <pre class="transcript">{esc(ans.transcript)}</pre>
            </div>"""
        points_html = "".join(render_point(ps) for ps in qs.point_scores)
        ocr_flag = (
            ' <span class="tag low">OCR ' f'{ans.confidence:.2f}</span>'
            if ans.confidence < low_confidence_threshold else ""
        )
        return f"""
        <div class="qcard">
          <div class="qcard-head">
            <span class="qid">Q {esc(qs.question_id)}{ocr_flag}</span>
            <span class="qscore">{qs.points_earned:g} / {qs.points_possible:g}</span>
          </div>
          <pre class="transcript">{esc(qs.transcript_used or ans.transcript)}</pre>
          <div class="points">{points_html}</div>
        </div>"""

    def render_unattempted_qcard(qs) -> str:
        points_html = "".join(render_point(ps) for ps in qs.point_scores)
        return f"""
        <div class="qcard unattempted-card">
          <div class="qcard-head">
            <span class="qid">Q {esc(qs.question_id)} <span class="tag review">unattempted</span></span>
            <span class="qscore">0 / {qs.points_possible:g}</span>
          </div>
          <div class="ua-note">No answer was transcribed — scored 0 / {qs.points_possible:g}.</div>
          <div class="points">{points_html}</div>
        </div>"""

    blocks = []
    for pi, img in enumerate(answer_images, start=1):
        data_uri = _img_to_data_uri(img)
        answers_here = page_to_answers.get(pi, [])
        if answers_here:
            cards = "".join(render_qcard(a) for a in answers_here)
        else:
            cards = '<div class="no-answer">No answers were mapped to this page.</div>'
        blocks.append(f"""
        <section class="page-block">
          <div class="page-title">Answer page {pi} of {len(answer_images)}</div>
          <div class="page-img"><img src="{data_uri}" alt="Answer page {pi}"></div>
          <div class="answers-col">{cards}</div>
        </section>""")

    flags_html = ""
    if scorecard.review_flags:
        items = "".join(f"<li>{esc(f)}</li>" for f in scorecard.review_flags)
        flags_html = f"""
        <div class="flags"><strong>⚠️ Review recommended</strong>
          <ul>{items}</ul></div>"""

    # Sub-parts that have a scorecard but no transcribed answer are the 0/max
    # "unattempted" ones; they belong to no answer page, so they get their own
    # section that explicitly names them and shows each rubric point as 0.
    answered_qids = {a.question_id for a in submission.answers}
    unattempted = [qs for qs in scorecard.questions if qs.question_id not in answered_qids]
    unattempted_html = ""
    if unattempted:
        ids = ", ".join(esc(qs.question_id) for qs in unattempted)
        zero_pts = sum(qs.points_possible for qs in unattempted)
        cards = "".join(render_unattempted_qcard(qs) for qs in unattempted)
        unattempted_html = f"""
        <section class="unattempted-block">
          <div class="unattempted-title">Unattempted — scored 0</div>
          <p class="unattempted-note">
            No answer was transcribed for <strong>{ids}</strong>
            ({len(unattempted)} sub-part(s)), so the student earned
            <strong>0 / {zero_pts:g}</strong> on these parts — they still count
            toward the total. If OCR may have missed an answer, double-check the
            answer pages.
          </p>
          <div class="answers-col">{cards}</div>
        </section>"""

    set_str = f" · {esc(scorecard.set_label)}" if scorecard.set_label else ""
    pct = scorecard.percentage

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scorecard — {esc(scorecard.subject)} {scorecard.year}</title>
<style>{_HTML_STYLE}</style></head>
<body><div class="wrap">
  <header class="report-head">
    <div>
      <h1>{esc(scorecard.subject)} {scorecard.year}{set_str}</h1>
      <div class="meta">Generated {esc(scorecard.generated_at)} · {len(scorecard.questions)} questions graded</div>
    </div>
    <div class="score-badge">
      <div class="pct">{pct:.0f}%</div>
      <div class="frac">{scorecard.total_points_earned:g} / {scorecard.total_points_possible:g} pts</div>
      <div class="bar"><i style="width:{max(0, min(100, pct)):.1f}%"></i></div>
    </div>
  </header>
  {flags_html}
  {unattempted_html}
  {''.join(blocks)}
  <footer class="report-foot">AP FRQ Auto-Grader · per-rubric-point evidence shown beside each answer page</footer>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Character error rate (kept for optional manual validation)
# ---------------------------------------------------------------------------

def character_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance / len(reference). 0.0 = perfect; 1.0 = totally wrong."""
    ref = reference.strip()
    hyp = hypothesis.strip()
    if not ref:
        return 0.0 if not hyp else 1.0

    m, n = len(ref), len(hyp)
    if n == 0:
        return 1.0

    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        ref_ch = ref[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ref_ch == hyp[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,         # deletion
                curr[j - 1] + 1,     # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr
    return prev[n] / m
