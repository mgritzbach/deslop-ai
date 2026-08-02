#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from deslop_core import (  # noqa: E402
    ACADEMIC_WORDS_PATH, CATALOG_PATH, PRIVATE_ROOT, Analyzer, DeslopError,
    conservative_rewrite, editorial_vector, protected_equal, protected_tokens,
    sha256_file, sha256_text, stable_id, strict_json_load, validate_request,
    validate_semantic, write_json,
)
from formats import apply_edits, compare_invariants, extract_source  # noqa: E402
from profiler import build_profile  # noqa: E402


SKILL_ROOT = SCRIPT_DIR.parent
OFFICE_SCRIPT = SCRIPT_DIR / "office_verify.ps1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eligible(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in source["blocks"] if block.get("eligible", True)]


def _assessment_finding(block: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "findingId": stable_id("finding", block["blockId"], "VALUE_SEMANTIC", item["verdict"]),
        "blockId": block["blockId"], "locator": block["locator"], "sourceText": block["text"],
        "sourceHash": block["sourceHash"], "ruleId": "VALUE_SEMANTIC",
        "family": "meaning and information value", "evidence": "E",
        "severity": "high" if item["verdict"] == "needs-improvement" else "medium",
        "confidence": 0.9 if item["verdict"] == "needs-improvement" else 0.7,
        "explanation": item["reason"], "action": "Improve or remove this block; do not publish empty words.",
        "suggestion": item["improvement"], "matchedText": "",
    }


def _load_request(path: Path) -> dict[str, Any]:
    return validate_request(strict_json_load(path))


def _load_profile(request: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if request["profileId"] == "none":
        return None, None
    path = PRIVATE_ROOT / "profiles" / request["profileId"] / "profile.json"
    if not path.is_file():
        raise DeslopError(f"Private profile not found: {path}")
    profile = strict_json_load(path)
    if profile.get("schemaVersion") != "deslop-profile/v1" or profile.get("id") != request["profileId"]:
        raise DeslopError("Private profile schema/id mismatch")
    if request["genre"] not in {"auto", profile.get("genre")}:
        raise DeslopError(f"Profile genre {profile.get('genre')} does not match request genre {request['genre']}")
    if profile.get("privacy", {}).get("rawTextStored") is not False:
        raise DeslopError("Profile privacy declaration is missing or unsafe")
    return profile, sha256_file(path)


def _profile_findings(profile: dict[str, Any] | None, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not profile:
        return []
    stats = profile.get("preferences", {}).get("blockLength", {})
    median = float(stats.get("median", 0)); mad = float(stats.get("mad", 0)); q1 = float(stats.get("q1", 0)); q3 = float(stats.get("q3", 0))
    upper = max(median + 3 * max(mad, 1), q3 + 1.5 * max(q3 - q1, 1))
    findings = []
    for block in blocks:
        length = len(block["text"].split())
        if upper > 0 and length > upper:
            findings.append({
                "findingId": stable_id("finding", block["blockId"], "PROFILE_BLOCK_LENGTH"),
                "blockId": block["blockId"], "locator": block["locator"], "sourceText": block["text"], "sourceHash": block["sourceHash"],
                "ruleId": "PROFILE_BLOCK_LENGTH", "family": "personal context fit", "evidence": "D", "severity": "low", "confidence": 0.75,
                "explanation": f"Block length ({length} words) is above the robust personal-profile range (upper fence {upper:.1f}).",
                "action": "Review density and rhythm; do not shorten if the detail is necessary.", "suggestion": "", "matchedText": "",
            })
    return findings


def _merge_semantic(request: dict[str, Any], blocks: list[dict[str, Any]], heuristic: list[dict[str, Any]], semantic_path: Path | None) -> tuple[list[dict[str, Any]], str]:
    if semantic_path is None:
        return heuristic, "local-heuristic"
    by_id = {block["blockId"]: block for block in blocks}
    semantic = validate_semantic(strict_json_load(semantic_path), request["id"], by_id)
    received = {item["blockId"] for item in semantic["assessments"]}
    required = set(by_id)
    if received != required:
        missing = sorted(required - received)
        extra = sorted(received - required)
        raise DeslopError(f"Semantic coverage is incomplete (missing={missing[:10]}, extra={extra[:10]})")
    return semantic["assessments"], "agent-semantic"


def _rewrite_plan(request: dict[str, Any], source: dict[str, Any], findings: list[dict[str, Any]], assessments: list[dict[str, Any]]) -> dict[str, Any]:
    by_block_findings: dict[str, list[str]] = {}
    for finding in findings:
        by_block_findings.setdefault(finding["blockId"], []).append(finding["findingId"])
    assessment_by_id = {item["blockId"]: item for item in assessments}
    edits, abstentions = [], []
    for block in _eligible(source):
        assessment = assessment_by_id[block["blockId"]]
        if request["operation"] == "audit-and-rewrite" and not block.get("supportedForRewrite", False):
            suggested = assessment.get("improvement", "") if assessment["verdict"] == "needs-improvement" else "No content change proposed; retained because this container is audit-only or format-sensitive."
            abstentions.append({"blockId": block["blockId"], "locator": block["locator"], "reason": block.get("unsupportedReason", "Unsafe span"), "contentVerdict": assessment["verdict"], "suggestedAction": suggested})
            continue
        semantic_replacement = assessment.get("replacement", "").strip()
        if semantic_replacement:
            replacement, reasons = semantic_replacement, ["agent-semantic-source-bound"]
            length_delta = abs(len(replacement) - len(block["text"])) / max(1, len(block["text"]))
            if length_delta > 0.60:
                abstentions.append({"blockId": block["blockId"], "locator": block["locator"], "reason": "Semantic replacement exceeds the conservative 60% length-change limit.", "suggestedAction": assessment["improvement"]})
                continue
        else:
            replacement, reasons = conservative_rewrite(block["text"], request["genre"], request["preserveTerms"])
        if request["operation"] == "audit" or replacement == block["text"]:
            if assessment["verdict"] == "needs-improvement":
                abstentions.append({
                    "blockId": block["blockId"], "locator": block["locator"],
                    "reason": "Audit-only request" if request["operation"] == "audit" else "No validated source-supported conservative replacement was provided.",
                    "suggestedAction": assessment["improvement"],
                })
            continue
        okay, token_check = protected_equal(block["text"], replacement, request["preserveTerms"])
        if not okay:
            abstentions.append({"blockId": block["blockId"], "locator": block["locator"], "reason": "Protected-token validation rejected the replacement.", "tokenCheck": token_check, "suggestedAction": "Review manually."})
            continue
        edits.append({
            "editId": stable_id("edit", block["blockId"], sha256_text(replacement)),
            "blockId": block["blockId"], "locator": block["locator"], "address": block["address"],
            "oldHash": block["sourceHash"], "original": block["text"], "replacement": replacement,
            "findingIds": by_block_findings.get(block["blockId"], []), "rewriteRules": reasons,
            "protectedTokens": protected_tokens(block["text"], request["preserveTerms"]), "status": "planned",
        })
    for block in _eligible(source):
        if not block.get("supportedForRewrite", False) and not any(item["blockId"] == block["blockId"] for item in abstentions):
            abstentions.append({"blockId": block["blockId"], "locator": block["locator"], "reason": block.get("unsupportedReason", "Unsafe span"), "suggestedAction": "Audit finding only; review this span manually."})
    return {"schemaVersion": "deslop-rewrite-plan/v1", "policy": "conservative", "edits": edits, "abstentions": abstentions}


def _output_name(request: dict[str, Any], source: dict[str, Any]) -> str:
    if request["input"]["kind"] == "text":
        return "revised-text.txt"
    path = Path(source["path"])
    return f"{path.stem}-deslopped{path.suffix}"


def _office_verify(path: Path, out: Path) -> dict[str, Any]:
    if path.suffix.lower() not in {".docx", ".pptx"}:
        result = {"applicable": False, "passed": True, "reason": "Office check not required for this format"}
        write_json(out, result)
        return result
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell or not OFFICE_SCRIPT.exists():
        result = {"applicable": True, "passed": False, "reason": "PowerShell/Office verifier unavailable"}
        write_json(out, result)
        return result
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OFFICE_SCRIPT), "-Path", str(path), "-OutJson", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if out.exists():
            result = strict_json_load(out)
        else:
            result = {"applicable": True, "passed": False, "reason": (completed.stderr or completed.stdout or "Office verifier produced no receipt").strip()[:1000]}
            write_json(out, result)
        return result
    except subprocess.TimeoutExpired:
        result = {"applicable": True, "passed": False, "reason": "Office verification timed out after 60 seconds"}
        write_json(out, result)
        return result


def _rescore(request: dict[str, Any], revised: Path) -> dict[str, Any]:
    revised_request = dict(request)
    revised_request["input"] = {"kind": "file", "path": str(revised)}
    revised_source = extract_source(revised_request)
    findings, assessments = Analyzer().analyze(_eligible(revised_source), request["genre"])
    deterministic = [item for item in findings if item["evidence"] != "E"]
    return {
        "engine": "local deterministic audit plus local meaning/value heuristic; not directly comparable to an agent semantic pass",
        "deterministicFindingCount": len(deterministic),
        "localHeuristicNeedsImprovementCount": sum(item["verdict"] == "needs-improvement" for item in assessments),
        "editorialVector": editorial_vector(deterministic, assessments),
        "sourceMap": revised_source,
    }


def _report(request: dict[str, Any], source: dict[str, Any], findings: list[dict[str, Any]], assessments: list[dict[str, Any]], plan: dict[str, Any], verification: dict[str, Any]) -> str:
    counts = Counter(item["verdict"] for item in assessments)
    lines = [
        "# DeSlopAI report", "", f"Request: `{request['id']}`", f"Status: **{verification['status']}**", "",
        "## Meaning and value coverage", "",
        f"Assessed **{len(assessments)} of {len(_eligible(source))}** eligible text blocks.", "",
        f"- Meaningful: {counts['meaningful']}", f"- Needs improvement: {counts['needs-improvement']}",
        f"- Abstain: {counts['abstain']}", f"- Exempt furniture: {counts['exempt']}", "",
        "Every visible eligible headline, bullet, paragraph, callout, caption, and table cell is represented. Exemptions remain explicit.", "",
        "## Editorial risk vector", "", "```json", json.dumps(verification["editorialVector"], indent=2), "```", "",
        "## Findings", "",
    ]
    for item in sorted(findings, key=lambda x: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x["severity"], 4), x["locator"])):
        lines.append(f"- **{item['severity'].upper()} · {item['locator']} · {item['family']}** — {item['explanation']} Action: {item['action']}")
    if not findings:
        lines.append("- No catalog or meaning/value findings.")
    lines += ["", "## Rewrite manifest", "", f"Planned/applied edits: {len(plan['edits'])}. Abstentions: {len(plan['abstentions'])}.", ""]
    for item in plan["edits"]:
        lines.append(f"- `{item['locator']}`: `{item['original']}` → `{item['replacement']}`")
    for item in plan["abstentions"]:
        lines.append(f"- **ABSTAIN · {item['locator']}** — {item['reason']} Suggested action: {item['suggestedAction']}")
    lines += ["", "## Verification", "", f"Mandatory gates: **{'passed' if verification['passed'] else 'failed'}**.", ""]
    for check in verification["checks"]:
        lines.append(f"- {'PASS' if check['passed'] else 'FAIL'} — {check['name']}: {check['detail']}")
    lines += ["", "This report describes observable writing risks. It does not estimate whether AI wrote the document.", ""]
    return "\n".join(lines)


def run_request(request_path: Path, out: Path, semantic_path: Path | None) -> int:
    request = _load_request(request_path)
    profile_data, profile_hash = _load_profile(request)
    if out.exists():
        raise DeslopError(f"Output directory already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=str(out.parent)))
    original_path = Path(request["input"]["path"]) if request["input"]["kind"] == "file" else None
    original_hash = sha256_file(original_path) if original_path else sha256_text(request["input"]["text"])
    try:
        write_json(temp / "request.json", request)
        source = extract_source(request)
        write_json(temp / "source-map.json", source)
        blocks = _eligible(source)
        analyzer = Analyzer()
        all_findings, heuristic = analyzer.analyze(blocks, request["genre"])
        deterministic = [item for item in all_findings if item["evidence"] != "E"] + _profile_findings(profile_data, blocks)
        assessments, semantic_engine = _merge_semantic(request, blocks, heuristic, semantic_path)
        semantic_findings = [_assessment_finding({block["blockId"]: block for block in blocks}[item["blockId"]], item) for item in assessments if item["verdict"] == "needs-improvement"]
        findings = deterministic + semantic_findings
        write_json(temp / "deterministic-findings.json", {"schemaVersion": "deslop-findings/v1", "findings": deterministic})
        write_json(temp / "semantic-findings.json", {"schemaVersion": "deslop-semantic-result/v1", "engine": semantic_engine, "assessments": assessments, "findings": semantic_findings})
        value_coverage = {
            "schemaVersion": "deslop-value-coverage/v1", "requestId": request["id"], "engine": semantic_engine,
            "eligibleBlockCount": len(blocks), "assessedBlockCount": len(assessments),
            "complete": len(assessments) == len(blocks) and {item["blockId"] for item in assessments} == {block["blockId"] for block in blocks},
            "verdictCounts": dict(Counter(item["verdict"] for item in assessments)), "assessments": assessments,
        }
        write_json(temp / "value-coverage.json", value_coverage)
        write_json(temp / "findings.json", {"schemaVersion": "deslop-findings/v1", "findings": findings})
        plan = _rewrite_plan(request, source, findings, assessments)
        write_json(temp / "rewrite-plan.json", plan)

        checks = [
            {"name": "complete value coverage", "passed": len(assessments) == len(blocks) and len({a['blockId'] for a in assessments}) == len(blocks), "detail": f"{len(assessments)}/{len(blocks)} eligible blocks assessed"},
            {"name": "unique source spans", "passed": len({block['locator'] for block in blocks}) == len(blocks), "detail": f"{len(blocks)} locators mapped exactly once"},
        ]
        revised_path = None
        rescored = None
        if request["operation"] == "audit-and-rewrite" and source["kind"] != "pdf":
            revised_path = temp / _output_name(request, source)
            apply_edits(source, plan["edits"], revised_path)
            revised_source = extract_source({**request, "input": {"kind": "file", "path": str(revised_path)}})
            invariant_problems = compare_invariants(source["invariants"], revised_source["invariants"], source["kind"])
            checks.append({"name": "format invariants", "passed": not invariant_problems, "detail": "; ".join(invariant_problems) or "unchanged"})
            if source["kind"] == "text":
                expected_text_after = source["text"]
                for edit in sorted(plan["edits"], key=lambda item: item["address"]["start"], reverse=True):
                    start, end = edit["address"]["start"], edit["address"]["end"]
                    expected_text_after = expected_text_after[:start] + edit["replacement"] + expected_text_after[end:]
                actual_text_after = revised_path.read_text(encoding="utf-8")
                text_differences = [] if expected_text_after == actual_text_after else ["pasted-text"]
            else:
                revised_by_locator = {block["locator"]: block["text"] for block in _eligible(revised_source)}
                replacements = {edit["locator"]: edit["replacement"] for edit in plan["edits"]}
                expected_by_locator = {block["locator"]: replacements.get(block["locator"], block["text"]) for block in blocks}
                text_differences = sorted(locator for locator in set(expected_by_locator) | set(revised_by_locator) if expected_by_locator.get(locator) != revised_by_locator.get(locator))
            checks.append({"name": "no unplanned text edits", "passed": not text_differences, "detail": "all source spans match the validated plan" if not text_differences else f"unexpected differences: {text_differences[:20]}"})
            expected_text = "\n".join(block["text"] for block in blocks)
            revised_text = "\n".join(block["text"] for block in _eligible(revised_source))
            okay, details = protected_equal(expected_text, revised_text, request["preserveTerms"])
            checks.append({"name": "protected tokens", "passed": okay, "detail": "unchanged" if okay else json.dumps(details, ensure_ascii=False)[:1000]})
            office = _office_verify(revised_path, temp / "office-verification.json")
            if source["kind"] == "pptx" and source.get("path"):
                source_office = _office_verify(Path(source["path"]), temp / "source-office-verification.json")
                new_overflow = sorted(set(office.get("overflow", [])) - set(source_office.get("overflow", [])))
                new_overlaps = sorted(set(office.get("objectOverlaps", [])) - set(source_office.get("objectOverlaps", [])))
                office_passed = bool(office.get("openedWithoutRepair")) and bool(source_office.get("openedWithoutRepair")) and not new_overflow and not new_overlaps
                office["baselineComparison"] = {
                    "sourceOverflowCount": len(source_office.get("overflow", [])),
                    "revisedOverflowCount": len(office.get("overflow", [])),
                    "newOverflow": new_overflow,
                    "sourceObjectOverlapCount": len(source_office.get("objectOverlaps", [])),
                    "revisedObjectOverlapCount": len(office.get("objectOverlaps", [])),
                    "newObjectOverlaps": new_overlaps,
                }
                write_json(temp / "office-verification.json", office)
                office_detail = f"PowerPoint opened source and revision without repair; new overflow={len(new_overflow)}, new text/chart overlaps={len(new_overlaps)}. Existing source-layout warnings are recorded separately."
            else:
                office_passed = bool(office.get("passed"))
                office_detail = office.get("reason", "Office opened the document without repair; overflow result recorded")
            checks.append({"name": "Office open/render", "passed": office_passed, "detail": office_detail})
            rescored = _rescore(request, revised_path)
            write_json(temp / "revised-source-map.json", rescored.pop("sourceMap"))
        elif source["kind"] == "pdf" and request["operation"] == "audit-and-rewrite":
            checks.append({"name": "PDF audit-only policy", "passed": True, "detail": "No PDF revision was produced; suggestions only"})

        unchanged = (sha256_file(original_path) if original_path else sha256_text(request["input"]["text"])) == original_hash
        checks.append({"name": "original unchanged", "passed": unchanged, "detail": original_hash})
        checks.append({"name": "source-bound edits", "passed": all(edit["oldHash"] == next(block["sourceHash"] for block in blocks if block["blockId"] == edit["blockId"]) for edit in plan["edits"]), "detail": f"{len(plan['edits'])} edit hashes matched"})
        passed = all(check["passed"] for check in checks)
        if not passed and revised_path and revised_path.exists():
            revised_path.unlink()
            revised_path = None
        verification = {
            "schemaVersion": "deslop-verification/v1", "requestId": request["id"], "createdAt": _now(),
            "status": "passed" if passed else "failed", "passed": passed, "checks": checks,
            "eligibleBlockCount": len(blocks), "assessedBlockCount": len(assessments),
            "findingsBefore": len(findings), "rescoreAfter": rescored,
            "semanticEngine": semantic_engine,
            "editorialVector": editorial_vector(findings, assessments),
            "revisedArtifact": revised_path.name if revised_path else None,
        }
        write_json(temp / "verification.json", verification)
        (temp / "report.md").write_text(_report(request, source, findings, assessments, plan, verification), encoding="utf-8")
        files = [path for path in temp.iterdir() if path.is_file() and path.name != "manifest.json"]
        manifest = {
            "schemaVersion": "deslop-manifest/v1", "requestId": request["id"], "createdAt": _now(),
            "sourceHash": original_hash, "catalogHash": analyzer.catalog_hash,
            "academicMarkersHash": sha256_file(ACADEMIC_WORDS_PATH), "skillHash": sha256_file(SKILL_ROOT / "SKILL.md"),
            "profileId": request["profileId"], "profileHash": profile_hash, "files": {path.name: sha256_file(path) for path in sorted(files)},
        }
        write_json(temp / "manifest.json", manifest)
        os.replace(temp, out)
        return 0 if passed else 2
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def request_verify(run: Path, out: Path) -> int:
    request = _load_request(run / "request.json")
    manifest = strict_json_load(run / "manifest.json")
    source = extract_source(request)
    checks = []
    checks.append({"name": "source hash", "passed": source["sourceHash"] == manifest["sourceHash"], "detail": source["sourceHash"]})
    for name, expected in manifest["files"].items():
        path = run / name
        checks.append({"name": f"artifact hash {name}", "passed": path.is_file() and sha256_file(path) == expected, "detail": expected})
    semantic = strict_json_load(run / "semantic-findings.json")
    blocks = _eligible(source)
    assessed = semantic["assessments"]
    checks.append({"name": "independent value coverage", "passed": len(assessed) == len(blocks) and {item['blockId'] for item in assessed} == {block['blockId'] for block in blocks}, "detail": f"{len(assessed)}/{len(blocks)}"})
    revised_name = strict_json_load(run / "verification.json").get("revisedArtifact")
    if revised_name:
        revised = run / revised_name
        revised_source = extract_source({**request, "input": {"kind": "file", "path": str(revised)}})
        problems = compare_invariants(source["invariants"], revised_source["invariants"], source["kind"])
        checks.append({"name": "independent format invariants", "passed": not problems, "detail": "; ".join(problems) or "unchanged"})
    result = {"schemaVersion": "deslop-independent-verification/v1", "requestId": request["id"], "createdAt": _now(), "passed": all(item["passed"] for item in checks), "checks": checks}
    write_json(out, result)
    return 0 if result["passed"] else 2


def preflight() -> int:
    analyzer = Analyzer()
    imports = {}
    for module in ["docx", "pptx", "pypdf"]:
        try:
            __import__(module)
            imports[module] = True
        except ImportError:
            imports[module] = False
    result = {
        "schemaVersion": "deslop-preflight/v1", "passed": all(imports.values()), "python": sys.executable,
        "imports": imports, "catalogHash": analyzer.catalog_hash,
        "catalogCounts": {"lexicalGroups": len(analyzer.lex_groups), "structuralRules": len(analyzer.structure_rules), "artifactRules": len(analyzer.artifact_rules), "academicMarkers": len(analyzer.academic_words)},
        "office": {"powerPoint": Path(r"C:\Program Files\Microsoft Office\Root\Office16\POWERPNT.EXE").exists(), "word": Path(r"C:\Program Files\Microsoft Office\Root\Office16\WINWORD.EXE").exists()},
        "privateRoot": str(PRIVATE_ROOT),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 2


def ingest(request_path: Path, out: Path) -> int:
    request = _load_request(request_path)
    profile_data, profile_hash = _load_profile(request)
    source = extract_source(request)
    packet = {"schemaVersion": "deslop-semantic-packet/v1", "requestId": request["id"], "communicationJob": request["communicationJob"], "profile": profile_data, "profileHash": profile_hash, "blocks": [{key: block[key] for key in ["blockId", "sourceHash", "locator", "text", "role", "scope", "supportedForRewrite", "unsupportedReason"] if key in block} for block in _eligible(source)]}
    write_json(out, packet)
    return 0


def profile(request_path: Path, out: Path) -> int:
    resolved = out.expanduser().resolve()
    private = PRIVATE_ROOT.resolve()
    try:
        resolved.relative_to(private)
    except ValueError as exc:
        raise DeslopError(f"Profile output must stay under the private root: {private}") from exc
    if resolved.exists():
        raise DeslopError(f"Profile output already exists: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    request = strict_json_load(request_path)
    result = build_profile(request)
    temp = Path(tempfile.mkdtemp(prefix=f".{resolved.name}-", dir=str(resolved.parent)))
    write_json(temp / "profile.json", result)
    os.replace(temp, resolved)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="DeSlopAI guarded document auditor")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    p_ingest = commands.add_parser("ingest"); p_ingest.add_argument("request", type=Path); p_ingest.add_argument("--out", type=Path, required=True)
    p_request = commands.add_parser("request"); p_request.add_argument("request", type=Path); p_request.add_argument("--semantic-findings", type=Path); p_request.add_argument("--out", type=Path, required=True)
    p_verify = commands.add_parser("request-verify"); p_verify.add_argument("run", type=Path); p_verify.add_argument("--out", type=Path, required=True)
    p_profile = commands.add_parser("profile"); p_profile.add_argument("request", type=Path); p_profile.add_argument("--out", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "preflight": return preflight()
        if args.command == "ingest": return ingest(args.request, args.out)
        if args.command == "request": return run_request(args.request, args.out, args.semantic_findings)
        if args.command == "request-verify": return request_verify(args.run, args.out)
        if args.command == "profile": return profile(args.request, args.out)
        raise DeslopError("Unknown command")
    except DeslopError as exc:
        print(f"DeSlopAI error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
