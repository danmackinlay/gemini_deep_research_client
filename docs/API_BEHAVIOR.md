# Gemini Deep Research API Behavior Briefing

## Overview

This document captures observed behaviors of the Gemini Deep Research API that inform our client implementation.

## Streaming Behavior

### Background Mode

The API is invoked with `background=True`, which starts a background research task:

```python
response = self._client.interactions.create(
    input=prompt,
    agent="deep-research-pro-preview-12-2025",
    background=True,
    stream=True,
    ...
)
```

### Event Types

The streaming API emits these event types:
- `interaction.start` - Contains the interaction ID
- `content.delta` - Contains text chunks (type: "text") or thinking summaries (type: "thought_summary")
- `interaction.complete` - Signals completion, includes usage metadata
- `error` - Contains error details (e.g., gateway_timeout)

### Stream Completion Scenarios

**Scenario 1: Fast completion (no research needed)**
For simple queries that don't require deep research:
1. `interaction.start` emitted immediately
2. `interaction.complete` emitted immediately
3. **No `content.delta` events** - text is empty
4. Status shows "completed" but no report content

**Scenario 2: Timeout before completion**
For complex queries requiring research:
1. `interaction.start` emitted
2. Some `content.delta` events may stream
3. `error` event with `gateway_timeout`
4. Stream terminates before completion

**Scenario 3: Full streaming success**
For queries that complete within timeout:
1. `interaction.start` emitted
2. Multiple `content.delta` events with text chunks
3. `interaction.complete` emitted with usage data
4. Full report available in accumulated text

## Polling Fallback

When streaming fails or times out, polling is required:

```python
interaction = self._client.interactions.get(interaction_id)
```

The interaction status progresses: `pending` → `in_progress` → `completed`

When `completed`, the full text is available in `interaction.outputs[-1].text`.

## Key Observations

1. **Empty stream completion is valid**: The API may emit `interaction.complete` without any text events for queries that don't require research. Our client should detect this and either poll for actual content or report the empty result appropriately.

2. **Gateway timeout is common**: Long-running research frequently exceeds streaming timeout. The polling fallback is essential.

3. **Usage metadata location varies**:
   - In streaming: Available on `interaction.complete` event via `chunk.interaction.usage`
   - In polling: Available on completed interaction via `interaction.usage`
   - Fields: `total_input_tokens`, `total_output_tokens`, `total_tokens`, `total_reasoning_tokens`

4. **Event IDs for resume**: Each `content.delta` chunk includes an `event_id` that can be used to resume interrupted streams via `last_event_id` parameter.

5. **Usage data may be missing**: When polling for results, the completed interaction may not include usage metadata. This appears to be API-side inconsistency - usage is more reliably available via streaming completion events than polling.

## Client Implementation Notes

### Current Behavior (after fix)
- Stream completes with content → process citations and save
- Stream completes but empty → automatically poll for actual result
- On timeout/error → falls back to polling automatically
- Polling continues until status is `completed`, `failed`, or `cancelled`

### Code Path (`_finalize_result`)
```python
if result.complete_via_stream and final_text.strip():
    # Case 1: Stream completed with content
elif result.complete_via_stream and not final_text.strip():
    # Case 2: Stream "completed" empty - poll for actual result
elif result.error and "Interrupted" in result.error:
    # Case 3: User interrupted - save for resume
else:
    # Case 4: Stream error/timeout - poll for result
```

### Future Improvements
- Add timeout configuration for polling phase
- Consider retry logic for transient gateway timeouts

## Test Queries

| Query Type | Expected Behavior |
|------------|------------------|
| Simple factual (e.g., "What is 2+2?") | Fast completion, likely empty stream |
| Research topic (e.g., "Market share analysis 2024") | Timeout likely, polling required |
| Short research (e.g., "Capital of France") | May complete via stream with text |

---

*Last updated: December 2024*
*Based on agent: deep-research-pro-preview-12-2025*
