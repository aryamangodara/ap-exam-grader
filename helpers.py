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

import os
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
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
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
    model: str = "gemini-2.5-pro",
) -> ParsedSubmission:
    """OCR the student's handwritten answers, using the question PDF as context.

    Both PDFs go in one call so Gemini uses the canonical question IDs from
    the question PDF when labeling each transcribed answer — no separate
    segmentation step needed.
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

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ParsedSubmission,
            temperature=0,
        ),
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
    model: str = "gemini-2.5-pro",
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
    model: str = "gemini-2.5-pro",
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
