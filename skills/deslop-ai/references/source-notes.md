# Research Sources and Data Provenance

The runtime catalog derives from the local DeSlopAI research base assembled on 2026-08-02.

Primary orientation:

- Kobak et al., “Delving into LLM-assisted writing in biomedical publications through excess vocabulary,” with the MIT-licensed `berenslab/llm-excess-vocab` dataset.
- Juzek and Ward, “Why Does ChatGPT ‘Delve’ So Much?”
- Wikipedia WikiProject AI Cleanup, “Signs of AI writing,” used as a descriptive field guide with its explicit false-positive caveats.
- Russell, Karpinska, and Iyyer, “People who frequently use ChatGPT for writing tasks are accurate and robust detectors of AI-generated text.”
- RAID, detector robustness studies, and Stanford’s detector-bias work.
- Vale, textlint, proselint, and public plain-language guidance as non-authorship editorial foundations.
- LinkedIn, “Keeping conversations real on LinkedIn,” for the platform-level distinction between useful perspective and polished but generic or repetitive content.
- Liebscher et al., “Workslop: Examining the prevalence, antecedents and consequences of low-quality AI-generated content at work,” for recipient rework and task-advancement failure.
- GOV.UK accessible-document and heading guidance for clear, active, descriptive headings that remain useful when scanned independently.
- W3C WAI heading guidance for headings that accurately describe a section's topic or purpose and reflect its content structure.
- CDC/ATSDR clear-writing guidance on hidden verbs and noun strings, used as an editorial basis for detecting actionless abstract stacks while preserving concrete technical labels.
- The CDC Style Guide's pronoun-and-antecedent guidance, used to flag orphaned references while preserving pronouns whose referent is explicit in the same container.
- U.S. National Archives and CDC active-voice guidance, used narrowly to identify decisions and assignments whose passive construction hides a necessary actor; scientific and technical passive voice remains allowed when the actor is irrelevant or explicit.
- U.S. National Archives guidance to omit needless modifiers and use concrete words, balanced with the UK Government uncertainty toolkit's requirement to communicate analytical uncertainty clearly rather than delete it.
- U.S. National Archives concrete-word and logical-list guidance plus GOV.UK's `be specific rather than general` content principle, used to distinguish empty plural buckets from informative approximate or technical quantifiers.
- UK Government Analysis Function guidance to put changes into context and describe their direction, size, period, and relevant comparator, used to identify unanchored generic comparisons while preserving concrete qualitative outcomes.
- HM Treasury Magenta Book guidance on causal pathways, counterfactuals, attribution, and alternative explanations, used to distinguish unsupported abstract causal slogans from evidence-backed or mechanism-specific claims.
- Wikipedia WikiProject AI Cleanup's descriptive observation of overused negative parallelism, combined with critical-writing guidance on false dilemmas; the construction is treated as a review candidate, never authorship evidence, and substantive contrasts remain protected.
- Razniewski et al., “A Straightforward Pipeline for Targeted Entailment and Contradiction Detection,” as orientation for treating broader contradiction detection as a semantic inference task; the local deterministic fallback is intentionally limited to explicit, high-confidence conflicts.

The bundled 434-word CSV is the style-related subset of the upstream 900-row excess-vocabulary dataset. Retain upstream attribution. Corpus-level markers cannot classify individual documents.
