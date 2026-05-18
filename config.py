"""Configuration constants for the AP FRQ Auto-Grader."""
from __future__ import annotations

# Canonical AP subject name -> filename slug used in rubrics/ directory.
SUBJECT_SLUG: dict[str, str] = {
    "AP Calculus AB":                 "calculus-ab",
    "AP Calculus BC":                 "calculus-bc",
    "AP Computer Science A":          "computer-science-a",
    "AP Computer Science Principles": "computer-science-principles",
    "AP Human Geography":             "human-geography",
    "AP Psychology":                  "psychology",
}

# Optional subject-specific addendum injected into the grading prompt.
# Filled out as each subject is validated in Phase 3.
SUBJECT_GRADING_ADDENDA: dict[str, str] = {
    "AP Calculus AB": (
        "Accept algebraically equivalent forms. A sign or arithmetic error that "
        "propagates through subsequent steps should only cost the point where the "
        "error was introduced; downstream points may still be earned on follow-through."
    ),
    "AP Calculus BC": (
        "Accept algebraically equivalent forms. A sign or arithmetic error that "
        "propagates through subsequent steps should only cost the point where the "
        "error was introduced; downstream points may still be earned on follow-through."
    ),
    "AP Computer Science A": (
        "Accept functionally equivalent Java with minor syntax issues (missing "
        "semicolons, off-by-one variable names) unless the rubric explicitly requires "
        "strict syntax. Variable-name differences are not penalized. Award points for "
        "correct algorithmic intent even if Java idiom is non-canonical."
    ),
    "AP Computer Science Principles": "",  # Filled during Phase 3
    "AP Human Geography": (
        "Require a concept definition paired with concrete application to the stimulus "
        "to earn the application point. Vague restatements of the stimulus without a "
        "geographic concept do not earn application credit."
    ),
    "AP Psychology": (
        "The 2025 redesign uses Article Analysis (AAQ) and Evidence-Based (EBQ) question "
        "formats. For each rubric point require BOTH a correct concept identification AND "
        "specific application to the scenario or article evidence. In EBQ responses, "
        "accept any citation that uniquely identifies one of the provided sources "
        "(e.g. 'Source 1', 'the second study', or the author's name)."
    ),
}

# Where on AP Central to direct the user to download a rubric they haven't
# already placed in rubrics/.
AP_CENTRAL_EXAM_PAGE = "https://apcentral.collegeboard.org/courses/ap-{slug}/exam/past-exam-questions"


def rubric_filename(subject: str, year: int, set_label: str | None = None) -> str:
    """Compute the expected filename for a rubric PDF inside rubrics/."""
    if subject not in SUBJECT_SLUG:
        raise KeyError(
            f"Unknown subject {subject!r}. Add it to SUBJECT_SLUG in config.py."
        )
    slug = SUBJECT_SLUG[subject]
    suffix = ""
    if set_label:
        suffix = "-" + set_label.lower().replace(" ", "")
    return f"{slug}-{year}{suffix}.pdf"
