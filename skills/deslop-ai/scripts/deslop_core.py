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
    "cut", "removed", "delayed", "estimate", "estimates", "tested", "failed",
    "approve", "assess", "choose", "decide", "delay", "deliver", "file", "hire",
    "improve", "move", "produce", "publish", "reduce", "reject", "review", "select",
    "send", "start", "stop", "store", "submit",
    "cover", "covers", "lack", "lacks", "rose", "use", "uses", "withdrew",
}
LOCAL_SLOP_PHRASES = {
    "a quiet shift", "today's fast-paced", "fast-paced landscape", "evolving landscape",
    "navigate change", "navigate this", "holistic", "human-centric", "unlock potential",
    "unlocking potential", "future belongs to", "let that sink in", "keep showing up",
    "thrilled to announce", "groundbreaking solution", "seamlessly revolutionizes",
    "strategic transformation", "integrated capabilities", "sustainable value",
    "cross-functional synergies", "drive alignment", "operational excellence",
    "pivotal study", "delves into", "delve into", "intricate interplay", "multifaceted nature",
    "underscoring", "future-ready", "scalable impact", "across the enterprise",
    "biggest lessons", "smallest moments", "growth isn't linear", "growth is not linear",
}
ABSTRACT_QUALITIES = {
    "clarity", "courage", "consistency", "trust", "mindset", "authenticity",
    "purpose", "passion", "potential", "growth", "resilience", "excellence",
}
VAGUE_NOUN_STACK_WORDS = {
    "acceleration", "agility", "alignment", "capability", "collaboration",
    "creation", "delivery", "ecosystem", "enablement", "end-to-end", "enhancement",
    "enterprise", "excellence", "facilitation", "future-ready", "holistic",
    "impact", "implementation", "innovation", "integrated", "optimization",
    "operational", "orchestration", "organizational", "realization", "scalable", "solution",
    "stakeholder", "strategic", "transformation", "value",
}
BARE_REFERENCE_RE = re.compile(
    r"^\s*(?:this|that|it|they|these|those)\s+"
    r"(?:is|are|was|were|has|have|had|will|would|can|could|may|might|must|should|"
    r"changes?|defines?|unlocks?|requires?|needs?|shows?|proves?|means?|matters?|works?|"
    r"fails?|succeeds?|allows?|prevents?|creates?|drives?|supports?|enables?|approved?|rejected?|"
    r"cuts?|reduces?|increases?|delays?|accelerates?|improves?|lowers?|raises?)\b",
    re.I,
)
VAGUE_DEMONSTRATIVE_RE = re.compile(
    r"^\s*(?:these|those)\s+(?:figures|numbers|results|findings|insights|factors|issues|"
    r"priorities|elements|themes|considerations|points|things|areas|opportunities|"
    r"challenges|capabilities|assets)\b",
    re.I,
)
AGENTLESS_DECISION_RE = re.compile(
    r"\b(?:(?:a|the)\s+)?(?:decision|determination|conclusion|agreement)\s+"
    r"(?:was|were|has been|had been|will be)\s+(?:made|reached)\b|"
    r"^\s*it\s+(?:was|has been|had been)\s+(?:decided|determined|agreed|concluded)\b",
    re.I,
)
OWNERLESS_TASK_RE = re.compile(
    r"\b(?:the\s+)?(?:issue|matter|request|proposal|plan|approach|process|next steps?|"
    r"details?|requirements?|feedback|alignment|input)\s+"
    r"(?:(?:will|should|must|needs?\s+to)\s+be|(?:is|are)\s+(?:being\s+)?)\s*"
    r"(?:addressed|handled|reviewed|considered|defined|developed|finalized|implemented|"
    r"optimized|aligned|validated|confirmed|incorporated|established)\b",
    re.I,
)
OWNERLESS_REQUIREMENT_RE = re.compile(
    r"^\s*(?:approval|validation|alignment|support|input|feedback|clarification)\s+"
    r"(?:is|are)\s+(?:needed|required)\b",
    re.I,
)
PSEUDO_ACTION_RE = re.compile(
    r"\b(?:should\s+consider\s+(?:exploring|assessing|reviewing|looking)|"
    r"may\s+be\s+worth\s+(?:considering|exploring|examining|looking)|"
    r"could\s+potentially\s+(?:assess|explore|consider)|"
    r"consideration\s+should\s+be\s+given\s+to|may\s+want\s+to\s+explore|"
    r"further\s+work\s+could\s+help\s+inform\s+future|"
    r"there\s+may\s+be\s+an\s+opportunity\s+to\s+potentially|"
    r"might\s+be\s+useful\s+to\s+consider)\b",
    re.I,
)
HEDGE_ACTION_MARKER_RE = re.compile(
    r"\b(?:may|might|could|should|perhaps|potentially|possibly|possible|consideration|"
    r"consider|considering|explore|exploring|opportunity|opportunities|worth|useful|"
    r"further|future|inform|engage|engaging|enhance|strategic)\b|\b(?:want\s+to|looking\s+into)\b",
    re.I,
)
EXPLICIT_CONDITION_RE = re.compile(r"\b(?:if|unless|when|whenever|provided that|subject to|depending on)\b", re.I)
UNSPECIFIED_BUCKET_RE = re.compile(
    r"\b(?:several|numerous|various|multiple|many|a\s+range\s+of|a\s+variety\s+of)\s+"
    r"(?:(?:key|strategic|potential|important|broad)\s+)?(?:factors?|opportunities|challenges|considerations|dimensions|areas|"
    r"initiatives|stakeholders|perspectives|benefits|levers|themes|elements|aspects|"
    r"priorities|issues)\b",
    re.I,
)
SPECIFIC_BUCKET_ACTION_RE = re.compile(
    r"\b(?:approved|rejected|signed|submitted|missed|failed|completed|selected|voted|"
    r"filed|withdrew|produced|use|uses|lack|lacks|covers|stored|measured|reported|"
    r"named|listed|include|includes)\b",
    re.I,
)
GENERIC_COMPARISON_CLAIM_RE = re.compile(
    r"\b(?:performance|results?|outcomes?|customer\s+experience|experience|costs?|adoption|"
    r"engagement|revenue|sales|demand|efficiency|quality|productivity|market|signals?|model|"
    r"approach|solution)\b.{0,60}\b(?:better|worse|faster|slower|higher|lower|stronger|"
    r"weaker|greater(?:\s+value)?|more\s+(?:efficient|effective|scalable|attractive|productive|valuable)|"
    r"less\s+(?:efficient|effective|scalable|attractive|productive|valuable)|improved|increased|"
    r"decreased|grew|fell|rose|risen|rise|rising|growing|declining)\b",
    re.I,
)
COMPARISON_ANCHOR_RE = re.compile(
    r"\b(?:than|versus|vs\.?|compared\s+(?:with|to)|relative\s+to|after|before|since|"
    r"year[- ]on[- ]year|month[- ]on[- ]month|quarter[- ]on[- ]quarter|week[- ]on[- ]week|"
    r"over\s+the\s+past|between|baseline|target)\b|\bfrom\b.{0,50}\bto\b",
    re.I,
)
NON_ACTOR_BY_WORDS = {
    "end", "launch", "deadline", "today", "tomorrow", "yesterday", "next",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}
VAGUE_AUTHORITY_RE = re.compile(
    r"\b(?:experts agree|research shows|studies show|the data (?:is|are) clear|it is widely (?:known|recognized))\b",
    re.I,
)
GENERIC_CONTRAST_RE = re.compile(
    r"\b(?:success|leadership|growth|innovation|transformation|change|failure|life)\b.{0,80}"
    r"\b(?:isn't|is not)\s+about\b.{0,120}\b(?:it's|it is)\s+about\b",
    re.I,
)
GENERIC_TRIPLET_RE = re.compile(r"\bevery\s+\w+\s+needs(?:\s+three\s+things)?\s*:", re.I)
GENERIC_HEADINGS = {
    "overview", "introduction", "background", "more information", "next steps",
    "key takeaways", "our approach", "the opportunity", "summary", "conclusion",
    "outlook", "discussion",
}
SELF_ANNOUNCING_RE = re.compile(
    r"\b(?:this|the following)\s+(?:document|report|analysis|section|presentation)\s+"
    r"(?:provides|presents|offers|explores|examines|outlines|identifies)\b",
    re.I,
)
PREMISE_ECHO_RE = re.compile(
    r"\byou asked (?:us|me) to\b.{0,140}\bthe following (?:analysis|section|report)\b",
    re.I,
)
WORKPLACE_PLATITUDE_RE = re.compile(
    r"\b(?:communication is key|alignment is critical|collaboration is essential|move forward with urgency|"
    r"both opportunities and challenges|a balanced approach|ensure success|highlight(?:s)? the importance)\b",
    re.I,
)
NON_OPERATIONAL_ACTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:continue|explore|consider|engage|leverage|support|drive|foster|enhance)\b"
    r".{0,180}\b(?:opportunities|stakeholders|initiatives|alignment|execution|efforts|workstreams|"
    r"as appropriate|where possible)\b",
    re.I,
)
UNSUPPORTED_MAGNITUDE_RE = re.compile(
    r"\b(?:significant|substantial|dramatic|material|meaningful|considerable|remarkable|strong|robust|major)\s+"
    r"(?:impact|improvement|growth|increase|decrease|reduction|results?|performance|success)\b|"
    r"\b(?:improved|increased|decreased|grew|fell)\s+"
    r"(?:significantly|substantially|dramatically|materially|meaningfully|considerably)\b|"
    r"\b(?:highly|very|extremely)\s+(?:successful|effective|impactful|robust)\b|"
    r"\bresults?\s+(?:was|were)\s+(?:highly\s+)?(?:robust|meaningful|strong|significant)\b",
    re.I,
)
CONDITIONAL_CLAIM_RE = re.compile(r"\b(?:could|may|might|risk|if|unless|until|scenario|assuming|subject to)\b", re.I)
POSITIVE_DIRECTION_RE = re.compile(r"\b(?:increase[ds]?|increasing|grew|grown|rose|risen|improve[ds]?|improving|higher)\b", re.I)
NEGATIVE_DIRECTION_RE = re.compile(r"\b(?:decrease[ds]?|decreasing|fell|fallen|decline[ds]?|declining|worsen(?:ed|s|ing)?|lower)\b", re.I)
POSITIVE_STATUS_RE = re.compile(
    r"\b(?:approved|authorized|validated|completed?|certified|ready|on track|proceed|launched|meets?)\b",
    re.I,
)
NEGATIVE_STATUS_RE = re.compile(
    r"\b(?:not approved|unapproved|approval (?:remains )?pending|not authorized|unauthorized|"
    r"not validated|unvalidated|incomplete|uncertified|not ready|off track|do not proceed|"
    r"cancelled|canceled|postponed|blocked|fails?|does not meet|did not meet)\b",
    re.I,
)
FURNITURE_PATTERNS = [
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"^\s*(?:confidential|draft|source|sources)\s*:?.*$", re.I),
    re.compile(r"^\s*(?:©|copyright)\b", re.I),
]
SIGNATURE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "being", "by",
    "for", "from", "had", "has", "have", "in", "is", "it", "of", "on", "or", "that",
    "the", "their", "this", "to", "was", "were", "will", "with",
}


def _signature_tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in re.findall(r"[A-Za-z]+|\d+(?:[.,]\d+)?%?", text.casefold()):
        if token in SIGNATURE_STOPWORDS or len(token) <= 2:
            continue
        stem = token
        for suffix in ("ing", "ied", "ed", "es", "s"):
            if stem.endswith(suffix) and len(stem) - len(suffix) >= 4:
                stem = stem[:-len(suffix)] + ("y" if suffix == "ied" else "")
                break
        result.add(stem)
    return result


def _comparison_anchors(text: str) -> tuple[set[str], set[str]]:
    numbers = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)", text))
    names: set[str] = set()
    for match in re.finditer(r"\b(?:[A-Z]{2,}|[A-Z][a-z]{2,})\b", text):
        token = match.group(0)
        prefix = text[:match.start()].rstrip()
        at_sentence_start = not prefix or prefix[-1:] in ".!?"
        if token not in {"AI", "LLM", "LLMS"} and (token.isupper() or not at_sentence_start):
            names.add(token.casefold())
    return numbers, names


def _near_duplicate(text: str, other: str) -> bool:
    left_numbers, left_names = _comparison_anchors(text)
    right_numbers, right_names = _comparison_anchors(other)
    if (left_numbers or right_numbers) and left_numbers != right_numbers:
        return False
    if (left_names or right_names) and left_names != right_names:
        return False
    left = _signature_tokens(text)
    right = _signature_tokens(other)
    if min(len(left), len(right)) < 4:
        return False
    intersection = len(left & right)
    containment = intersection / min(len(left), len(right))
    union = intersection / len(left | right)
    return containment >= 0.75 and union >= 0.55


def _has_agent_cue(text: str) -> bool:
    for match in re.finditer(r"\b(?:by|from)\s+(?:the\s+)?([A-Za-z][A-Za-z'-]*)", text, re.I):
        if match.group(1).casefold() not in NON_ACTOR_BY_WORDS:
            return True
    return False


def _polarity(text: str, positive: re.Pattern[str], negative: re.Pattern[str]) -> int:
    negative_matches = list(negative.finditer(text))
    scrubbed = text
    for match in reversed(negative_matches):
        scrubbed = scrubbed[:match.start()] + " " * (match.end() - match.start()) + scrubbed[match.end():]
    positive_hit = bool(positive.search(scrubbed))
    negative_hit = bool(negative_matches)
    if positive_hit and negative_hit:
        return 0
    if positive_hit:
        return 1
    if negative_hit:
        return -1
    return 0


def _conflict_subject(text: str) -> set[str]:
    scrubbed = text
    for pattern in (POSITIVE_DIRECTION_RE, NEGATIVE_DIRECTION_RE, POSITIVE_STATUS_RE, NEGATIVE_STATUS_RE):
        scrubbed = pattern.sub(" ", scrubbed)
    return _signature_tokens(scrubbed) - {
        "all", "any", "every", "none", "not", "remain", "still", "currently", "two",
    }


def _same_conflict_subject(left: str, right: str) -> bool:
    left_subject = _conflict_subject(left)
    right_subject = _conflict_subject(right)
    if not left_subject or not right_subject:
        return False
    if left_subject == right_subject:
        return True
    intersection = len(left_subject & right_subject)
    containment = intersection / min(len(left_subject), len(right_subject))
    return intersection >= 2 and containment >= 0.60


def _explicit_container_conflict(left: str, right: str) -> str | None:
    if CONDITIONAL_CLAIM_RE.search(left) or CONDITIONAL_CLAIM_RE.search(right):
        return None
    left_direction = _polarity(left, POSITIVE_DIRECTION_RE, NEGATIVE_DIRECTION_RE)
    right_direction = _polarity(right, POSITIVE_DIRECTION_RE, NEGATIVE_DIRECTION_RE)
    if left_direction and right_direction and left_direction == -right_direction and _same_conflict_subject(left, right):
        return "opposite directional claims"
    left_status = _polarity(left, POSITIVE_STATUS_RE, NEGATIVE_STATUS_RE)
    right_status = _polarity(right, POSITIVE_STATUS_RE, NEGATIVE_STATUS_RE)
    if left_status and right_status and left_status == -right_status and _same_conflict_subject(left, right):
        return "incompatible status claims"
    return None


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
    has_citation = bool(re.search(r"https?://|\b10\.\d{4,9}/|\([A-Z][A-Za-z-]+,?\s+\d{4}\)", text))
    has_named = False
    for match in re.finditer(r"\b(?:[A-Z]{2,}|[A-Z][a-z]{2,})\b", text):
        token = match.group(0)
        prefix = text[:match.start()].rstrip()
        at_sentence_start = not prefix or prefix[-1:] in ".!?"
        if token not in {"AI", "LLM", "LLMS"} and (token.isupper() or not at_sentence_start):
            has_named = True
            break
    buzz_ratio = abstract_count / max(1, len(words))
    folded = text.casefold()
    normalized = re.sub(r"\W+", " ", folded).strip()
    slop_phrase_count = sum(phrase in folded for phrase in LOCAL_SLOP_PHRASES)
    vague_authority = bool(VAGUE_AUTHORITY_RE.search(text)) and not has_number and not has_citation
    generic_contrast = bool(GENERIC_CONTRAST_RE.search(text)) and not has_number and not has_citation
    generic_triplet = bool(GENERIC_TRIPLET_RE.search(text)) and sum(word in ABSTRACT_QUALITIES for word in words) >= 2
    generic_heading_label = role == "headline" and normalized in GENERIC_HEADINGS
    self_announcing = bool(SELF_ANNOUNCING_RE.search(text) or PREMISE_ECHO_RE.search(text)) and not has_number and not has_citation
    workplace_platitude = bool(WORKPLACE_PLATITUDE_RE.search(text)) and not has_number and not has_citation
    non_operational_action = bool(NON_OPERATIONAL_ACTION_RE.search(text)) and not has_number and not has_citation and not has_named
    unsupported_magnitude = bool(UNSUPPORTED_MAGNITUDE_RE.search(text)) and not has_number and not has_citation
    orphan_reference = (
        bool(BARE_REFERENCE_RE.search(text) or VAGUE_DEMONSTRATIVE_RE.search(text))
        and (role == "headline" or not nearby_texts)
    )
    ownerless_action = bool(
        AGENTLESS_DECISION_RE.search(text)
        or OWNERLESS_TASK_RE.search(text)
        or OWNERLESS_REQUIREMENT_RE.search(text)
    ) and not _has_agent_cue(text)
    hedge_action_count = len(HEDGE_ACTION_MARKER_RE.findall(text))
    pseudo_action = (
        bool(PSEUDO_ACTION_RE.search(text)) or hedge_action_count >= 3
    ) and not EXPLICIT_CONDITION_RE.search(text)
    unspecified_bucket = (
        bool(UNSPECIFIED_BUCKET_RE.search(text))
        and not has_number
        and not has_citation
        and ":" not in text
        and ";" not in text
        and not SPECIFIC_BUCKET_ACTION_RE.search(text)
    )
    has_explicit_comparison = bool(COMPARISON_ANCHOR_RE.search(text))
    has_named_group_contrast = has_named and bool(re.search(r"\b(?:but|while|whereas)\b", text, re.I))
    unanchored_comparison = (
        bool(GENERIC_COMPARISON_CLAIM_RE.search(text))
        and not has_explicit_comparison
        and not has_named_group_contrast
        and not has_number
        and not has_citation
    )
    vague_noun_count = sum(word in VAGUE_NOUN_STACK_WORDS for word in words)
    vague_noun_stack = (
        role in {"headline", "bullet", "caption"}
        and 2 <= len(words) <= 8
        and vague_noun_count >= 2
        and vague_noun_count / len(words) >= 0.60
        and not has_number
        and not has_citation
        and (not has_named or vague_noun_count == len(words))
        and ":" not in text
    )
    clustered_slop = (
        slop_phrase_count >= 2
        and not has_number
        and not has_citation
    )
    duplicate = any(
        normalized == re.sub(r"\W+", " ", other.casefold()).strip() or _near_duplicate(text, other)
        for other in nearby_texts if other.strip()
    )
    generic_heading = role == "headline" and len(words) <= 8 and buzz_ratio >= 0.25 and not has_number and content_verb_count == 0
    universal_fit = buzz_ratio >= 0.20 and not has_number and not has_named and content_verb_count <= 1
    too_short_to_judge = len(words) <= 2 and role not in {"headline", "table-cell"}

    if duplicate:
        return {**base, "verdict": "needs-improvement", "meaning": "Repeats a nearby block.", "valueAdded": "No unique information detected.", "relevance": "Redundant in this location.", "reason": "This block duplicates nearby text.", "improvement": "Delete it or replace it with a distinct fact, reason, action, qualification, or decision."}
    if pseudo_action:
        return {**base, "verdict": "needs-improvement", "meaning": "Suggests considering or exploring activity without committing to a defined decision or task.", "valueAdded": "Signals openness but does not identify what will be produced, decided, tested, or triggered.", "relevance": "The recipient must still convert the suggestion into an assignment or decision rule.", "reason": "Stacked hedges create a noncommittal pseudo-action.", "improvement": "State the actual supported action, decision, experiment, or risk condition. Name the owner and output when the source provides them; otherwise request those facts. Preserve genuine uncertainty instead of overstating confidence."}
    if unspecified_bucket:
        return {**base, "verdict": "needs-improvement", "meaning": "Refers to a plural category without identifying its members or substantive relationship.", "valueAdded": "Promises factors, opportunities, challenges, or levers but does not tell the reader what they are.", "relevance": "A bucket label adds value only when its contents, evidence, or concrete consequence are visible.", "reason": "Unspecified plural bucket implies substance without supplying it.", "improvement": "Name the supported members, state the concrete relationship or consequence, or delete the bucket claim. If the source does not identify them, request the missing list rather than inventing it."}
    if unanchored_comparison:
        return {**base, "verdict": "needs-improvement", "meaning": "Claims improvement, decline, or comparative advantage without identifying the reference point.", "valueAdded": "Provides direction but not the baseline, period, comparator, or size needed to interpret it.", "relevance": "A reader cannot judge whether the change is meaningful or decision-relevant without comparison context.", "reason": "Generic directional or comparative claim lacks a baseline, period, or reference group.", "improvement": "Add the supported comparator, time period, baseline, and measured change. If the source contains only direction, state that limitation rather than inventing a number."}
    if ownerless_action:
        return {**base, "verdict": "needs-improvement", "meaning": "States that a decision, review, approval, or task exists without naming the responsible actor.", "valueAdded": "The required activity is visible, but ownership and accountability are not.", "relevance": "The recipient cannot assign, verify, or escalate the action without knowing who decides or acts.", "reason": "Agentless passive decision or task hides responsibility.", "improvement": "Name the supported decision-maker or owner and the concrete action. Add a deadline or completion condition when the source provides one; otherwise request the missing owner instead of inventing it."}
    if orphan_reference:
        return {**base, "verdict": "needs-improvement", "meaning": "Makes a claim about an unnamed prior subject.", "valueAdded": "The consequence may be stated, but the reader cannot identify what causes or owns it.", "relevance": "A standalone block needs a clear antecedent in the same visible container; a headline must identify its subject directly.", "reason": "Orphaned pronoun or demonstrative reference has no clear antecedent.", "improvement": "Replace the pronoun or vague carrier noun with the exact subject already supported by the source. If the subject is absent, request it instead of guessing."}
    if generic_heading_label:
        return {**base, "verdict": "needs-improvement", "meaning": "Labels a section without describing its contents or takeaway.", "valueAdded": "Provides hierarchy but no standalone orientation.", "relevance": "Readers should understand the section when scanning headings alone.", "reason": "Generic heading depends entirely on the body and could label almost any document.", "improvement": "Name the specific subject, decision, action, or result covered by the section."}
    if self_announcing:
        return {**base, "verdict": "needs-improvement", "meaning": "Announces that analysis follows instead of supplying its result.", "valueAdded": "Restates the task or document function.", "relevance": "The recipient needs the conclusion, scope, or decision—not a description of the document.", "reason": "Self-announcing or premise-echo language consumes space without advancing the work.", "improvement": "Replace it with the principal finding, defined scope, or decision the analysis supports."}
    if workplace_platitude:
        return {**base, "verdict": "needs-improvement", "meaning": "States a broadly agreeable workplace principle or balance.", "valueAdded": "No owner, trade-off, evidence, or operational consequence is specified.", "relevance": "The recipient still has to infer what changes in the work.", "reason": "Generic workplace conclusion appears complete but transfers interpretation to the reader.", "improvement": "State the observed problem, the responsible owner, the decision or action, and the completion condition."}
    if non_operational_action:
        return {**base, "verdict": "needs-improvement", "meaning": "Uses an action verb without defining a finishable task.", "valueAdded": "Signals activity but not ownership or completion.", "relevance": "An action item must be assignable and verifiable.", "reason": "Non-operational action lacks a concrete object, owner, deadline, deliverable, or acceptance condition.", "improvement": "Name who will produce what, for whom, by when, and how completion will be checked."}
    if unsupported_magnitude:
        return {**base, "verdict": "needs-improvement", "meaning": "Claims a large, important, or successful result.", "valueAdded": "The direction may be clear, but its asserted magnitude or quality is not checkable.", "relevance": "Evaluative claims need a measure, threshold, comparison, or cited source.", "reason": "Unsupported magnitude or success language substitutes evaluation for evidence.", "improvement": "Report the measured change, baseline, comparison, acceptance criterion, or citation. Remove the modifier if no support exists."}
    if vague_noun_stack:
        return {**base, "verdict": "needs-improvement", "meaning": "Stacks abstract process or outcome nouns without stating their relationship.", "valueAdded": "Names an intended theme but not an actor, action, object, result, or decision.", "relevance": "A compact label still needs enough information for a reader to understand what happens or why it matters.", "reason": "Abstract noun stack hides the action and could label many unrelated initiatives.", "improvement": "Unpack the nouns: name who changes or produces what, for whom, and what verified result, condition, or decision follows. Keep the label only if the surrounding container supplies that meaning."}
    if vague_authority:
        return {**base, "verdict": "needs-improvement", "meaning": "Invokes unnamed evidence or consensus.", "valueAdded": "No checkable support is supplied.", "relevance": "Authority claims must let the reader inspect the source.", "reason": "Uses vague authority language without a citation, named source, or measurable result.", "improvement": "Name and cite the source, report the relevant finding, or remove the authority claim."}
    if generic_contrast or generic_triplet:
        return {**base, "verdict": "needs-improvement", "meaning": "States a broad maxim in a familiar rhetorical template.", "valueAdded": "No subject-specific mechanism, example, or boundary is supplied.", "relevance": "The wording could fit many unrelated posts.", "reason": "Formulaic contrast or abstract triplet carries rhetoric but little testable information.", "improvement": "State the observed situation, the specific lesson, and the evidence or action that follows."}
    if clustered_slop:
        return {**base, "verdict": "needs-improvement", "meaning": "Combines promotional or formulaic abstractions.", "valueAdded": "The passage sounds directional but does not identify a concrete actor, mechanism, measure, or decision.", "relevance": "Polish does not substitute for subject-specific information.", "reason": f"Clusters {slop_phrase_count} generic scaffold or inflation phrases without measurable support.", "improvement": "Replace the scaffolding with the actor, action, object, evidence, trade-off, or next decision. If none exists, delete the passage."}
    if generic_heading:
        return {**base, "verdict": "needs-improvement", "meaning": "Names an abstract theme rather than a claim.", "valueAdded": "No supported takeaway or decision.", "relevance": "A headline should orient the reader or state the point.", "reason": "Buzzword-heavy headline without a concrete subject, action, result, or tension.", "improvement": "State the supported takeaway: who did what, what changed, compared with what, or what the audience must decide."}
    if universal_fit:
        return {**base, "verdict": "needs-improvement", "meaning": "Expresses broad positive abstractions.", "valueAdded": "Little subject-specific information.", "relevance": "Could fit unrelated subjects with minimal change.", "reason": "High abstract/buzzword density without concrete evidence or mechanism.", "improvement": "Replace abstractions with the actor, action, mechanism, measure, evidence, or decision. Delete the block if none is available."}
    if too_short_to_judge:
        return {**base, "verdict": "abstain", "meaning": "Insufficient standalone context.", "valueAdded": "Cannot determine reliably.", "relevance": "May be a label.", "reason": "Too little text for a safe value judgment.", "improvement": "Review with its visual/container context."}

    specific_short_claim = (
        content_verb_count > 0
        and len(unique) >= 3
        and abstract_count == 0
        and not unique.intersection({"thing", "things", "something", "anything", "everything"})
    )
    value_markers = int(has_number) + int(has_named) + int(content_verb_count > 0) + int(len(unique) >= 6) + int(has_explicit_comparison)
    if specific_short_claim:
        return {**base, "verdict": "meaningful", "meaning": "States a concise subject-specific outcome or action.", "valueAdded": "Adds a concrete directional claim without generic filler.", "relevance": f"Fits the {role} role; verify evidentiary support in context.", "reason": "Short claim contains a concrete subject and content verb."}
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
        previous_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            scope = block.get("scope", "document")
            nearby = [item["text"] for item in previous_by_scope[scope]]
            assessment = value_assessment(block, nearby, genre)
            assessments.append(assessment)
            findings.extend(self.analyze_block(block, genre))
            if assessment["verdict"] == "needs-improvement":
                value_rule = (
                    "VALUE_NOUN_STACK" if assessment["reason"].startswith("Abstract noun stack")
                    else "VALUE_ORPHAN_REFERENCE" if assessment["reason"].startswith("Orphaned pronoun")
                    else "VALUE_OWNERLESS_ACTION" if assessment["reason"].startswith("Agentless passive")
                    else "VALUE_PSEUDO_ACTION" if assessment["reason"].startswith("Stacked hedges")
                    else "VALUE_UNSPECIFIED_BUCKET" if assessment["reason"].startswith("Unspecified plural bucket")
                    else "VALUE_UNANCHORED_COMPARISON" if assessment["reason"].startswith("Generic directional")
                    else "VALUE_BLOCK"
                )
                findings.append(self._finding(
                    block, value_rule, "meaning and information value", "E", "high",
                    assessment["reason"], "Improve or remove this block; do not publish empty words.",
                    suggestion=assessment["improvement"], confidence=0.88,
                ).to_dict())
            previous_by_scope[scope].append(block)
        assessment_by_id = {item["blockId"]: item for item in assessments}
        scopes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            scopes[block.get("scope", "document")].append(block)
        for scope_blocks in scopes.values():
            headlines = [block for block in scope_blocks if classify_role(block) == "headline"]
            substantive = [block for block in scope_blocks if classify_role(block) not in {"headline", "furniture"}]
            for headline in headlines:
                conflict = next(
                    ((other, kind) for other in substantive if (kind := _explicit_container_conflict(headline["text"], other["text"]))),
                    None,
                )
                if not conflict:
                    continue
                other, kind = conflict
                assessment = assessment_by_id[headline["blockId"]]
                assessment.update({
                    "verdict": "needs-improvement",
                    "meaning": "The headline and supporting content make incompatible claims.",
                    "valueAdded": "The container cannot support a stable conclusion while both claims remain.",
                    "relevance": "A headline must accurately represent the content it governs.",
                    "reason": f"Explicit container contradiction: {kind}.",
                    "improvement": "Resolve the underlying fact or status, then rewrite the headline and supporting block to state one qualified, time-bound conclusion.",
                })
                findings.append(self._finding(
                    headline, "CONTAINER_EXPLICIT_CONTRADICTION", "headline and body consistency", "E", "high",
                    f"Headline conflicts with {other['locator']}: {kind}.",
                    "Resolve the source conflict before publishing; do not guess which claim is current.",
                    matched=f"{headline['text']} <> {other['text']}", confidence=0.94,
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
        if any(word in family for word in ("structure", "parallel", "triplet", "dash", "transition")) or rule.startswith(("STR_", "DOC_", "CONTAINER_")):
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
