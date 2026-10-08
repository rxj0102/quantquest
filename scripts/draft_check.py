"""Check drafted questions before they are considered for ``data/curated``.

Run:  python -m scripts.draft_check data/drafts/prob_conditional.yaml

For each draft it prints the prompt, answer, reference, the real verifier's result, an independent
third-check result, and its nearest neighbours in the existing pool and among the other drafts. It
also lints the rules for drafts (answer format stated, symbolic answers are a single expression,
a reference is present, ids and nodes are valid). It never touches a database and never promotes
anything. Exit code 1 if any draft has an error or fails verification.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.loader import DEFAULT_DIR, LoadError, load_questions
from core.schema import AnswerType, Format, Status, Verification
from core.similarity import Similarity, compare, nearest
from core.skill_tree import SkillTree, load_tree
from scripts.check_answers import exact_agrees
from scripts.draft_checks import THIRD_CHECKS
from verify.curated import CuratedEntry, load_curated_file
from verify.dispatch import verify_question
from verify.parsing import ParseRejected, safe_parse

SEEDING_DIFFICULTIES = range(1, 5)
_FORMAT_PHRASES = re.compile(
    r"fraction|decimal|significant (?:figure|digit)|exact (?:value|expression|form|answer)|"
    r"single expression|in terms of|integer|whole number|nearest",
    re.IGNORECASE,
)
_SET_WORDS = re.compile(
    r"\b(values|all solutions|both roots|all roots|list|set of)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Issue:
    """A problem found in a draft. Errors block promotion; warnings are for the reviewer."""

    level: str  # "error" | "warn"
    message: str


@dataclass
class DraftReport:
    """Everything the review sheet shows for one draft."""

    entry: CuratedEntry
    verification: Verification
    issues: list[Issue] = field(default_factory=list)
    nearest: list[tuple[str, Similarity]] = field(default_factory=list)
    same_answer: list[str] = field(default_factory=list)
    third_check: str = "none"  # "agrees" | "DISAGREES" | "error" | "none"

    @property
    def has_errors(self) -> bool:
        """True if the draft has an error issue or failed verification."""
        return any(i.level == "error" for i in self.issues) or self.verification.result == "fail"


def format_is_stated(prompt: str) -> bool:
    """True if the prompt says how the answer should be written."""
    return bool(_FORMAT_PHRASES.search(prompt))


def _answer_key(answer: str) -> str:
    try:
        return str(safe_parse(answer, reference=True))
    except ParseRejected:
        return answer.strip()


MAX_DISPLAY_LINE = 90


def _display_math_ok(text: str) -> bool:
    """True if every ``$$`` sits alone on its line and the fences pair up.

    Streamlit's markdown renders a ``$$...$$`` that shares a line with its formula (or spans
    lines with indentation) as a raw red KaTeX error, so the fences must be on their own lines.
    A formula line longer than ``MAX_DISPLAY_LINE`` characters is clipped, so split it with
    ``aligned``.
    """
    inside, fences = False, 0
    for line in text.splitlines():
        if "$$" in line:
            if line.strip() != "$$":
                return False
            fences += 1
            inside = not inside
        elif inside and len(line.strip()) > MAX_DISPLAY_LINE:
            return False  # too wide: the right-hand side is clipped in the app
    return fences % 2 == 0


def _lint(
    entry: CuratedEntry, *, curated_ids: set[str], tree: SkillTree, verification: Verification
) -> list[Issue]:
    q, ref = entry.question, entry.reference
    out: list[Issue] = []

    def err(msg: str) -> None:
        out.append(Issue("error", msg))

    if q.id in curated_ids:
        err(f"id {q.id} is already used by the curated pool")
    if q.node_id not in tree.nodes:
        err(f"node_id {q.node_id!r} is not a skill-tree node")
    for column, text in (("prompt_md", q.prompt_md), ("solution_md", q.solution_md)):
        if not _display_math_ok(text):
            err(
                f"{column}: display math needs $$ alone on its line and formula lines "
                f"under {MAX_DISPLAY_LINE} characters"
            )
    if q.status is not Status.FRESH:
        err(f"status must be fresh in a draft, got {q.status.value}")
    if q.source != "curated":
        err("source must be 'curated'")
    if q.difficulty not in SEEDING_DIFFICULTIES:
        err("difficulty must be 1 to 4 for seeding")
    if not format_is_stated(q.prompt_md):
        err(
            "the prompt does not state the answer format "
            "(fraction, decimal precision or exact expression)"
        )
    if q.answer_type is AnswerType.SYMBOLIC:
        if "," in q.answer.replace(" ", "") and not re.search(r"\(.*,.*\)", q.answer):
            err("a symbolic answer must be a single expression, not several values")
        if "single expression" not in q.prompt_md.lower() or _SET_WORDS.search(q.prompt_md):
            err(
                "a symbolic prompt must ask for a single expression "
                "(not 'values' or 'all solutions')"
            )
    if q.answer_type is AnswerType.TEXT:
        out.append(Issue("warn", "text answers can never be trusted by promotion"))
    elif q.answer_type in (AnswerType.NUMERIC, AnswerType.SYMBOLIC):
        if ref is None or ref.exact is None:
            err("a numeric or symbolic draft needs a reference with an exact expression")
        elif q.format is Format.PROBABILITY and ref.simulator is None:
            err("a probability-format draft needs a simulator reference (Monte Carlo is required)")
    if verification.result == "unverified" and q.answer_type is not AnswerType.TEXT:
        err(f"could not be verified: {verification.details}")
    if verification.result == "fail":
        err(f"verification failed: {verification.details}")
    return out


def check_drafts(
    path: Path,
    *,
    run_verifier: bool = True,
    third_checks: Mapping[str, Callable[[], object]] | None = None,
    curated_dir: Path = DEFAULT_DIR,
    tree: SkillTree | None = None,
) -> list[DraftReport]:
    """Check one draft file, or every ``*.yaml`` in a directory."""
    tree = tree or load_tree()
    checks = THIRD_CHECKS if third_checks is None else third_checks
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    entries = [e for f in files for e in load_curated_file(f)]
    curated = {q.id: q for q in load_questions(curated_dir)}
    pool = {qid: q.prompt_md for qid, q in curated.items()}
    ids = [e.question.id for e in entries]
    reports: list[DraftReport] = []
    for entry in entries:
        q = entry.question
        if run_verifier:
            verification = verify_question(q, entry.reference)
        else:
            verification = Verification(method="skipped", result="unverified", details="not run")
        others = {
            **pool,
            **{o.question.id: o.question.prompt_md for o in entries if o is not entry},
        }
        issues = _lint(entry, curated_ids=set(curated), tree=tree, verification=verification)
        if not run_verifier:
            issues = [i for i in issues if not i.message.startswith("could not be verified")]
        if ids.count(q.id) > 1:
            issues.append(Issue("error", f"duplicate draft id {q.id}"))
        for other_id, text in others.items():
            sim = compare(q.prompt_md, text)
            if sim.flagged:
                issues.append(
                    Issue(
                        "error",
                        f"near-duplicate of {other_id} "
                        f"(jaccard {sim.jaccard:.2f}, ratio {sim.ratio:.2f})",
                    )
                )
        key = _answer_key(q.answer)
        same = sorted(
            [
                qid
                for qid, c in curated.items()
                if c.node_id == q.node_id and _answer_key(c.answer) == key
            ]
            + [
                o.question.id
                for o in entries
                if o is not entry
                and o.question.node_id == q.node_id
                and _answer_key(o.question.answer) == key
            ]
        )
        for qid in same:
            issues.append(
                Issue("warn", f"same answer as {qid} in the same node: check it is not a rewording")
            )
        third = _third_check(q.id, q.answer, q.answer_type, checks, issues)
        reports.append(
            DraftReport(entry, verification, issues, nearest(q.prompt_md, others, 3), same, third)
        )
    return reports


def _third_check(
    qid: str,
    answer: str,
    answer_type: AnswerType,
    checks: Mapping[str, Callable[[], object]],
    issues: list[Issue],
) -> str:
    if qid not in checks:
        issues.append(Issue("warn", "no independent third check registered for this draft"))
        return "none"
    try:
        agrees = exact_agrees(answer, answer_type, checks[qid]())
    except Exception as exc:  # noqa: BLE001 - a crashing check certifies nothing
        issues.append(Issue("error", f"third check crashed: {type(exc).__name__}: {exc}"))
        return "error"
    if not agrees:
        issues.append(Issue("error", "third check disagrees with the stated answer"))
        return "DISAGREES"
    return "agrees"


def format_report(reports: list[DraftReport]) -> str:
    """The review sheet: one block per draft, then a summary."""
    lines: list[str] = []
    for r in reports:
        q = r.entry.question
        lines += [
            "=" * 100,
            f"{q.id} · {q.node_id} · difficulty {q.difficulty} · {q.format.value} "
            f"· {q.answer_type.value}",
            "-" * 100,
            "PROMPT:",
            *("  " + ln for ln in q.prompt_md.strip().splitlines()),
            f"ANSWER: {q.answer}",
            "REFERENCE:",
        ]
        ref = r.entry.reference
        dumped = (
            yaml.safe_dump(
                ref.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
                sort_keys=False,
                width=200,
            )
            if ref
            else "none\n"
        )
        lines += ["  " + ln for ln in dumped.rstrip().splitlines()]
        v = r.verification
        lines += [
            f"VERIFIER: {v.result}  [{v.method}]",
            f"  {v.details}",
            f"THIRD CHECK: {r.third_check}",
            "NEAREST (jaccard / ratio):",
            *(
                f"  {qid:<12} {s.jaccard:.2f} / {s.ratio:.2f}{'  <-- FLAGGED' if s.flagged else ''}"
                for qid, s in r.nearest
            ),
        ]
        if r.issues:
            lines.append("ISSUES:")
            lines += [f"  [{i.level.upper()}] {i.message}" for i in r.issues]
        else:
            lines.append("ISSUES: none")
    n = len(reports)
    count = lambda pred: sum(1 for r in reports if pred(r))  # noqa: E731
    lines += [
        "=" * 100,
        f"SUMMARY: {n} draft{'s' if n != 1 else ''}; "
        f"verifier pass {count(lambda r: r.verification.result == 'pass')}, "
        f"fail {count(lambda r: r.verification.result == 'fail')}, "
        f"unverified {count(lambda r: r.verification.result == 'unverified')}; "
        f"third check agrees {count(lambda r: r.third_check == 'agrees')}; "
        f"with errors {count(lambda r: r.has_errors)}; "
        f"warnings {sum(1 for r in reports for i in r.issues if i.level == 'warn')}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 1 if any draft has an error or fails verification, 2 if unreadable."""
    parser = argparse.ArgumentParser(description="Check drafted questions")
    parser.add_argument("path", type=Path, help="a draft YAML file or a directory of them")
    parser.add_argument("--no-third-check", action="store_true", help="skip the independent checks")
    parser.add_argument(
        "--no-verify", action="store_true", help="lint only; do not run the verifier"
    )
    args = parser.parse_args(argv)
    try:
        reports = check_drafts(
            args.path,
            run_verifier=not args.no_verify,
            third_checks={} if args.no_third_check else None,
        )
    except LoadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_report(reports))
    return 1 if any(r.has_errors for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
