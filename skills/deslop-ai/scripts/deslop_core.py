from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = SKILL_ROOT / "assets" / "rules" / "catalog.json"
ACADEMIC_WORDS_PATH = SKILL_ROOT / "assets" / "data" / "kobak-2025-style-markers.csv"
PRIVATE_ROOT = Path.home() / ".deslop-ai"

SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pptx", ".pdf"}
GENRES = {"consulting", "academic", "chat", "social", "general", "auto"}
OPERATIONS = {"audit", "audit-and-rewrite"}
VERDICTS = {"meaningful", "needs-improvement", "exempt", "abstain"}


class DeslopError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:14]}"


def strict_json_load(path: Path, *, max_bytes: int = 4_000_000) -> Any:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise DeslopError(f"JSON input exceeds {max_bytes} bytes: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeslopError(f"JSON input must be valid UTF-8: {path}") from exc

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DeslopError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=lambda x: (_ for _ in ()).throw(DeslopError(f"Non-finite number: {x}")))
    except json.JSONDecodeError as exc:
        raise DeslopError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_request(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DeslopError("Request must be a JSON object")
    allowed = {
        "schemaVersion", "id", "prompt", "input", "operation", "genre",
        "profileId", "communicationJob", "rewritePolicy", "preserveTerms",
    }
    required = allowed - {"preserveTerms"}
    unknown = sorted(set(data) - allowed)
    missing = sorted(required - set(data))
    if unknown:
        raise DeslopError(f"Unknown request fields: {', '.join(unknown)}")
    if missing:
        raise DeslopError(f"Missing request fields: {', '.join(missing)}")
    if data["schemaVersion"] != "deslop-request/v1":
        raise DeslopError("schemaVersion must be deslop-request/v1")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(data["id"])):
        raise DeslopError("id must be lowercase hyphen-case and <=64 characters")
    if not isinstance(data["prompt"], str) or len(data["prompt"]) > 20_000:
        raise DeslopError("prompt must be a string <=20,000 characters")
    if data["operation"] not in OPERATIONS:
        raise DeslopError(f"Unsupported operation: {data['operation']}")
    if data["genre"] not in GENRES:
        raise DeslopError(f"Unsupported genre: {data['genre']}")
    if data["rewritePolicy"] != "conservative":
        raise DeslopError("Only conservative rewritePolicy is supported")
    if not re.fullmatch(r"(?:none|[a-z0-9][a-z0-9-]{0,63})", str(data["profileId"])):
        raise DeslopError("Invalid profileId")
    job = data["communicationJob"]
    if not isinstance(job, dict) or set(job) != {"audience", "outcome", "takeaway"}:
        raise DeslopError("communicationJob must contain only audience, outcome, and takeaway")
    for key, limit in (("audience", 500), ("outcome", 500), ("takeaway", 1000)):
        if not isinstance(job[key], str) or len(job[key]) > limit:
            raise DeslopError(f"communicationJob.{key} is invalid")
    source = data["input"]
    if not isinstance(source, dict) or source.get("kind") not in {"file", "text"}:
        raise DeslopError("input.kind must be file or text")
    if source["kind"] == "file":
        if set(source) != {"kind", "path"} or not isinstance(source["path"], str):
            raise DeslopError("File input must contain only kind and path")
        path = Path(source["path"]).expanduser().resolve()
        if not path.is_file():
            raise DeslopError(f"Input file does not exist: {path}")
        if path.stat().st_size > 100_000_000:
            raise DeslopError("Input file exceeds the 100 MB v1 safety limit")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise DeslopError(f"Unsupported file extension: {path.suffix}")
        source["path"] = str(path)
    else:
        if set(source) != {"kind", "text"} or not isinstance(source["text"], str):
            raise DeslopError("Text input must contain only kind and text")
        if len(source["text"]) > 2_000_000:
            raise DeslopError("Pasted text exceeds 2,000,000 characters")
    terms = data.get("preserveTerms", [])
    if not isinstance(terms, list) or len(terms) > 200 or any(not isinstance(x, str) or not x or len(x) > 200 for x in terms):
        raise DeslopError("preserveTerms must be an array of 1-200 character strings")
    data["preserveTerms"] = terms
    return data


def validate_semantic(data: Any, request_id: str, blocks_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "requestId", "assessments"}:
        raise DeslopError("Semantic file must contain only schemaVersion, requestId, assessments")
    if data["schemaVersion"] != "deslop-semantic/v1" or data["requestId"] != request_id:
        raise DeslopError("Semantic schemaVersion/requestId mismatch")
    assessments = data["assessments"]
    if not isinstance(assessments, list):
        raise DeslopError("assessments must be an array")
    seen: set[str] = set()
    required = {"blockId", "sourceHash", "verdict", "meaning", "valueAdded", "relevance", "reason", "improvement", "replacement"}
    for item in assessments:
        if not isinstance(item, dict) or set(item) != required:
            raise DeslopError("Every semantic assessment must use the exact v1 fields")
        block_id = item["blockId"]
        if block_id in seen:
            raise DeslopError(f"Duplicate semantic assessment: {block_id}")
        seen.add(block_id)
        if block_id not in blocks_by_id:
            raise DeslopError(f"Unknown semantic blockId: {block_id}")
        if item["sourceHash"] != blocks_by_id[block_id]["sourceHash"]:
            raise DeslopError(f"Stale semantic assessment: {block_id}")
        if item["verdict"] not in VERDICTS:
            raise DeslopError(f"Invalid semantic verdict: {item['verdict']}")
        for key in required - {"blockId", "sourceHash", "verdict"}:
            if not isinstance(item[key], str):
                raise DeslopError(f"Semantic {key} must be a string")
        if item["verdict"] == "needs-improvement" and (not item["reason"].strip() or not item["improvement"].strip()):
            raise DeslopError(f"needs-improvement requires reason and improvement: {block_id}")
        if len(item["replacement"]) > 4000:
            raise DeslopError(f"Semantic replacement exceeds 4,000 characters: {block_id}")
        if item["replacement"].strip() and item["verdict"] != "needs-improvement":
            raise DeslopError(f"Only needs-improvement assessments may propose a replacement: {block_id}")
    return data


def load_catalog() -> dict[str, Any]:
    data = strict_json_load(CATALOG_PATH, max_bytes=2_000_000)
    if data.get("schemaVersion") != "deslop-catalog/v1":
        raise DeslopError("Unsupported rule catalog")
    return data


def load_academic_markers() -> set[str]:
    with ACADEMIC_WORDS_PATH.open(encoding="utf-8", newline="") as handle:
        return {row["word"].casefold() for row in csv.DictReader(handle)}


def protected_tokens(text: str, preserve_terms: Iterable[str] = ()) -> dict[str, list[str]]:
    patterns = {
        "numbers": r"(?<!\w)[+-]?(?:\d{1,3}(?:[,. ]\d{3})+|\d+)(?:[.,]\d+)?%?(?!\w)",
        "urls": r"https?://[^\s<>\])}]+",
        "dois": r"(?i)\b10\.\d{4,9}/[-._;()/:A-Z0-9]+",
        "citations": r"(?:\[[0-9,;\-– ]+\]|\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?,?\s+\d{4}[a-z]?\))",
        "quotes": r'''(?:“[^”]{1,1000}”|"[^"\n]{1,1000}"|'[^'\n]{2,500}')''',
    }
    result = {name: re.findall(pattern, text) for name, pattern in patterns.items()}
    result["declared"] = [term for term in preserve_terms if term in text]
    return {key: sorted(values) for key, values in result.items() if values}


def protected_equal(before: str, after: str, preserve_terms: Iterable[str]) -> tuple[bool, dict[str, Any]]:
    old = protected_tokens(before, preserve_terms)
    new = protected_tokens(after, preserve_terms)
    return old == new, {"before": old, "after": new}


@dataclass
class Finding:
    findingId: str
    blockId: str
    locator: str
    sourceText: str
    sourceHash: str
    ruleId: str
    family: str
    evidence: str
    severity: str
    confidence: float
    explanation: str
    action: str
    suggestion: str = ""
    matchedText: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ABSTRACT_WORDS = {
    "innovation", "transformation", "excellence", "value", "impact", "growth",
    "alignment", "collaboration", "efficiency", "potential", "insights",
    "strategy", "leadership", "future", "journey", "landscape", "ecosystem",
    "empowerment", "resilience", "sustainability", "opportunity", "vision",
}
CONTENT_VERBS = {
    "built", "reduced", "increased", "launched", "measured", "found", "shows",
    "changed", "approved", "rejected", "owns", "will", "must", "costs", "saved",
    "sold", "opened", "closed", "reported", "compared", "requires", "caused",
}
FURNITURE_PATTERNS = [
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"^\s*(?:confidential|draft|source|sources)\s*:?.*$", re.I),
    re.compile(r"^\s*(?:©|copyright)\b", re.I),
]


def classify_role(block: dict[str, Any]) -> str:
    role = block.get("role")
    if role:
        return role
    text = block["text"].strip()
    if any(pattern.match(text) for pattern in FURNITURE_PATTERNS):
        return "furniture"
    if block.get("level") == 0 and len(text) <= 100:
        return "headline"
    if block.get("isBullet"):
        return "bullet"
    if block.get("container") == "table":
        return "table-cell"
    return "paragraph"


def value_assessment(block: dict[str, Any], nearby_texts: list[str], genre: str) -> dict[str, Any]:
    text = block["text"].strip()
    role = classify_role(block)
    base = {
        "blockId": block["blockId"], "sourceHash": block["sourceHash"],
        "role": role, "meaning": "", "valueAdded": "", "relevance": "",
        "reason": "", "improvement": "",
    }
    if not text:
        return {**base, "verdict": "exempt", "reason": "Empty structural paragraph; no visible content."}
    if role == "furniture":
        return {**base, "verdict": "exempt", "meaning": "Document furniture.", "valueAdded": "Navigation, source, legal, or status metadata.", "relevance": "Exempt from substantive-content scoring.", "reason": "Classified as furniture."}

    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text.casefold())
    unique = set(words)
    abstract_count = sum(word in ABSTRACT_WORDS for word in words)
    content_verb_count = sum(word in CONTENT_VERBS or word.endswith(("ed", "ing")) for word in words)
    has_number = bool(re.search(r"\d", text))
    has_named = bool(re.search(r"\b[A-Z][a-z]{2,}\b", text))
    buzz_ratio = abstract_count / max(1, len(words))
    normalized = re.sub(r"\W+", " ", text.casefold()).strip()
    duplicate = any(normalized == re.sub(r"\W+", " ", other.casefold()).strip() for other in nearby_texts if other.strip())
    generic_heading = role == "headline" and len(words) <= 8 and buzz_ratio >= 0.25 and not has_number and content_verb_count == 0
    universal_fit = buzz_ratio >= 0.20 and not has_number and not has_named and content_verb_count <= 1
    too_short_to_judge = len(words) <= 2 and role not in {"headline", "table-cell"}

    if duplicate:
        return {**base, "verdict": "needs-improvement", "meaning": "Repeats a nearby block.", "valueAdded": "No unique information detected.", "relevance": "Redundant in this location.", "reason": "This block duplicates nearby text.", "improvement": "Delete it or replace it with a distinct fact, reason, action, qualification, or decision."}
    if generic_heading:
        return {**base, "verdict": "needs-improvement", "meaning": "Names an abstract theme rather than a claim.", "valueAdded": "No supported takeaway or decision.", "relevance": "A headline should orient the reader or state the point.", "reason": "Buzzword-heavy headline without a concrete subject, action, result, or tension.", "improvement": "State the supported takeaway: who did what, what changed, compared with what, or what the audience must decide."}
    if universal_fit:
        return {**base, "verdict": "needs-improvement", "meaning": "Expresses broad positive abstractions.", "valueAdded": "Little subject-specific information.", "relevance": "Could fit unrelated subjects with minimal change.", "reason": "High abstract/buzzword density without concrete evidence or mechanism.", "improvement": "Replace abstractions with the actor, action, mechanism, measure, evidence, or decision. Delete the block if none is available."}
    if too_short_to_judge:
        return {**base, "verdict": "abstain", "meaning": "Insufficient standalone context.", "valueAdded": "Cannot determine reliably.", "relevance": "May be a label.", "reason": "Too little text for a safe value judgment.", "improvement": "Review with its visual/container context."}

    value_markers = int(has_number) + int(has_named) + int(content_verb_count > 0) + int(len(unique) >= 6)
    if value_markers >= 2 or role in {"table-cell", "headline"}:
        return {**base, "verdict": "meaningful", "meaning": "Contains a concrete subject, action, label, or claim.", "valueAdded": "Adds identifiable information at this location.", "relevance": f"Fits the {role} role; verify against the communication job.", "reason": "Passes the local specificity and information-gain screen."}
    return {**base, "verdict": "needs-improvement", "meaning": "A grammatical statement with limited concrete content.", "valueAdded": "Weak or unclear information gain.", "relevance": "The connection to the communication job is not explicit.", "reason": "Lacks enough subject-specific evidence, mechanism, action, or qualification.", "improvement": "Make the claim testable and specific, or remove it if it does not change the reader's understanding or decision."}


class Analyzer:
    def __init__(self) -> None:
        self.catalog = load_catalog()
        self.academic_words = load_academic_markers()
        self.catalog_hash = sha256_file(CATALOG_PATH)
        self.lex_groups = self.catalog["lexicon"]["groups"]
        self.structure_rules = self.catalog["structures"]["rules"]
        self.artifact_rules = self.catalog["artifacts"]["rules"]

    def _finding(self, block: dict[str, Any], rule_id: str, family: str, evidence: str, severity: str, explanation: str, action: str, matched: str = "", suggestion: str = "", confidence: float = 0.85) -> Finding:
        return Finding(
            findingId=stable_id("finding", block["blockId"], rule_id, matched.casefold()),
            blockId=block["blockId"], locator=block["locator"], sourceText=block["text"],
            sourceHash=block["sourceHash"], ruleId=rule_id, family=family,
            evidence=evidence, severity=severity, confidence=confidence,
            explanation=explanation, action=action, suggestion=suggestion, matchedText=matched,
        )

    def analyze_block(self, block: dict[str, Any], genre: str) -> list[dict[str, Any]]:
        text = block["text"]
        folded = text.casefold()
        findings: list[Finding] = []
        for group in self.lex_groups:
            matches = []
            for term in group.get("terms", []):
                if str(term).casefold() in folded:
                    matches.append(str(term))
            if matches:
                weight = float(group.get("default_weight", 0.5))
                severity = "medium" if weight >= 1.5 else "low"
                if group.get("evidence") == "A":
                    severity = "high"
                findings.append(self._finding(
                    block, group["id"], group.get("label", "lexical pattern"), group.get("evidence", "C"),
                    severity, f"Matched {len(matches)} term(s) from {group.get('label', group['id'])}. Presence alone is not authorship evidence.",
                    str(group.get("action", "Review density, fit, specificity, and information value.")),
                    ", ".join(matches[:8]), confidence=min(0.98, 0.65 + 0.05 * len(matches)),
                ))

        for rule in self.structure_rules:
            matched = []
            for pattern in rule.get("patterns", []):
                try:
                    hit = re.search(pattern, text, re.I if rule.get("pattern_type", "").endswith("_ci") else 0)
                except re.error:
                    continue
                if hit:
                    matched.append(hit.group(0))
            if rule.get("id") == "STR_RANGE_FROM_TO":
                matched = [value for value in matched if "everything from" in value.casefold() or not re.search(r"\d", value)]
            if matched:
                findings.append(self._finding(
                    block, rule["id"], rule.get("label", "structural pattern"), rule.get("evidence", "C"),
                    rule.get("severity", "low"), rule.get("review", f"Review repeated use of {rule.get('label', rule['id'])}."),
                    "Review the structure in context; keep deliberate rhetoric and revise formulaic or low-value use.",
                    matched[0][:200], confidence=0.82,
                ))

        for rule in self.artifact_rules:
            if rule.get("context_required") == "non_markdown_medium" and block.get("format") == "md":
                continue
            matched = []
            for pattern in rule.get("patterns", []):
                try:
                    hit = re.search(pattern, text, re.I if "ci" in rule.get("pattern_type", "") else 0)
                except re.error:
                    continue
                if hit:
                    matched.append(hit.group(0))
            for term in rule.get("terms", []):
                if str(term).casefold() in folded:
                    matched.append(str(term))
            if matched:
                findings.append(self._finding(
                    block, rule["id"], rule.get("label", "artifact"), "A",
                    rule.get("severity", "high"), f"Detected objective artifact candidate: {rule.get('label', rule['id'])}.",
                    "Remove or verify the artifact before publication.", matched[0][:200], confidence=0.98,
                ))

        if genre == "academic":
            academic = sorted({word for word in re.findall(r"[A-Za-z]+", folded) if word in self.academic_words})
            if len(academic) >= 3:
                findings.append(self._finding(
                    block, "LEX_KOBAK_DENSITY", "measured academic marker density", "B", "low",
                    f"Contains {len(academic)} words from a corpus-level excess-vocabulary dataset. This cannot classify an individual passage.",
                    "Review only when these words cluster with vagueness, templating, or weak information value.",
                    ", ".join(academic[:12]), confidence=0.65,
                ))
        return [item.to_dict() for item in findings]

    def analyze(self, blocks: list[dict[str, Any]], genre: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        findings: list[dict[str, Any]] = []
        assessments: list[dict[str, Any]] = []
        by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            by_scope[block.get("scope", "document")].append(block)
        for block in blocks:
            nearby = [item["text"] for item in by_scope[block.get("scope", "document")] if item["blockId"] != block["blockId"]]
            assessment = value_assessment(block, nearby, genre)
            assessments.append(assessment)
            findings.extend(self.analyze_block(block, genre))
            if assessment["verdict"] == "needs-improvement":
                findings.append(self._finding(
                    block, "VALUE_BLOCK", "meaning and information value", "E", "high",
                    assessment["reason"], "Improve or remove this block; do not publish empty words.",
                    suggestion=assessment["improvement"], confidence=0.88,
                ).to_dict())
        return findings, assessments


SAFE_REPLACEMENTS = [
    (re.compile(r"\bin order to\b", re.I), "to"),
    (re.compile(r"\bat this point in time\b", re.I), "now"),
    (re.compile(r"\bdue to the fact that\b", re.I), "because"),
    (re.compile(r"\bit is important to note that\s+", re.I), ""),
    (re.compile(r"\bit is worth noting that\s+", re.I), ""),
    (re.compile(r"\bin conclusion,?\s+", re.I), ""),
    (re.compile(r"\butilize(?:s|d|ing)?\b", re.I), lambda m: {"utilizes": "uses", "utilized": "used", "utilizing": "using"}.get(m.group(0).casefold(), "use")),
    (re.compile(r"\bserves as\b", re.I), "is"),
    (re.compile(r"\bplays a crucial role in\b", re.I), "affects"),
]


def conservative_rewrite(text: str, genre: str, preserve_terms: list[str]) -> tuple[str, list[str]]:
    revised = text
    reasons: list[str] = []
    for pattern, replacement in SAFE_REPLACEMENTS:
        candidate, count = pattern.subn(replacement, revised)
        if count and candidate != revised:
            ok, _ = protected_equal(revised, candidate, preserve_terms)
            if ok:
                revised = candidate
                reasons.append(pattern.pattern)
    revised = re.sub(r"[ \t]{2,}", " ", revised)
    if revised:
        revised = revised[0].upper() + revised[1:] if text[:1].isupper() else revised
    if len(text) and abs(len(revised) - len(text)) / len(text) > 0.60:
        return text, []
    return revised, reasons


def editorial_vector(findings: list[dict[str, Any]], assessments: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for item in findings:
        family = (item.get("family") or "").casefold()
        rule = item.get("ruleId", "")
        if item.get("evidence") == "A":
            counts["artifacts"] += 2
        if any(word in family for word in ("authority", "citation", "support", "evidence")):
            counts["support"] += 1
        if any(word in family for word in ("structure", "parallel", "triplet", "dash", "transition")) or rule.startswith(("STR_", "DOC_")):
            counts["structure"] += 1
        if any(word in family for word in ("significance", "consultant", "promotional", "abstract", "inflation")):
            counts["inflation"] += 1
        if "repeat" in family or "duplicate" in item.get("explanation", "").casefold():
            counts["repetition"] += 1
        if "assistant" in family or "context" in family:
            counts["context-fit"] += 1
    needs = sum(x["verdict"] == "needs-improvement" for x in assessments)
    counts["specificity"] += needs
    return {key: min(3, int(math.ceil(counts[key] / 2))) for key in ["artifacts", "specificity", "support", "structure", "inflation", "repetition", "context-fit"]}


def profile_statistics(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": 0.0, "mad": 0.0, "q1": 0.0, "q3": 0.0}
    ordered = sorted(values)
    median = statistics.median(ordered)
    deviations = [abs(x - median) for x in ordered]
    q = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 else [ordered[0], ordered[0], ordered[0]]
    return {"median": median, "mad": statistics.median(deviations), "q1": q[0], "q3": q[2]}
