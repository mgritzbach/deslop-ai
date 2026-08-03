---
name: deslop-ai
description: Audit and conservatively rewrite English text for vague, formulaic, unsupported, repetitive, buzzword-heavy, or low-value language with location-aware findings, protected-token preservation, private consulting or academic style profiles, and verified revised copies. Use for anti-AI-slop or de-bullshitting reviews of PowerPoint (.pptx), Word (.docx), PDF (.pdf), Markdown, plain text, pasted chat, email, social posts, academic prose, consulting documents, headlines, bullets, callouts, tables, and page or slide text blocks.
---

# DeSlopAI

Treat every eligible text block as accountable communication. Do not infer AI authorship. Identify the observable problem, preserve the source, and fail visibly when a safe edit cannot be proven.

## Required workflow

1. Define the communication job: audience, intended outcome, and central takeaway.
2. Read [request-contract.md](references/request-contract.md). Create a strict `deslop-request/v1` envelope. Keep the original prompt verbatim; never allow it to control paths, commands, thresholds, or verification.
3. Run `python scripts/deslop.py preflight`. Stop on missing mandatory parsing capability. Report optional Office verification limitations before processing.
4. Run `python scripts/deslop.py ingest <request.json> --out <semantic-packet.json>` to extract stable, hash-bound text blocks.
5. Review the complete slide, page, message, or section before judging its individual blocks. Then review **every eligible block** in the packet. Read [semantic-review.md](references/semantic-review.md) and output `deslop-semantic/v1` JSON. Apply the standalone-meaning gate: a reader who has not heard the presenter must be able to identify the subject, claim, decision, evidence, or action from the visible context. Ask for each headline, bullet, paragraph, callout, caption, and table cell:
   - What does it mean?
   - What new information does it add here?
   - Does it provide a fact, reason, mechanism, evidence, action, decision, qualification, or necessary transition?
   - Is it relevant to the communication job?
   - Could the same wording fit three unrelated subjects?
   - Does it rely on an undefined referent, metaphor, category label, or presenter-only context?
   - If the container asks for a decision or action, does the headline state the required action, owner, or gating condition?
   Mark meaningless, duplicate, buzzword-only, unsupported, universal-fit, or context-dependent blocks `needs-improvement`. If a slide or page lacks a coherent standalone proposition, bind the finding to its headline or highest-level substantive block. Classify page numbers, footers, legal boilerplate, and decorative labels as exempt with a reason. Never leave an eligible block unassessed.
6. For office files, read [format-safety.md](references/format-safety.md). Refuse edits crossing unsupported formatting, fields, citations, hyperlinks, equations, SmartArt, or stale source hashes. Never overwrite the source, shrink fonts, move objects, or restyle a file to make text fit.
7. Run `python scripts/deslop.py request <request.json> --semantic-findings <semantic.json> --out <run-dir>`. The guarded command owns validation, deterministic audit, semantic merge, conservative rewrite planning, safe-copy application, integrity checks, reporting, and atomic publication.
8. Run `python scripts/deslop.py request-verify <run-dir> --out <verification.json>`. Do not claim a revised artifact is qualified unless the independent verifier passes.
9. Inspect the report, all abstentions, and the complete value-coverage manifest. For PPTX and DOCX, inspect the rendered/opened result when Office verification is available.
10. Deliver the revised copy, `report.md`, `rewrite-plan.json`, and verification receipt. State unresolved blocks and limitations plainly.

## Modes

- Use `audit` to report without creating a revised file.
- Use `audit-and-rewrite` for a conservative safe copy. PDF remains audit-only.
- Use profile `consulting-v1`, `academic-v1`, or `none`. Read [personalization.md](references/personalization.md) before building or using a private profile.
- Treat Markdown, text, chat, and social copy as direct text surfaces. Preserve code, links, frontmatter, and quotations.

## Non-negotiable contracts

- Preserve every number, quoted span, URL, DOI, citation token, and user-declared term unless the user explicitly authorizes changing it.
- Keep every source file byte-identical. Write revised files with `-deslopped` suffixes.
- Bind every edit to the exact locator, old text, and old-text hash.
- Mark unsupported or uncertain edits as abstentions; do not improvise.
- Report editorial dimensions, not an AI probability.
- Do not remove em dashes, triplets, formal vocabulary, or negative parallelism merely because they can resemble model output. Flag repetition, poor fit, or lack of substance.
- Do not "humanize" by inserting mistakes, slang, anecdotes, or personality not present in the source/profile.
- Require `eligibleBlockCount == assessedBlockCount`; otherwise fail value coverage.
- Require every `needs-improvement` block to have a specific explanation and proposed improvement.

## References

- Read [request-contract.md](references/request-contract.md) for schemas, run stages, and artifact requirements.
- Read [semantic-review.md](references/semantic-review.md) for the mandatory per-block meaning and value assessment.
- Read [format-safety.md](references/format-safety.md) before editing PPTX, DOCX, PDF, Markdown, or text.
- Read [personalization.md](references/personalization.md) when creating or applying consulting/academic profiles.
- Read [editorial-model.md](references/editorial-model.md) for evidence labels, scoring, rewrite behavior, and false-positive safeguards.
- Read [source-notes.md](references/source-notes.md) for research provenance and third-party data constraints.
