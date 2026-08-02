# DeSlopAI

DeSlopAI is a local Codex skill and plugin for finding and conservatively rewriting vague, formulaic, unsupported, repetitive, buzzword-heavy, or low-value English text. It audits every eligible text block for meaning and information value and produces source-bound verification evidence. It never reports an "AI probability."

## What makes it different

- Every extractable visible headline, bullet, paragraph, caption, chart label, callout, and table cell receives a meaning/value assessment.
- `eligibleBlockCount` must equal `assessedBlockCount`; furniture exemptions and unsafe spans remain explicit.
- Numbers, quotations, citations, URLs, DOIs, and declared terminology are protected.
- Edits are locator- and source-hash-bound, originals remain byte-identical, and outputs use `-deslopped` suffixes.
- PowerPoint and Word copies are opened in Microsoft Office when available. Geometry, package relationships, non-text parts, and structural invariants are recomputed.
- Private consulting and academic profiles store anonymous corpus-level metrics only: no text, source paths, filenames, email addresses, per-document hashes, or per-document measurements.

Supported in v0.1.0: PPTX, DOCX, PDF audit, Markdown, plain text, and pasted text. PDF is audit-only. SmartArt, mixed formatting, fields, equations, citation fields, tracked changes, hyperlinks, notes, and auxiliary Office text are audited or abstained where a format-faithful edit cannot be proven.

## Quick start

Install the runtime packages with your preferred Python environment:

```text
python -m pip install -r requirements.txt
python skills/deslop-ai/scripts/deslop.py preflight
```

Create a strict request from `skills/deslop-ai/assets/schemas/request.schema.json`, then:

```text
python skills/deslop-ai/scripts/deslop.py ingest request.json --out semantic-packet.json
python skills/deslop-ai/scripts/deslop.py request request.json --semantic-findings semantic.json --out run
python skills/deslop-ai/scripts/deslop.py request-verify run --out independent-verification.json
```

The skill instructs the host agent to assess all blocks from `semantic-packet.json`. Direct CLI use without `--semantic-findings` remains deterministic and complete, but uses the deliberately conservative local meaning/value heuristic.

Every run contains the strict request, source map, deterministic and semantic findings, combined findings, rewrite plan, report, verification, manifest, and—only after mandatory gates pass—the revised artifact.

## Private profiles

Profile requests accept only explicit files or folders and write only beneath `~/.deslop-ai`:

```text
python skills/deslop-ai/scripts/deslop.py profile profile-request.json --out ~/.deslop-ai/profiles/consulting-v1
```

Consulting and academic profiles are separate. Duplicate template text is counted once; document influence is down-weighted with robust median/MAD/IQR statistics when its style or anti-slop risk diverges from the corpus.

## Development and validation

```text
python tests/build_fixtures.py
python -m unittest discover -s tests -v
python scripts/privacy_audit.py
```

The plugin manifest is `.codex-plugin/plugin.json`; the independently installable skill is `skills/deslop-ai`. See `THIRD_PARTY_NOTICES.md` for research-data attribution. Original code and rules are MIT licensed.
