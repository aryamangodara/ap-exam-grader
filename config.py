"""Configuration constants for the AP FRQ Auto-Grader.

The grader is subject-agnostic: it grades whatever marking-scheme PDF you
supply, and injects the matching SUBJECT_GRADING_ADDENDA[...] text into the
grading prompt. To support a new subject, add it to BOTH maps below. The
canonical subject name (the dict key) is what you put in CONFIG["subject"].
"""
from __future__ import annotations

# Canonical AP subject name -> filename slug used in the rubrics/ directory.
SUBJECT_SLUG: dict[str, str] = {
    "AP Calculus AB":                          "calculus-ab",
    "AP Calculus BC":                          "calculus-bc",
    "AP Precalculus":                          "precalculus",
    "AP Physics C: Mechanics":                 "physics-c-mechanics",
    "AP Physics C: Electricity and Magnetism": "physics-c-em",
    "AP Environmental Science":                "environmental-science",
    "AP Microeconomics":                       "microeconomics",
    "AP Macroeconomics":                       "macroeconomics",
    "AP Psychology":                           "psychology",
    "AP World History: Modern":                "world-history",
    "AP Human Geography":                      "human-geography",
    "AP Comparative Government and Politics":  "comparative-government-politics",
    "AP English Language and Composition":     "english-language",
    "AP Computer Science A":                   "computer-science-a",
    "AP Computer Science Principles":          "computer-science-principles",
}

# Subject-specific guidance injected into the grading prompt. Each string
# encodes how that subject's official rubric is meant to be applied (point
# structure, follow-through rules, what earns vs. doesn't earn credit).
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
    "AP Precalculus": (
        "Accept algebraically equivalent forms and equivalent exact or decimal answers. "
        "Apply follow-through: an arithmetic or sign error costs only the point where it "
        "occurs, and downstream points may still be earned. Where the rubric asks for "
        "justification or reasoning, a bare final answer does not earn the reasoning point."
    ),
    "AP Physics C: Mechanics": (
        "Calculus-based mechanics. Award points per the rubric's structure (e.g. correct "
        "relationship/setup, substitution, final answer). Apply follow-through: an "
        "incorrect earlier value used correctly downstream still earns the later points. "
        "Accept algebraically and calculus-equivalent expressions and correct symbolic "
        "answers. Require correct units and vector direction where the rubric specifies. "
        "Do not award an answer point for a bare numerical result when the rubric requires "
        "supporting work."
    ),
    "AP Physics C: Electricity and Magnetism": (
        "Calculus-based E&M. Award points per the rubric's structure (correct "
        "relationship/setup, substitution, final answer). Apply follow-through: an "
        "incorrect earlier value used correctly downstream still earns the later points. "
        "Accept algebraically and calculus-equivalent expressions (including correct use "
        "of integrals/derivatives, Gauss's/Ampère's law, etc.) and correct symbolic "
        "answers. Require correct units and direction where the rubric specifies."
    ),
    "AP Environmental Science": (
        "FRQs are point-based and reward specificity. Vague or generic statements earn no "
        "credit — require a concrete mechanism, named example, or specific cause-and-effect "
        "link. For calculation parts, require the setup/equation and correct units, and "
        "apply follow-through on arithmetic. 'Describe/Explain' demands detail; 'Identify' "
        "may be brief."
    ),
    "AP Microeconomics": (
        "Graded on correct economic reasoning, not prose quality. For graph points, require "
        "correctly labeled axes, correctly shaped/positioned curves, and clearly indicated "
        "equilibria or shifts as the rubric specifies. Award an explanation point only when "
        "the response gives the correct direction of change AND the causal chain. Apply "
        "follow-through: an explanation consistent with the student's earlier (even if "
        "incorrect) answer earns the dependent point."
    ),
    "AP Macroeconomics": (
        "Graded on correct economic reasoning, not prose quality. For graph points, require "
        "correctly labeled axes, correctly shaped/positioned curves (AD/AS, money market, "
        "loanable funds, etc.), and clearly indicated equilibria or shifts as the rubric "
        "specifies. Award an explanation point only when the response gives the correct "
        "direction of change AND the causal chain. Apply follow-through on dependent points."
    ),
    "AP Psychology": (
        "The 2025 redesign uses Article Analysis (AAQ) and Evidence-Based (EBQ) question "
        "formats. For each rubric point require BOTH a correct concept identification AND "
        "specific application to the scenario or article evidence. In EBQ responses, "
        "accept any citation that uniquely identifies one of the provided sources "
        "(e.g. 'Source 1', 'the second study', or the author's name)."
    ),
    "AP World History: Modern": (
        "Apply the College Board DBQ/LEQ rubric structure: Thesis/Claim (a defensible "
        "claim that responds to the prompt), Contextualization (broader historical setting), "
        "Evidence (specific and relevant; for DBQs, use of and sourcing of documents via "
        "HIPP), and Analysis & Reasoning (historical reasoning plus complexity). Award each "
        "rubric point independently when its specific criterion is met — do not require a "
        "flawless essay."
    ),
    "AP Human Geography": (
        "Require a concept definition paired with concrete application to the stimulus "
        "to earn the application point. Vague restatements of the stimulus without a "
        "geographic concept do not earn application credit."
    ),
    "AP Comparative Government and Politics": (
        "Require use of specific course concepts and, where the prompt calls for it, accurate "
        "examples from the six core countries (UK, Russia, China, Mexico, Iran, Nigeria). For "
        "each point require BOTH a correct concept/definition AND its application to the "
        "scenario or country. Generic statements without a course concept do not earn "
        "application credit."
    ),
    "AP English Language and Composition": (
        "Apply the 6-point analytic rubric: Thesis (1 pt — a defensible position responding "
        "to the prompt), Evidence & Commentary (up to 4 pts — specific evidence plus a clear "
        "line of reasoning), and Sophistication (1 pt — genuine nuance/complexity, awarded "
        "sparingly). Reward a defensible thesis and specific support; do not penalize minor "
        "grammar or spelling unless it obscures meaning."
    ),
    "AP Computer Science A": (
        "Accept functionally equivalent Java with minor syntax issues (missing "
        "semicolons, off-by-one variable names) unless the rubric explicitly requires "
        "strict syntax. Variable-name differences are not penalized. Award points for "
        "correct algorithmic intent even if Java idiom is non-canonical."
    ),
    "AP Computer Science Principles": (
        "Written-response / Create-task style. Award points for clearly explaining the "
        "program's purpose and function, the role of the selected code segment (e.g. an "
        "algorithm using sequencing/selection/iteration, or an abstraction such as a list "
        "or a procedure with a parameter), and how the code works. Require specificity tied "
        "to the student's OWN program; generic definitions with no reference to the response "
        "do not earn credit."
    ),
}

# Where on AP Central to direct the user to download a rubric they haven't
# already placed in rubrics/.
AP_CENTRAL_EXAM_PAGE = "https://apcentral.collegeboard.org/courses/ap-{slug}/exam/past-exam-questions"


def rubric_filename(subject: str, year: int, set_label: str | None = None) -> str:
    """Compute the expected filename for a rubric PDF inside rubrics/."""
    if subject not in SUBJECT_SLUG:
        raise KeyError(
            f"Unknown subject {subject!r}. Add it to SUBJECT_SLUG in config.py. "
            f"Known subjects: {sorted(SUBJECT_SLUG)}"
        )
    slug = SUBJECT_SLUG[subject]
    suffix = ""
    if set_label:
        suffix = "-" + set_label.lower().replace(" ", "")
    return f"{slug}-{year}{suffix}.pdf"
