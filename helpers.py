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
import random
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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

def get_gemini_client(timeout_ms: int = 300_000) -> genai.Client:
    """Build a Gemini client. Prefers GEMINI_API_KEY (AI Studio); falls back to Vertex.

    `timeout_ms` is the per-request HTTP timeout in milliseconds (google-genai
    measures timeouts in ms; the SDK default is no explicit timeout). The
    5-minute default comfortably covers a heavy Pro-model OCR call so it isn't
    cancelled mid-flight; pair it with `generate_with_retry` for transient
    499/503 blips.

    Vertex AI mode requires:
        GOOGLE_APPLICATION_CREDENTIALS  → path to service-account JSON on disk
        GOOGLE_CLOUD_PROJECT            → GCP project ID
        GOOGLE_CLOUD_LOCATION           → optional, defaults to us-central1
    """
    http_options = types.HttpOptions(timeout=timeout_ms)
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key, http_options=http_options)

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
        return genai.Client(vertexai=True, project=project, location=location,
                            http_options=http_options)

    raise RuntimeError(
        "No Gemini credentials found. In Grader/.env set either:\n"
        "  GEMINI_API_KEY=...                                  (AI Studio, simpler)\n"
        "or:\n"
        "  GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/sa.json   (Vertex AI)\n"
        "  GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>\n"
    )


# ---------------------------------------------------------------------------
# Transient-error retry around Gemini calls
# ---------------------------------------------------------------------------

# Worth retrying: rate limits, 5xx server errors, and the mid-flight
# cancellations (499 CANCELLED / DEADLINE_EXCEEDED) seen on the preview models.
_RETRYABLE_CODES = {408, 429, 499, 500, 502, 503, 504}
_RETRYABLE_STATUSES = {
    "CANCELLED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL",
    "RESOURCE_EXHAUSTED", "ABORTED",
}


def _is_transient(exc: Exception) -> bool:
    """True if `exc` looks like a transient Gemini API error worth retrying."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    if isinstance(code, int) and code in _RETRYABLE_CODES:
        return True
    if isinstance(status, str) and status.upper() in _RETRYABLE_STATUSES:
        return True
    blob = str(exc).upper()
    if any(s in blob for s in _RETRYABLE_STATUSES):
        return True
    return any(str(c) in blob for c in _RETRYABLE_CODES)


def _looks_empty(response) -> bool:
    """True if the response carries no parsed structured content.

    All grader call sites use ``response_schema``, so ``response.parsed is None``
    means the model produced nothing usable — typically a safety/recitation
    filter, a MAX_TOKENS truncation of the structured output, or a transient
    blank response. Worth retrying.
    """
    return response is None or getattr(response, "parsed", None) is None


def _diagnose_empty(response) -> str:
    """One-line description of why a response has no parsed content."""
    if response is None:
        return "response is None"
    finish = block = None
    try:
        cands = list(getattr(response, "candidates", None) or [])
        if cands:
            finish = getattr(cands[0], "finish_reason", None)
    except Exception:
        pass
    try:
        pf = getattr(response, "prompt_feedback", None)
        if pf is not None:
            block = getattr(pf, "block_reason", None)
    except Exception:
        pass
    text_len = len(response.text or "") if getattr(response, "text", None) is not None else 0
    return f"finish_reason={finish!r}, block_reason={block!r}, text_len={text_len}"


def generate_with_retry(
    client: genai.Client,
    *,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    label: str = "",
    **kwargs,
):
    """Call ``client.models.generate_content`` with retry on transient failures.

    Retries on two distinct flavours of transience:

    1. **Exception-based** — 429/499/5xx and CANCELLED/UNAVAILABLE/
       DEADLINE_EXCEEDED — with exponential backoff + jitter.
    2. **Empty response** — the call succeeded HTTP-wise but came back with no
       parsed content (``response.parsed is None``), typically a safety filter,
       MAX_TOKENS truncation of structured output, or a transient blank reply.

    Non-transient exceptions (400 bad request, auth failures) raise immediately.
    On the final attempt an empty response is returned to the caller so it can
    raise with the diagnostic from ``_diagnose_empty``.
    """
    tag = f" [{label}]" if label else ""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(**kwargs)
        except Exception as exc:
            if attempt == max_attempts or not _is_transient(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            print(f"    transient Gemini error{tag} (attempt {attempt}/{max_attempts}): {exc}")
            print(f"    retrying in {delay:.1f}s...")
            time.sleep(delay)
            continue
        if _looks_empty(response):
            if attempt == max_attempts:
                return response  # let the caller raise with full diagnostics
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            print(f"    empty Gemini response{tag} (attempt {attempt}/{max_attempts}): "
                  f"{_diagnose_empty(response)}")
            print(f"    retrying in {delay:.1f}s...")
            time.sleep(delay)
            continue
        return response


# ---------------------------------------------------------------------------
# Question-ID normalization — canonical lowercase form everywhere
# ---------------------------------------------------------------------------

def _normalize_qid(qid: str) -> str:
    """Canonicalize a question identifier to lowercase + trimmed.

    Both the OCR and rubric-parse passes can drift on sub-part casing — Gemini
    sometimes returns ``"1A"`` / ``"2B-i"`` when the prompt says ``"1a"`` /
    ``"2b-i"``. Without normalization, the grader's ``answers ∩ rubric`` set
    is empty and every sub-part is silently scored 0/max via
    :func:`build_unattempted_scorecards`. Applying ``.lower()`` at every
    ingestion point guarantees both sides match.
    """
    return (qid or "").strip().lower()


def _normalize_rubric_qids(rubric) -> None:
    """In-place: lowercase every question_id inside a ParsedRubric."""
    for q in rubric.questions:
        q.question_id = _normalize_qid(q.question_id)
        for p in q.rubric_points:
            p.question_id = _normalize_qid(p.question_id)


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

    response = generate_with_retry(
        client,
        label="OCR",
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**config_kwargs),
    )

    parsed = response.parsed
    if parsed is None:
        raise RuntimeError(
            f"Gemini returned no parsed ParsedSubmission ({_diagnose_empty(response)}). "
            f"Raw text:\n" + (response.text or "<empty>")
        )
    # Normalize sub-part casing — Gemini occasionally returns "1A" / "2B-i"
    # instead of the prompt-specified "1a" / "2b-i". Without this, the
    # answers-vs-rubric intersection is empty and everything scores 0/max.
    for ans in parsed.answers:
        ans.question_id = _normalize_qid(ans.question_id)
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
        cached = ParsedRubric.model_validate_json(cache_path.read_text(encoding="utf-8"))
        # Defensive: normalize old caches whose ids might be mixed-case.
        _normalize_rubric_qids(cached)
        return cached

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

    response = generate_with_retry(
        client,
        label="rubric",
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
            f"Gemini returned no parsed ParsedRubric ({_diagnose_empty(response)}). "
            f"Raw text:\n" + (response.text or "<empty>")
        )

    # Backfill metadata from context if model omitted it
    if not parsed.subject:
        parsed.subject = subject
    if not parsed.year:
        parsed.year = year
    if set_label and not parsed.set_label:
        parsed.set_label = set_label

    # Normalize sub-part casing before writing the cache so future loads stay clean.
    _normalize_rubric_qids(parsed)

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

    response = generate_with_retry(
        client,
        label=f"grade {answer.question_id}",
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
            f"Gemini returned no parsed QuestionScorecard for Q{answer.question_id} "
            f"({_diagnose_empty(response)}). Raw text:\n{response.text or '<empty>'}"
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
    force_review_qids: set[str] | None = None,
) -> list[QuestionScorecard]:
    """Grade many questions concurrently with a thread pool.

    Each `grade_question` call is an independent, I/O-bound Gemini request, so
    running them on threads gives near-linear speedup over a sequential loop
    without changing any per-question logic. Results are returned in the same
    order as `qids`; any qid missing from the rubric or the answers is skipped
    with a printed note (matching the previous sequential behaviour).

    ``force_review_qids`` flags every rubric point of those qids for human
    review regardless of OCR confidence — used for sub-parts whose transcript
    was recovered from a parent-level OCR block (see
    ``_synthesize_subpart_answers_from_parents``).
    """
    force_review = force_review_qids or set()
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
            review_recommended=(
                qid in force_review or ans.confidence < low_confidence_threshold
            ),
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
# Sub-part recovery — rescue parent-level OCR blocks for sub-part rubrics
# ---------------------------------------------------------------------------

def _synthesize_subpart_answers_from_parents(
    answer_by_qid: dict[str, TranscribedAnswer],
    missing_qids: list[str],
) -> tuple[dict[str, TranscribedAnswer], list[str], list[str]]:
    """Recover sub-part answers from a parent-level transcript.

    If the OCR pass labeled a continuous unlabeled response with the parent
    question id (e.g. ``"4"`` because the student wrote one block addressing
    4a–4d without writing the sub-part labels themselves), every sub-part
    missing an explicit answer is given a copy of that parent transcript so
    the grader can locate per-rubric-point evidence inside the same block.
    Without this rescue, every such sub-part is silently scored 0/max via
    :func:`build_unattempted_scorecards` even though the student did write a
    response — the original bug this guards against.

    Matching rule: for each missing sub-part ``X``, the **longest existing**
    OCR'd question id that is a strict prefix of ``X`` wins (so ``"1b"`` is
    preferred over ``"1"`` when both exist for a sub-part like ``"1b-ii"``).

    Returns ``(updated_answer_by_qid, still_missing, recovered_qids)``.
    ``recovered_qids`` is the list of sub-parts we filled in — callers should
    flag those for human review (the per-sub-part attribution came from the
    grader rather than from explicit student labels).
    """
    updated = dict(answer_by_qid)
    recovered: list[str] = []
    still_missing: list[str] = []
    # Sort candidate parents longest-first so the most specific prefix wins.
    candidates = sorted(updated, key=len, reverse=True)
    for sub in missing_qids:
        parent = next(
            (c for c in candidates if c != sub and sub.startswith(c)),
            None,
        )
        if parent is None:
            still_missing.append(sub)
            continue
        parent_ans = updated[parent]
        updated[sub] = TranscribedAnswer(
            question_id=sub,
            transcript=parent_ans.transcript,
            source_pages=list(parent_ans.source_pages),
            confidence=parent_ans.confidence,
            low_confidence_snippets=list(parent_ans.low_confidence_snippets),
        )
        recovered.append(sub)
    return updated, still_missing, recovered


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
    recovered_qids: list[str] | None = None,
) -> str:
    """Build a self-contained HTML report.

    Layout: one block per answer-PDF page. Left = the rendered page image
    (sticky); right = every question mapped to that page with its rubric
    points, each showing awarded/denied, points, rationale, and the exact
    transcript evidence quote. Images are embedded as base64 so the file is
    fully portable.

    ``recovered_qids`` lists sub-parts whose transcript was recovered from a
    parent-level OCR block (the student wrote one continuous unlabeled
    response to a multi-part question). They are rendered alongside the
    parent's answer pages — not in the Unattempted section — with a
    "shared transcript" tag, since each sub-part was graded against the same
    parent transcript. The parent's own (now redundant) "not graded" card is
    suppressed in favour of its children.
    """
    esc = html.escape
    scorecards_by_qid = {qs.question_id: qs for qs in scorecard.questions}
    ocr_by_qid = {a.question_id: a for a in submission.answers}
    recovered_list = list(recovered_qids or [])  # preserve caller order for stable card order
    recovered_set = set(recovered_list)

    # For each recovered sub-part, find its OCR parent so we can place the
    # sub-part's qcard on the parent's pages and surface the parent transcript.
    # Mirrors the longest-prefix rule in `_synthesize_subpart_answers_from_parents`.
    def _parent_of(sub: str) -> str | None:
        for cand in sorted(ocr_by_qid, key=len, reverse=True):
            if cand != sub and sub.startswith(cand):
                return cand
        return None
    # Build as a plain dict (insertion-ordered) so qcards render in the order
    # the caller passed — typically the sorted sub-part order (4a, 4b, 4c, 4d).
    recovered_parents: dict[str, str] = {}
    for sub in recovered_list:
        parent = _parent_of(sub)
        if parent is not None:
            recovered_parents[sub] = parent
    parents_with_recovered_children = set(recovered_parents.values())

    # Build the answer list we actually render: keep every OCR'd answer except
    # parents whose children took their place, and synthesize one entry per
    # recovered sub-part that inherits the parent's source_pages + transcript
    # so the grading appears beside the page image the student wrote on.
    augmented: list[TranscribedAnswer] = []
    for ans in submission.answers:
        if ans.question_id in parents_with_recovered_children:
            continue  # children render in its place — suppress the orphan card
        augmented.append(ans)
    for sub, parent in recovered_parents.items():
        p_ans = ocr_by_qid[parent]
        augmented.append(TranscribedAnswer(
            question_id=sub,
            transcript=p_ans.transcript,
            confidence=p_ans.confidence,
            source_pages=list(p_ans.source_pages),
            low_confidence_snippets=list(p_ans.low_confidence_snippets),
        ))

    # Map each page -> answers appearing on it (OCR order, recovered last).
    page_to_answers: dict[int, list[TranscribedAnswer]] = defaultdict(list)
    for ans in augmented:
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
        shared_flag = (
            ' <span class="tag review">shared transcript</span>'
            if ans.question_id in recovered_set else ""
        )
        return f"""
        <div class="qcard">
          <div class="qcard-head">
            <span class="qid">Q {esc(qs.question_id)}{ocr_flag}{shared_flag}</span>
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
    # Note: `rendered_qids` is built from `augmented`, not `submission.answers`,
    # so recovered sub-parts (graded against a parent transcript) appear in the
    # per-page section above and do NOT fall into Unattempted.
    rendered_qids = {a.question_id for a in augmented}
    unattempted = [qs for qs in scorecard.questions if qs.question_id not in rendered_qids]
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
# Batch orchestration — discover subject folders and grade each end-to-end
# ---------------------------------------------------------------------------

def _find_pdf(folder: Path, *keywords: str) -> Path | None:
    """First PDF in `folder` whose filename stem contains all keywords (case-insensitive)."""
    pdfs = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
    for p in pdfs:
        stem = p.stem.lower()
        if all(k in stem for k in keywords):
            return p
    return None


def discover_exam_folders(
    data_dir: Path,
    slug_to_subject: dict[str, str],
    *,
    only_slugs: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Find subject sub-folders under ``data_dir`` that hold a full exam.

    A folder qualifies if it contains a questions PDF, an answers PDF and a
    marking-scheme PDF. Files are matched loosely by filename keyword, so
    ``marking scheme.pdf`` and ``marking-scheme.pdf`` both work. Returns
    ``(exams, notes)``: each exam is a dict with the resolved paths, the folder
    ``slug`` and its canonical ``subject``; ``notes`` holds human-readable
    reasons folders were skipped (so nothing is dropped silently).
    """
    data_dir = Path(data_dir)
    exams: list[dict] = []
    notes: list[str] = []
    for folder in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        slug = folder.name
        if only_slugs and slug not in only_slugs:
            continue
        q = _find_pdf(folder, "question")
        a = _find_pdf(folder, "answer")
        m = _find_pdf(folder, "marking") or _find_pdf(folder, "scheme")
        missing = [n for n, p in (("questions", q), ("answers", a), ("marking-scheme", m)) if p is None]
        if len(missing) == 3:
            continue  # empty scaffold folder — nothing to grade yet
        if missing:
            notes.append(f"{slug}: skipped — missing {', '.join(missing)} PDF(s)")
            continue
        subject = slug_to_subject.get(slug)
        if subject is None:
            notes.append(f"{slug}: skipped — folder name is not a known subject slug (see config.py)")
            continue
        exams.append({
            "slug": slug,
            "subject": subject,
            "folder": folder,
            "questions_pdf": q,
            "answers_pdf": a,
            "marking_scheme_pdf": m,
        })
    return exams, notes


def assemble_scorecard(
    *,
    subject: str,
    year: int,
    set_label: str | None,
    question_scorecards: list[QuestionScorecard],
    missing_qids: list[str],
    recovered_qids: list[str] | None = None,
    config_echo: dict | None = None,
) -> Scorecard:
    """Total per-question scorecards into a Scorecard, with review flags.

    Flags cover unattempted (0/max) sub-parts, sub-parts whose transcript was
    recovered from a parent-level OCR block (unlabeled multi-part response),
    questions whose OCR confidence fell below threshold, and any point graded
    with low confidence.
    """
    total_earned = sum(qs.points_earned for qs in question_scorecards)
    total_possible = sum(qs.points_possible for qs in question_scorecards)
    percentage = (total_earned / total_possible * 100.0) if total_possible else 0.0

    missing_set = set(missing_qids)
    recovered_set = set(recovered_qids or [])
    review_flags: list[str] = []
    for qs in question_scorecards:
        if qs.question_id in missing_set:
            review_flags.append(
                f"Q{qs.question_id}: no answer transcribed — scored 0/max; verify it was truly left blank"
            )
            continue
        if qs.question_id in recovered_set:
            review_flags.append(
                f"Q{qs.question_id}: student wrote an unlabeled response covering this and "
                "sibling sub-parts; transcript reused from the parent question — verify the "
                "grader attributed evidence to the right sub-part"
            )
            # Don't double-flag with the generic OCR / low-confidence messages.
            continue
        if any(ps.review_recommended for ps in qs.point_scores):
            review_flags.append(f"Q{qs.question_id}: OCR confidence below threshold — verify transcript")
        if any(ps.grading_confidence == "low" for ps in qs.point_scores):
            review_flags.append(f"Q{qs.question_id}: one or more rubric points scored with low confidence")

    return Scorecard(
        subject=subject,
        year=year,
        set_label=set_label,
        total_points_earned=total_earned,
        total_points_possible=total_possible,
        percentage=percentage,
        questions=question_scorecards,
        review_flags=review_flags,
        generated_at=datetime.now(timezone.utc).isoformat(),
        config_echo=config_echo or {},
    )


def grade_exam(
    client: genai.Client,
    *,
    subject: str,
    year: int,
    set_label: str | None,
    questions_pdf: Path,
    answers_pdf: Path,
    marking_scheme_pdf: Path,
    ocr_prompt_path: Path,
    rubric_prompt_path: Path,
    grade_prompt_path: Path,
    subject_addendum: str = "",
    model_ocr: str = "gemini-3.1-pro-preview",
    model_rubric: str = "gemini-3.5-flash",
    model_grading: str = "gemini-3.5-flash",
    ocr_dpi: int = 300,
    rubric_dpi: int = 200,
    ocr_thinking_level: str | None = "low",
    grading_max_workers: int = 8,
    low_confidence_threshold: float = 0.75,
    questions: list[str] | str = "all",
    config_echo: dict | None = None,
) -> dict:
    """Run the full OCR -> rubric -> grade -> assemble pipeline for one exam.

    Returns a dict with: ``scorecard``, ``submission``, ``answer_images``,
    ``rubric``, ``qids_to_grade`` and ``missing_qids``. Pass ``submission`` and
    ``answer_images`` straight to :func:`render_html_report` to build the HTML.
    Unattempted sub-parts (in the rubric but not transcribed) are scored 0/max.
    """
    question_images = render_pdf_to_images(questions_pdf, dpi=ocr_dpi)
    answer_images = render_pdf_to_images(answers_pdf, dpi=ocr_dpi)

    submission = ocr_submission(
        client, question_images, answer_images, ocr_prompt_path,
        model=model_ocr, thinking_level=ocr_thinking_level,
    )

    rubric = load_rubric(
        client, marking_scheme_pdf,
        subject=subject, year=year, set_label=set_label,
        prompt_path=rubric_prompt_path, model=model_rubric, dpi=rubric_dpi,
    )

    rubric_by_qid = flatten_rubric_by_subpart(rubric)
    answer_by_qid = {a.question_id: a for a in submission.answers}

    if questions == "all":
        universe = set(rubric_by_qid)
    else:
        universe = set(questions) & set(rubric_by_qid)
    if not universe:
        raise RuntimeError(
            f"No questions to grade for {subject!r}. "
            f"Rubric sub-parts: {sorted(rubric_by_qid)}; "
            f"answer sub-parts: {sorted(answer_by_qid)}; requested: {questions}."
        )

    # First pass: which rubric sub-parts have an explicit OCR'd answer?
    initial_missing = sorted(universe - set(answer_by_qid))

    # Rescue parent-level OCR blocks (e.g. student wrote one continuous answer
    # to Q4 without labeling 4a/4b/4c/4d) by copying the parent transcript
    # into each missing sub-part. Sub-parts genuinely not addressed remain in
    # ``missing_qids`` and are scored 0/max as before.
    answer_by_qid, still_missing, recovered_qids = (
        _synthesize_subpart_answers_from_parents(answer_by_qid, initial_missing)
    )
    if recovered_qids:
        print(
            f"  Recovered {len(recovered_qids)} sub-part(s) from a parent-level "
            f"OCR block: {', '.join(recovered_qids)}"
        )

    qids_to_grade = sorted(universe & set(answer_by_qid))
    missing_qids = sorted(set(still_missing) & universe)

    question_scorecards = grade_questions_parallel(
        client, qids_to_grade, rubric_by_qid, answer_by_qid,
        subject=subject, prompt_path=grade_prompt_path,
        subject_addendum=subject_addendum, model=model_grading,
        low_confidence_threshold=low_confidence_threshold,
        max_workers=grading_max_workers,
        force_review_qids=set(recovered_qids),
    )
    question_scorecards += build_unattempted_scorecards(rubric_by_qid, missing_qids)
    question_scorecards.sort(key=lambda qs: qs.question_id)

    scorecard = assemble_scorecard(
        subject=subject, year=year, set_label=set_label,
        question_scorecards=question_scorecards, missing_qids=missing_qids,
        recovered_qids=recovered_qids,
        config_echo=config_echo,
    )
    return {
        "scorecard": scorecard,
        "submission": submission,
        "answer_images": answer_images,
        "rubric": rubric,
        "qids_to_grade": qids_to_grade,
        "missing_qids": missing_qids,
        "recovered_qids": recovered_qids,
    }


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
