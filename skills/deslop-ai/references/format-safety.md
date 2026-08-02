# Format Safety

## Contents

1. Shared invariants
2. PowerPoint
3. Word
4. PDF
5. Markdown and text

## 1. Shared invariants

Never overwrite the source. Bind edits to locator plus source hash. Preserve numbers, quotations, URLs, DOIs, citation tokens, and declared terms. Reject stale or ambiguous targets.

## 2. PowerPoint

Audit all extractable visible text, tables, notes, and supported grouped shapes. Automatically rewrite only ordinary native text paragraphs/table cells whose run formatting is uniform and whose relationships contain no hyperlink or field.

Preserve slide count, size, master/layout bindings, object geometry, bullets, fonts, notes, charts, images, groups, relationships, and all non-target objects. Do not change font size, shape size, or position. Treat SmartArt, embedded objects, field-backed text, and mixed-run replacements as abstentions.

When Microsoft PowerPoint is present, require open-without-repair and check text-frame overflow. Export previews when practical.

## 3. Word

Audit body paragraphs, tables, headers, footers, notes, and supported auxiliary XML. Rewrite only uniform ordinary runs without fields, hyperlinks, equations, content controls, tracked changes, or citation machinery.

Preserve styles, relationships, sections, tables, headers/footers, fields, bookmarks, numbering, and footnotes. Report pagination changes.

## 4. PDF

Audit page-aware extracted text. Never rewrite the PDF in v1. Report extraction uncertainty and OCR limitations.

## 5. Markdown and text

Bind replacements to character offsets and hashes. Protect frontmatter, fenced code, inline code, URLs, link destinations, quoted spans, and markup delimiters. Preserve line endings and encoding when possible.

