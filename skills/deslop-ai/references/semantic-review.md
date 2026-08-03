# Mandatory Meaning and Value Review

## Contents

1. Coverage rule
2. Block roles
3. Container and standalone-meaning review
4. Value test
5. Verdicts
6. Improvement behavior

## 1. Coverage rule

Assess every eligible visible text block. A document fails value coverage if any eligible block lacks exactly one current assessment bound to its source hash.

Do not assess page numbers, dates used solely as running furniture, confidentiality labels, required legal boilerplate, source lines, or decorative section numerals as substantive content. Mark them `exempt` and state why.

## 2. Block roles

- **Headline/title:** state a topic, conclusion, tension, decision, or useful framing. Prefer a takeaway over a generic theme. On a decision or action page, foreground the decision, action, owner, or gating condition.
- **Bullet/callout:** add a distinct fact, reason, mechanism, action, implication, qualification, or evidence item.
- **Paragraph:** advance the argument; do not merely restate the heading or preceding sentence.
- **Table cell/caption:** label or explain a real relationship; short labels can be meaningful.
- **Footer/source/legal:** classify as furniture or required boilerplate.

## 3. Container and standalone-meaning review

Read the complete slide, page, message, or section before assessing its blocks. First write its literal proposition in plain language: who or what is doing what, under which condition, with what evidence or consequence. If that proposition cannot be stated from the visible content, the container fails even when each sentence is grammatical.

Apply two tests to every substantive block:

1. **Local meaning:** Does the block make sense in relation to the other visible blocks in the same container?
2. **Standalone meaning:** Would a reader who has not heard the presenter understand the block's subject and purpose if it were copied into an agenda, excerpt, screenshot, or summary?

A headline may rely on nouns that are clearly defined elsewhere on the same slide or page. It may not rely on presenter narration, a distant slide, or an undefined abstraction. Flag vague referents and containers such as `this`, `these`, `it`, `wedge`, `route`, `decision`, `asset`, `capability`, `model`, `chain`, `boundary`, `platform`, or `solution` when the visible context does not say exactly what they denote. These words are not defects by themselves; the defect is missing referential meaning.

Also flag:

- a topic label presented as though it were a conclusion;
- a sentence fragment whose missing clause carries the actual point;
- a technically grammatical title that omits the actor, object, condition, comparison, or consequence needed to understand it;
- a headline that merely announces the content below rather than interpreting it;
- multiple meaningful blocks that do not combine into a coherent standalone proposition;
- shorthand that is intelligible only to the author or project team.

When a slide or page fails at container level, bind `needs-improvement` to the headline or highest-level substantive block and explain the missing relationship. Do not let individually meaningful cards or bullets compensate for a meaningless headline.

### Actionable-title gate

Classify the container's primary job as `inform`, `explain`, `decide`, or `act`. For `decide` and `act`, apply a stricter title test: after reading the title alone, can the intended reader say what must be decided or done, by whom when the source identifies an owner, and under what gating condition when one exists?

Flag a title when it:

- merely describes a process or fact while the real decision or next step is buried in a callout;
- uses an aphorism, slogan, equation, or insider shorthand instead of the operational implication;
- says that something “needs” owners, evidence, validation, or approval without telling the reader what to do about that requirement;
- announces a topic such as `buyer system`, `operating model`, or `next steps` without stating the consequence;
- leaves the reader unable to answer `What should happen next?`.

When the source supports it, convert the title into a direct decision rule or imperative and use the body to explain why. Examples of useful structures include `Do not proceed until [condition]`, `[Owner] must [action] before [event]`, and `Choose [option] because [evidence]`. Treat these as logical structures, not stock phrases: retain the document's vocabulary and avoid forcing every title into the same syntax.

If the body contains a specific next step, compare it with the headline. Put the governing action or decision in the headline and reserve the callout for the immediate execution step. Do not repeat the same instruction twice.

## 4. Value test

Answer all five:

1. What does the block literally mean?
2. What new information does it add relative to nearby blocks?
3. What makes it useful: fact, reason, mechanism, evidence, action, decision, qualification, or necessary transition?
4. Is it relevant to the communication job and its location?
5. Could the wording fit three unrelated subjects without material change?

Use observable language. “Sounds AI-generated” is never a reason.

Indicators of low value include:

- buzzword or abstract-noun density without an actor, action, object, or consequence;
- generic claims such as “unlocking value through innovation”;
- headline/body duplication;
- three near-synonymous bullets;
- unsupported significance or causality;
- labels that promise insight while the body supplies none;
- universal-fit sentences;
- transitions or summaries with no information gain.

Concrete nouns and numbers are not automatically meaningful. A metric without a denominator, source, baseline, or decision may still fail.

## 5. Verdicts

- `meaningful`: state the meaning and unique value.
- `needs-improvement`: identify the missing information and give a safe improvement or an explicit request for facts.
- `exempt`: identify the furniture/legal/source role.
- `abstain`: explain why meaning cannot be safely judged.

## 6. Improvement behavior

Never invent the fact that would make a block valuable. Prefer:

- deleting redundant text;
- stating the direct claim;
- replacing evaluation with a verified measure;
- naming the actor, mechanism, comparison, owner, or decision;
- converting a topic headline into a supported takeaway;
- asking for the missing evidence when it is essential.

Set `replacement` to a complete source-faithful replacement only when the existing source already supports it. Otherwise leave `replacement` empty and use `improvement` to state what evidence or decision is missing. Replacements must preserve protected tokens, remain within the original block's role and formatting capacity, and may not add a claim, number, name, citation, or implication that the source does not support.
