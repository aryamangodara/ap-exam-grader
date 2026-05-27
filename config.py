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
    "AP Statistics":                           "statistics",
    "AP Physics C: Mechanics":                 "physics-c-mechanics",
    "AP Physics C: Electricity and Magnetism": "physics-c-em",
    "AP Biology":                              "biology",
    "AP Chemistry":                            "chemistry",
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

_CALCULUS_ADDENDUM = (
    "Accept algebraically equivalent forms. A sign or arithmetic error that "
    "propagates through subsequent steps should only cost the point where the "
    "error was introduced; downstream points may still be earned on follow-through."
)

# Subject-specific guidance injected into the grading prompt. Each string
# encodes how that subject's official rubric is meant to be applied (point
# structure, follow-through rules, what earns vs. doesn't earn credit).
SUBJECT_GRADING_ADDENDA: dict[str, str] = {
    "AP Calculus AB": _CALCULUS_ADDENDUM,
    "AP Calculus BC": _CALCULUS_ADDENDUM,
    "AP Precalculus": (
        "Accept algebraically equivalent forms and equivalent exact or decimal answers. "
        "Apply follow-through: an arithmetic or sign error costs only the point where it "
        "occurs, and downstream points may still be earned. Where the rubric asks for "
        "justification or reasoning, a bare final answer does not earn the reasoning point."
    ),
    "AP Statistics": (
        "FRQs are scored holistically per part: communication and reasoning carry as "
        "much weight as the numerical answer. Award full credit only when the response "
        "is stated in context (named variable, population, and units where appropriate), "
        "not just symbolically. For inference questions, require all four components — "
        "defined parameter and hypotheses, conditions checked, mechanics (test statistic "
        "and p-value, or the interval), and a conclusion linked to the significance level "
        "and the original claim. Apply follow-through: an incorrect earlier value used "
        "correctly downstream still earns the later points. Graph points require correctly "
        "labeled axes and scale plus a shape/center/spread description tied to the "
        "scenario; a bare numerical answer with no justification does not earn the "
        "reasoning point."
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
    "AP Biology": (
        "FRQs are point-based; award each point independently. For quantitative "
        "parts (typically Q1 / statistical-analysis), require the correct setup "
        "AND the numerical answer with appropriate units; accept answers within "
        "the rubric's stated tolerance and do not over-penalize significant "
        "figures. Apply follow-through (consequent) credit: an incorrect earlier "
        "value used correctly downstream still earns the later points. "
        "Justify / Explain / Predict points require a stated mechanism at the "
        "molecular, cellular, or organismal level (e.g. enzyme structure-function, "
        "membrane transport, signal transduction, natural selection on heritable "
        "variation) tied to the prompt — a correct conclusion with no valid "
        "reasoning earns no reasoning point. For experimental-design parts, "
        "require a testable hypothesis tied to the independent and dependent "
        "variables, an explicit control, and a justification for replication or "
        "sample size where the rubric specifies. Graph points require correctly "
        "labeled axes with units, an appropriate scale, and data plotted "
        "accurately; 'describe the data' demands a trend tied to the variables, "
        "not a bare numerical restatement."
    ),
    "AP Chemistry": (
        "FRQs are point-based; award each point independently. For calculations, "
        "require the correct setup AND the final answer with appropriate units, and "
        "apply follow-through (consequent) credit so an incorrect earlier value used "
        "correctly downstream still earns later points. Accept answers within the "
        "rubric's stated tolerance and do not over-penalize significant figures. "
        "Explanation/justification points require correct particulate- or "
        "molecular-level reasoning (intermolecular forces, Coulombic attraction, "
        "collision theory, etc.) tied to the prompt — a correct answer with no valid "
        "reasoning earns no reasoning point. Require balanced equations and correct "
        "chemical formulas where the rubric specifies."
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

# Subject-specific OCR addenda. Injected into the OCR prompt verbatim AFTER
# the generic Visual content rules in prompts/ocr.txt, so each subject can
# pin down the notation conventions its rubrics care about. Subjects without
# an entry use the base prompt unchanged (i.e. the .get(subject, "") default).
SUBJECT_OCR_ADDENDA: dict[str, str] = {
    "AP Chemistry": (
        "Diagram fidelity matters more than for other subjects — rubric points "
        "are awarded for specific bonds, lone pairs, charges and geometries the "
        "student drew. For Lewis / dot structures: name every atom by element "
        "symbol, every bond by its multiplicity and the two atoms it joins "
        "(e.g. 'C=O', 'N-H'), every lone pair (count and on which atom), every "
        "formal charge with the sign and adjacent atom, and the overall shape "
        "(bent, trigonal planar, tetrahedral, octahedral, etc.) when discernible. "
        "For intermolecular-force diagrams (hydrogen bonds, dipole-dipole, etc.): "
        "for each interaction line drawn, state the donor atom (and which H on it), "
        "the acceptor atom (and which lone pair on it), the molecule each belongs "
        "to, and the position on the page (left/right/top/bottom of the central "
        "species). For PES/spectroscopy: count peaks, give relative heights and "
        "x-position (binding energy or wavelength) for each, and identify exactly "
        "which peaks the student circled/marked using both absolute position "
        "(numeric x value or rough range) and relative language ('rightmost two', "
        "'leftmost', 'tallest'). For reaction-energy / potential-energy diagrams: "
        "describe each peak's relative height versus the others, the position and "
        "label of any intermediate, and whether reactants are higher or lower than "
        "products. For volumetric / glassware sketches: say whether the meniscus "
        "is concave or convex, where its bottom sits relative to the calibration "
        "line, and what (if anything) is labelled."
    ),
    "AP Biology": (
        "For biological diagrams (cell, organelle, tissue, organ, organism): "
        "name every structure the student labelled and where it sits relative "
        "to others (nucleus inside cytoplasm, mitochondrion adjacent to ribosome, "
        "etc.). For cycle diagrams (Krebs, Calvin, cell cycle, nitrogen cycle): "
        "name each stage in order, the direction of every arrow, and any "
        "inputs/outputs the student wrote on the arrows. For experimental graphs: "
        "axes (label + units + scale), trend per group/treatment, error bars or "
        "ranges if drawn, and any annotation the student added (asterisks for "
        "significance, labelled controls vs experimentals). For pedigrees, "
        "Punnett squares, gel images: row/column layout and what's in each cell."
    ),
    "AP Physics C: Mechanics": (
        "For free-body / force diagrams: every vector with its tail point, head "
        "direction (up/down/left/right or angle), labelled magnitude (mg, N, T, "
        "f, etc.), and what object it acts on. For motion / kinematics sketches: "
        "coordinate axes, position vs time / velocity vs time / acceleration vs "
        "time curves with their shape (linear, parabolic, constant) and key "
        "values at labelled points. For graphs generally: full axis labels with "
        "units, scale, every plotted point or curve, area under curve if shaded."
    ),
    "AP Physics C: Electricity and Magnetism": (
        "For circuit diagrams: each component (resistor, capacitor, battery, "
        "switch, inductor) with its labelled value, connection topology, and "
        "labelled current direction or polarity. For field diagrams: arrow "
        "directions (electric / magnetic), arrow density relative to source, "
        "labelled magnitudes, and any Gaussian/Amperian surfaces drawn. For "
        "graphs: axes, scale, every plotted feature including discontinuities."
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
