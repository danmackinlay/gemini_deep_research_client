Goal and acceptance criteria

You want a human-readable Markdown report where:
	1.	Every citation is a clickable URL (inline links or footnotes).
	2.	Citations are not hallucinated by the model; they come from structured provenance captured during the Deep Research run.
	3.	The report is reproducible/auditable: you can show exactly which URLs were consulted.

Your current output ([cite: 4], and a “sources” list with just names) fails all three.

⸻

Diagnosis of what’s actually wrong (in your implementation, not “the model”)

You’re treating the Deep Research agent as if the only output is a Markdown string, and you’re asking the model to “do citations in prose”.

That is the wrong layer.

The Interactions API already has a first-class citation/provenance mechanism:
	•	Text blocks and streamed text deltas can include annotations: start/end byte ranges plus a source identifier.  ￼
	•	The streamed event schema supports tool/result deltas like google_search_result and url_context_result, which explicitly carry URLs, titles, and fetch statuses.  ￼

Your code (as shown in deep_research_app/deep_research.py) only accumulates:
	•	delta.text (plain text), and
	•	a best-effort “thought summary” string,

…and discards everything else. That guarantees you will not get URL provenance even if the API provided it.

So the “[cite: N]” format you’re seeing is not “Deep Research provenance”; it’s the model improvising a citation convention because you forced the whole thing into a single Markdown channel.

⸻

30,000 ft design pattern: what you should be building

A research report is two artifacts, not one:
	1.	Content: the narrative report (Markdown/HTML/Docx/etc).
	2.	Provenance: a structured citation graph (spans → sources), plus a list of sources (URL/title/status).

Deep Research (via Interactions) is capable of giving you both, but not necessarily in the same text channel. Your job is to:
	•	capture the structured provenance,
	•	then render it into Markdown links deterministically.

Do not ask the model to be your citation formatter.

⸻

The correct extraction and rendering pipeline (instructions)

Step 1 — Stop relying on the model’s in-text citation syntax

Treat any of these as presentation noise that you will remove:
	•	[cite: 4], [cite: 1, 9]
	•	a “Sources used” section written by the model

Reason: these are not guaranteed to be URL-complete, stable, or accurate. The API has better data.

Implementation rule: your renderer strips all model-invented citation tokens and replaces them with your own citations built from metadata.

⸻

Step 2 — Capture provenance from the Interactions API (you are currently dropping it)

You need to capture at least one of the following (preferably both):

2A) Final output annotations (most robust)
When the interaction completes, retrieve the full interaction object and read:
	•	The final TextContent.text
	•	The final TextContent.annotations[]
(byte ranges + source)  ￼

Even if you streamed during execution, still do a final interactions.get(id) at completion and build the report from that canonical final object.

Why: streaming can be interrupted; annotations can be partial; final is canonical.

2B) Tool/result deltas for URLs (best for “what was actually consulted”)
In the event stream you should record (or later retrieve from stored outputs/events if present):
	•	google_search_result deltas (contain url, title, etc.)  ￼
	•	url_context_result deltas (contain fetched url and status: success/paywall/unsafe/error)  ￼

This is the cleanest way to produce a defensible “sources consulted” list, including failure modes (paywalls, unsafe pages).

Blunt critique: your current _process_chunk() ignores these delta types, so you are throwing away the only reliable place URLs may appear.

⸻

Step 3 — Build a structured internal citation model (don’t go straight to Markdown)

Create an internal structure like:

{
  "text": "...final report text...",
  "annotations": [
    {"start": 123, "end": 220, "source": "https://..."},
    {"start": 400, "end": 450, "source": "Britannica: Paris"}
  ],
  "sources": [
    {"url": "https://...", "title": "...", "status": "success", "seen_in": ["google_search_result", "url_context_result"] }
  ]
}

Key rules:
	•	Byte offsets: start_index / end_index are in bytes, not Unicode codepoints.  ￼
Your insertion logic must operate on UTF‑8 bytes or carefully map bytes→string indices.
	•	Normalize URLs: de-dup by a canonical URL (strip obvious trackers; keep stable params if needed).
	•	Source identity: annotation.source “could be a URL, title, or other identifier”.  ￼
So implement:
	•	If it looks like a URL (http:// or https://), treat it as canonical.
	•	Else, try to map it to a URL using your captured google_search_result titles and/or fetched url_context_result URLs.
	•	If it can’t be mapped, keep it as “unresolved_source_label” and do not fabricate a URL.

⸻

Step 4 — Decide your Markdown citation format (then enforce it)

Pick one and enforce it in code:

Option A: Markdown footnotes (best for readability)
In text: ... Paris has ~2.1M residents.[^12]

Footnotes section:

[^12]: Britannica — Paris. https://www.britannica.com/place/Paris (accessed 2025-12-16)

Pros: clean prose, clickable link in footnote, supports multiple citations.

Option B: Inline numeric links (best for “immediately clickable”)
In text: ... Paris has ~2.1M residents. [[12]](https://...)

Pros: directly clickable.

Do not keep the model’s [cite: N] format. It’s nonstandard and not automatically linkable.

⸻

Step 5 — Render citations from annotations deterministically

Algorithm sketch:
	1.	Start from canonical final_text (from the final interaction).
	2.	Sort annotations by end_index descending (byte offsets).
	3.	For each annotation span:
	•	assign a citation number for its source (dedupe sources),
	•	insert [^n] (or [[n]](url)) at end_index.
	4.	Append the “Sources” section generated from your deduped source list.

Important details:
	•	Because indices are in bytes, insertion must be done in byte space or via a byte→char index map.  ￼
	•	Descending insertion avoids shifting earlier indices.

⸻

Step 6 — Generate the bibliography from captured URLs, not from model prose

Your “Sources” section should be built from:
	•	URLs in google_search_result (with titles)  ￼
	•	URLs in url_context_result (with fetch status)  ￼
	•	URLs directly appearing in annotation.source (if the API gives them)

And should include:
	•	Title (if known)
	•	URL
	•	Retrieval status (success/paywall/unsafe/error)
	•	Accessed date (use interaction completion timestamp)

This produces auditable provenance independent of the model’s narrative.

⸻

Step 7 — Add “citation coverage” quality gates (so you can fail fast)

If you care about provenance, enforce it:
	•	Gate 1: ≥ X% of sentences contain at least one citation marker after rendering.
	•	Gate 2: every citation marker resolves to a URL (unless explicitly flagged unresolved).
	•	Gate 3: no “unresolved” sources for numeric claims (optional but strongly recommended).

If the gates fail, run a revision pass (see Step 9).

⸻

Step 8 — Prompt changes (useful, but not the core fix)

Prompting is secondary to extraction, but you should still add guardrails:
	•	“Do not include a bibliography section; citations will be handled separately.”
	•	“Avoid invented citation markers like [cite: N].”
	•	“Prefer primary/official sources; avoid low-quality blogs for quantitative claims.”

This reduces cleanup and improves source quality, but it does not replace Step 2–6.

⸻

Step 9 — Optional: use a second pass to reformat (because Deep Research can’t do structured outputs)

The Deep Research agent has a documented limitation: it doesn’t support structured outputs.  ￼

So if you want a model-assisted formatting pass, do it like this:
	1.	Run Deep Research (agent) → extract final_text + annotations + sources.
	2.	Run a follow-up interaction with a standard model (e.g. gemini-3-pro-preview) using previous_interaction_id, and provide your source map and formatting spec.  ￼
	3.	Tell the model: “You may only cite from this provided list of URLs; do not invent new sources.”

This is a safe use of the model: it’s formatting and placing citations, not inventing provenance.

⸻

Decision log (why these choices are the “with the grain” approach)
	1.	Use API annotations for citations, not model-generated tokens
Because the Interactions schema explicitly supports citation annotations with byte ranges.  ￼
	2.	Capture tool result deltas to get real URLs and statuses
Because google_search_result and url_context_result deltas are where URLs live, and they’re machine-readable.  ￼
	3.	Render citations yourself
Because Markdown is a presentation layer; provenance is structured data. Conflating them produces the garbage you showed.
	4.	Two-pass formatting is optional but clean
Because Deep Research doesn’t support structured output, but follow-ups via previous_interaction_id are explicitly supported.  ￼

⸻

What parts of the suspect agent report I agree/disagree with
	•	Agree with the core point: you’re capturing only the final text and hoping prompting fixes provenance.
	•	Disagree with the framing that the solution is “thought summaries” or “agent logs” as primary. That’s squishy. The Interactions API gives you hard objects (annotations + search/url results) that are the right substrate.  ￼

⸻

Minimal concrete checklist to implement next
	1.	After completion, always interactions.get(interaction_id) and store raw JSON.
	2.	Extract:
	•	final_text
	•	final_annotations
	•	all google_search_result / url_context_result blocks you can find (from stream log or outputs).
	3.	Build source map (URL → title/status).
	4.	Strip model [cite: ...] markers from the text.
	5.	Insert citations based on annotations, render as footnotes with clickable URLs.
	6.	Generate Sources section from captured URLs + statuses.
	7.	Add coverage gates; if failed, run revision pass with previous_interaction_id.

This will produce Markdown reports with real clickable provenance without fighting the model.