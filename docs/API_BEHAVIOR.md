# Gemini Deep Research API Behavior Briefing

## Overview

This document captures observed behaviors of the Gemini Deep Research API that inform our client implementation.

## Architecture Decision: Poll-Only

**As of December 2024, this client uses polling rather than streaming.**

### Rationale

Streaming was removed because:

1. **Non-trivial research queries reliably timeout**: The streaming connection times out after ~60 seconds (gateway_timeout), while actual research takes 3-10 minutes. Every real query required a polling fallback.

2. **Simple queries complete empty**: Fast queries that don't need research emit `interaction.complete` immediately with no content. These still required polling to get actual results.

3. **Streaming added complexity without benefit**: Partial text accumulation, event ID tracking, stream state persistence, and resume-from-event-id were all complexity that never worked in practice.

4. **Resume is simpler with polling**: Resuming an interrupted run just requires the interaction_id and polling until completion.

### Current Implementation

```python
# 1. Create interaction (returns immediately)
interaction_id = client.create_interaction(prompt)

# 2. Poll until completion (takes 3-10 minutes)
result = client.poll_interaction(interaction_id)

# Result contains:
#   - status: completed/failed/cancelled
#   - final_markdown: the report text
#   - usage: token counts for billing
```

## API Behavior

### Background Mode

The API is invoked with `background=True`, which starts a background research task:

```python
response = self._client.interactions.create(
    input=prompt,
    agent="deep-research-pro-preview-12-2025",
    background=True,
    stream=False,  # We don't use streaming
    ...
)
```

### Polling Behavior

When polling for status:

```python
interaction = self._client.interactions.get(interaction_id)
```

The interaction status progresses: `pending` → `running` → `completed`

When `completed`, the full text is available in `interaction.outputs[-1].text`.

### Usage Metadata

Available on completed interaction via `interaction.usage`:
- `total_input_tokens`
- `total_output_tokens`
- `total_tokens`
- `total_reasoning_tokens`

Note: Usage data may occasionally be missing in polling responses.

## Historical: Streaming Behavior

For reference, the streaming API (which we no longer use) emitted these event types:
- `interaction.start` - Contains the interaction ID
- `content.delta` - Contains text chunks or thinking summaries
- `interaction.complete` - Signals completion
- `error` - Contains error details (e.g., gateway_timeout)

Streaming was problematic because:
- Simple queries completed with empty content
- Complex queries timed out with gateway_timeout
- Both scenarios required polling fallback

## Test Observations

| Query Type | Behavior |
|------------|----------|
| Simple factual (e.g., "What is 2+2?") | Completes in ~1 minute |
| Research topic (e.g., "Market share analysis 2024") | Takes 3-10 minutes |
| Short research (e.g., "Capital of France") | Completes in 1-2 minutes |

All queries work reliably with polling. The complexity of streaming provided no practical benefit.

---

*Last updated: December 2024*
*Based on agent: deep-research-pro-preview-12-2025*
