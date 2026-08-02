from __future__ import annotations

import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from deslop_core import Analyzer, DeslopError, profile_statistics, stable_id
from formats import extract_source


SUPPORTED = {".pptx", ".docx", ".pdf", ".md", ".txt"}


def _files(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise DeslopError(f"Profile input does not exist: {path}")
        if path.is_file():
            if path.suffix.lower() in SUPPORTED:
                found.append(path)
        else:
            found.extend(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("~$") and item.suffix.lower() in SUPPORTED)
    unique = {str(path).casefold(): path for path in found}
    return [unique[key] for key in sorted(unique)]


def _document_metrics(blocks: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [block["text"] for block in blocks]
    sentences = [part.strip() for text in texts for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    words = [re.findall(r"[A-Za-z][A-Za-z'-]*", text) for text in texts]
    transition_openings = 0
    for text in texts:
        first = re.match(r"^\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2})[,;:]", text)
        if first:
            transition_openings += 1
    headings = [block for block in blocks if block.get("role") == "headline"]
    bullets = [block for block in blocks if block.get("role") == "bullet"]
    return {
        "sentenceLengths": [len(re.findall(r"\b\w+\b", sentence)) for sentence in sentences],
        "blockLengths": [len(tokens) for tokens in words],
        "punctuation": {char: sum(text.count(char) for text in texts) for char in ["—", ":", ";", "(", ")"]},
        "transitionOpeningCount": transition_openings,
        "headlineCount": len(headings),
        "bulletCount": len(bullets),
        "wordCount": sum(map(len, words)),
        "findingCount": len(findings),
        "highRiskCount": sum(item.get("severity") in {"high", "critical"} for item in findings),
    }


def build_profile(request: dict[str, Any]) -> dict[str, Any]:
    allowed = {"schemaVersion", "id", "genre", "inputs", "approvedExcerptIds"}
    if not isinstance(request, dict) or set(request) - allowed:
        raise DeslopError(f"Invalid profile request fields: {sorted(set(request) - allowed) if isinstance(request, dict) else 'not an object'}")
    if request.get("schemaVersion") != "deslop-profile-request/v1":
        raise DeslopError("profile schemaVersion must be deslop-profile-request/v1")
    if request.get("genre") not in {"consulting", "academic"}:
        raise DeslopError("profile genre must be consulting or academic")
    if not isinstance(request.get("inputs"), list) or not request["inputs"]:
        raise DeslopError("profile inputs must be a non-empty list of exact files or folders")
    files = _files(request["inputs"])
    if not files:
        raise DeslopError("No supported files found in the supplied profile inputs")

    analyzer = Analyzer()
    documents: list[dict[str, Any]] = []
    seen_blocks: set[str] = set()
    failed_documents = 0
    for path in files:
        try:
            source = extract_source({"input": {"kind": "file", "path": str(path)}})
        except Exception:
            failed_documents += 1
            continue
        deduped = []
        for block in source["blocks"]:
            normalized = re.sub(r"\s+", " ", block["text"].strip()).casefold()
            signature = stable_id("boilerplate", normalized)
            if signature not in seen_blocks:
                seen_blocks.add(signature)
                deduped.append(block)
        findings, _ = analyzer.analyze(deduped, request["genre"])
        metrics = _document_metrics(deduped, findings)
        documents.append({
            "format": path.suffix.lower().lstrip("."),
            "blockCount": len(deduped),
            "metrics": metrics,
        })

    if not documents:
        raise DeslopError("None of the supplied supported documents could be parsed")
    slop_rates = [doc["metrics"]["highRiskCount"] / max(1, doc["blockCount"]) for doc in documents]
    rate_stats = profile_statistics(slop_rates)
    for doc, rate in zip(documents, slop_rates):
        scale = max(rate_stats["mad"] * 3, 0.05)
        doc["weight"] = round(max(0.1, min(1.0, 1.0 - max(0.0, rate - rate_stats["median"]) / scale)), 4)

    def combined(field: str) -> dict[str, float]:
        values = [float(value) for doc in documents for value in doc["metrics"][field]]
        return profile_statistics(values)

    punctuation = {char: profile_statistics([doc["metrics"]["punctuation"][char] / max(1, doc["metrics"]["wordCount"]) * 1000 for doc in documents]) for char in ["—", ":", ";", "(", ")"]}
    transition_rates = [doc["metrics"]["transitionOpeningCount"] / max(1, doc["blockCount"]) for doc in documents]
    return {
        "schemaVersion": "deslop-profile/v1",
        "id": request["id"],
        "genre": request["genre"],
        "privacy": {"rawTextStored": False, "excerptTextStored": False, "approvedExcerptIds": request.get("approvedExcerptIds", [])},
        "corpus": {
            "documentCount": len(documents),
            "failedDocumentCount": failed_documents,
            "uniqueBlockCount": len(seen_blocks),
            "formatCounts": dict(Counter(doc["format"] for doc in documents)),
            "downweightedDocumentCount": sum(doc["weight"] < 1.0 for doc in documents),
            "perDocumentDataStored": False,
        },
        "preferences": {
            "sentenceLength": combined("sentenceLengths"),
            "blockLength": combined("blockLengths"),
            "punctuationPerThousandWords": punctuation,
            "transitionOpeningRate": profile_statistics(transition_rates),
        },
        "robustness": {"method": "median/MAD/IQR with duplicate boilerplate counted once", "highRiskRate": rate_stats},
    }
