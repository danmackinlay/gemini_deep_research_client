## Background you need for correct design decisions

1. **Deep Research is an agent that returns a report, not a provenance graph.** The official Deep Research streaming example only handles `content.delta` of type `text` and `thought_summary` plus lifecycle events; it does **not** show any stream events for Google Search results, URL fetches, or per-span citation metadata. ([Google AI for Developers][1])
   **Consequence:** expecting “annotations” / byte ranges / tool-call logs from the Deep Research stream is a category error for the current product surface.

2. **Deep Research uses `google_search` and `url_context` internally by default, but that internal tool use is not surfaced to you as structured output.** ([Google AI for Developers][1])
   **Consequence:** the only provenance you can reliably extract *from Deep Research* is whatever the model prints into the report text (e.g., a Sources list), unless you add a second step yourself.

3. **If you want real structured provenance, Google Search grounding (on standard models) is the API that actually exposes it.** It returns `groundingMetadata` with:

   * `groundingChunks` (title + uri)
   * `groundingSupports` mapping `(startIndex,endIndex)` text spans to chunk indices
     and even provides the standard insertion pattern for inline clickable citations. ([Google AI for Developers][2])
     **Consequence:** “high quality citation UX” is *with the grain* of **search grounding**, not Deep Research.

4. **The `vertexaisearch.cloud.google.com/grounding-api-redirect/...` URLs are normal in Google’s grounding surfaces.** There isn’t a magic “give me the final URL without following redirects” field; people have explicitly asked for that and (at least in practice) you must follow the redirect to get the canonical target. ([GitHub][3])

Given those constraints, here is a plan that **actually works** today.

---

## Decision log (so you can stop thrashing)

* **Decision A:** Keep Deep Research as the “research/writing” engine, but treat citations as **untrusted text** that must be normalized post-hoc.
* **Decision B:** Standardize on a citation rendering that your Markdown renderers will actually display: **inline numeric Markdown links** like `[1](https://…)` (Gradio + most Markdown renderers handle this; footnotes are inconsistent).
* **Decision C:** Store provenance as **data**, not just pretty text: generate a `sources.json` sidecar from the report, and rebuild the Markdown “Sources” section from that canonical data.
* **Decision D (optional but recommended):** Resolve redirect URLs to final URLs via HTTP and store **both** (`redirect_url` and `final_url`).

If you need **span-level, machine-verifiable provenance**, stop using Deep Research and switch to Google Search grounding (see “Plan 2” at the end). ([Google AI for Developers][2])

---

## Plan 1: Keep Deep Research, but force and normalize linkable citations

### 1) Fix the root cause: your prompt is underspecified

Your current template says: “use inline citations and a references section” but does not define:

* the exact marker syntax,
* that citations must be clickable links,
* that the sources list must contain **URLs** (not “Britannica” as plain text).

So the model makes up `[cite: 4]` etc.

**Replace your “Evidence and Citations” instruction with a strict citation contract.** Example (drop-in block for `INITIAL_RESEARCH_TEMPLATE`):

```text
## Citation Contract (must follow exactly)
- Every factual claim must end with one or more inline citation links in this exact format:
  [1](URL), [2](URL)
  (i.e., square brackets with the source number; the bracket itself must be the clickable link.)
- Do NOT use “[cite: …]”, “(cite …)”, or any other citation syntax.
- At the end include a section exactly titled: "## Sources"
  Each entry must be numbered and must include a clickable Markdown link:
  1. [Title](URL) — Publisher/Domain, publication date (if known), accessed YYYY-MM-DD
- URLs must be explicit. Do not write just site names without URLs.
- If the only URL you have is a vertexaisearch redirect URL, still include it as URL.
```

Why this helps:

* Your output becomes trivially parseable (and already in final desired display format).
* Even if the model partially fails, you now have a well-defined “contract violation” you can detect automatically and trigger a formatting retry.

Deep Research explicitly supports steering output format through prompt instructions; this is exactly the intended lever. ([Google AI for Developers][1])

---

### 2) Add a deterministic “citation normalizer” step after the report is generated

You already persist `report_vN.md`. Insert a post-processing step before saving (or immediately after saving, but before displaying) that:

**2.1 Extracts sources → canonical mapping**

* Find the `## Sources` / `## References` / `Sources:` section.
* Parse entries into:

```json
{
  "1": {"title": "...", "url": "..."},
  "2": {"title": "...", "url": "..."}
}
```

**2.2 Normalizes inline citations**
Convert any of these into canonical `[n](url)` form:

* `[cite: 1, 9]` → `[1](url1), [9](url9)`
* ` [1]` (non-link) → `[1](url1)`
* ` (1)` if you see that → `[1](url1)` (optional)

**2.3 Rebuilds the “## Sources” section from the mapping**
Even if the model’s list is messy, you rewrite it into your house style.

**2.4 Integrity checks (fail closed)**

* Every citation number referenced in the body must exist in the source map.
* Every source number in the map should be used at least once (optional warning).
* Every source must have a URL. If not: mark report as invalid.

If invalid, automatically run a *formatting revision* using `previous_interaction_id` (see step 4).

This turns citation quality into engineering, not vibes.

---

### 3) Resolve redirect URLs (optional, but this is how you get “nice” URLs)

When a source URL matches:

`https://vertexaisearch.cloud.google.com/grounding-api-redirect/...`

Do:

* HTTP `HEAD` (or `GET` if HEAD is blocked), `allow_redirects=True`
* record `final_url = response.url`

Store:

```json
{
  "id": 1,
  "redirect_url": "...grounding-api-redirect/...",
  "final_url": "https://www.britannica.com/place/Paris",
  "title": "Paris | Britannica"
}
```

Then in the Markdown:

* Use `final_url` for the clickable link (human-friendly),
* optionally include the redirect URL in a hidden comment or in `sources.json` for audit.

This “follow the redirect” requirement is not you being dumb; it’s the current state of Google grounding outputs (people have asked for a non-fetch way and effectively don’t get one). ([GitHub][3])

---

### 4) Add an automatic “formatting retry” when the contract is violated

Deep Research supports follow-ups via `previous_interaction_id`. ([Google AI for Developers][1])
Use that to fix formatting without rerunning the whole research.

**Trigger conditions (examples):**

* sources section missing,
* fewer than X sources,
* any citation references an undefined source number,
* any source missing URL,
* presence of `[cite:`.

**Retry prompt (run with a *standard model*, not Deep Research, to save time):**

```text
Rewrite the previous report to comply with the Citation Contract below.
Do not change factual content unless required to attach correct citations.

Citation Contract:
- Use inline citations exactly like: [1](URL), [2](URL)
- Provide ## Sources with numbered [Title](URL) entries.
- No “[cite: …]”.
Return only Markdown.
```

Because Deep Research outputs are stored and you can reference them via interaction history, this is the cheap fix pass. ([Google AI for Developers][1])

---

### 5) Stop pretending “Evidence and Citations” is a separate appendix

If you want provenance, citations must live at the claim sites.

Update your template to make “Evidence” a *style requirement across sections*:

* “Main Findings: every bullet must end with citations”
* “Tables: add a Sources column with `[n](url)` links”

Otherwise you’ll keep getting a garbage “bibliography” that’s disconnected from claims.

---

## Critiques of your current design (actionable, not moralizing)

1. **You asked for citations but didn’t specify a citation grammar.** That’s why you got `[cite: 4]` and publisher-name-only “sources”. The model did what you asked: “citations exist”, not “citations are linkable and parseable”.

2. **You assumed Deep Research would expose provenance as structured metadata.** The Deep Research docs’ streaming example does not show that surface; it only emits thought summaries and text. ([Google AI for Developers][1])
   So “extract from logs” is the wrong strategy for this specific agent today.

3. **If you genuinely require audit-grade provenance, you picked the wrong feature.** Google Search grounding is the one that explicitly returns structured span→source mappings (`groundingSupports`) for building inline citations. ([Google AI for Developers][2])
   Deep Research is optimized for “analyst report”, not “provenance graph”.

---

## Plan 2 (recommended if provenance is non-negotiable): switch to Search Grounding for structured citations

If you want citations you can *prove* (segment ranges → source IDs), use a standard model with the `google_search` tool and consume `groundingMetadata.groundingSupports` + `groundingChunks` exactly as documented. ([Google AI for Developers][2])

High-level pattern:

1. Call `models.generate_content(... tools=[google_search] ...)`
2. Get `response.text` + `groundingMetadata`
3. Insert citations programmatically at `segment.endIndex` using chunk indices (the docs literally show this pattern). ([Google AI for Developers][2])
4. Ask the model to structure the report (sections/tables) **in the prompt**, but keep citations insertion as code driven by metadata, not by model obedience.

This is “with the grain” if what you want is provenance.

---

## Minimal implementation checklist (what to do next)

1. **Change `INITIAL_RESEARCH_TEMPLATE`** to include the strict Citation Contract (URLs + `[n](URL)` only).
2. **Add `citations.py`** with:

   * `parse_sources(report_text) -> sources_map`
   * `rewrite_inline_citations(report_text, sources_map) -> report_text`
   * `rebuild_sources_section(sources_map) -> markdown`
   * `validate(report_text, sources_map) -> ok/errors`
3. **Add `--resolve-redirects` option** that follows `grounding-api-redirect` URLs and stores both URLs.
4. **Add “formatting retry”** using `previous_interaction_id` when validation fails (use a cheaper model).
5. **Persist `sources.json`** beside `report_vN.md` so provenance is data, not vibes.

That pipeline yields Markdown outputs with clickable citations and a clean sources section even when the Deep Research agent’s native formatting is inconsistent.

[1]: https://ai.google.dev/gemini-api/docs/deep-research "Gemini Deep Research Agent  |  Gemini API  |  Google AI for Developers"
[2]: https://ai.google.dev/gemini-api/docs/google-search "Grounding with Google Search  |  Gemini API  |  Google AI for Developers"
[3]: https://github.com/googleapis/python-genai/issues/1512 "Grounding URLs are through vertexaisearch.cloud.google · Issue #1512 · googleapis/python-genai · GitHub"
