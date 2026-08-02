# Private Personalization

## Contents

1. Privacy boundary
2. Profile construction
3. Robust aggregation
4. Profile use

## 1. Privacy boundary

Accept only explicitly supplied files or folders. Never crawl drives. Store profiles and run artifacts under the private DeSlopAI root, outside the plugin and Git repository. Do not store raw source text or unapproved excerpts in a profile.

## 2. Profile construction

Maintain separate consulting and academic profiles. Extract document hashes and aggregate features: sentence/paragraph distributions, punctuation, headings, bullets, transitions, hedging, vocabulary, citations, text density, and deterministic editorial-risk dimensions.

Generate excerpt candidate IDs from hashes. Include excerpt text only after explicit approval.

## 3. Robust aggregation

Deduplicate normalized repeated boilerplate. Compute per-document features, medians, median absolute deviations, and IQRs. Down-weight outliers and unusually high-slop documents. Record weights and reasons so profiling is inspectable.

## 4. Profile use

Use the profile to calibrate rhythm, density, terminology, and genre conventions. Never use it to weaken protected-token, evidence, formatting, or value-coverage requirements. If a personal habit is vague or unsupported, flag it.

