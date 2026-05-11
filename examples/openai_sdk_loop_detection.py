"""
Example: OpenAI Agents SDK + Conduit Loop Detection

Problem: An agent calls the same tool with identical params 3+ times after failures.
         Without Conduit, it burns tokens and time in an infinite retry loop.
         With Conduit, the loop is detected and recovery context is provided.

What this demonstrates:
  1. AgentLoopDetector tracks call signatures within a sliding window
  2. Identical loops detected at threshold (default: 3 identical calls)
  3. Tool cycling detected (A→B→A→B pattern)
"""

from conduit.intelligence.detector import AgentLoopDetector, ToolCallSpan, ToolFailureDetector

# --- Step 1: Set up detectors ---
loop_detector = AgentLoopDetector(window_size=10, threshold=3)
failure_detector = ToolFailureDetector()

print("=" * 60)
print("Scenario 1: Identical call loop (same tool, same params, repeated failures)")
print("=" * 60 + "\n")

# Simulate an agent retrying the same failed call
for i in range(4):
    span = ToolCallSpan(
        tool_id="send_email",
        outcome="tool_error",
        params={"to": "user@example.com", "subject": "Hello", "body": "Test"},
        exception_type="TimeoutError",
        exception_message="SMTP server timed out after 30s",
        latency_ms=30000,
        trace_id="trace-001",
        span_id=f"span-{i}",
        step_index=i,
    )

    # Classify the failure
    failure = failure_detector.classify(span)
    print(f"  Call #{i+1}: send_email → {failure.sub_type if failure else 'success'}")

    # Check for loops
    loop = loop_detector.check(span)
    if loop:
        print(f"\n  🚨 LOOP DETECTED at call #{i+1}!")
        print(f"     Class: {loop.failure_class}")
        print(f"     Type: {loop.sub_type}")
        print(f"     Severity: {loop.severity}")
        print(f"     Evidence: {loop.evidence}")
        print(f"\n     → Conduit would inject recovery context:")
        print(f'       "send_email has failed 3 times with identical parameters.')
        print(f'        Failure reason: TimeoutError (SMTP server timed out).')
        print(f'        Suggestions: (1) try alternative delivery method,')
        print(f'        (2) queue for later retry, (3) notify user of delay."')
        break

# --- Step 2: Tool cycling detection ---
print("\n\n" + "=" * 60)
print("Scenario 2: Tool cycling (A→B→A→B pattern)")
print("=" * 60 + "\n")

cycle_detector = AgentLoopDetector(window_size=10, threshold=3)

calls = [
    ("search_web", {"query": "python docs"}),
    ("read_file", {"path": "/docs/api.md"}),
    ("search_web", {"query": "python docs"}),
    ("read_file", {"path": "/docs/api.md"}),
]

for i, (tool, params) in enumerate(calls):
    span = ToolCallSpan(
        tool_id=tool, outcome="success", params=params,
        trace_id="trace-002", span_id=f"span-{i}", step_index=i,
    )
    print(f"  Call #{i+1}: {tool}({params})")
    loop = cycle_detector.check(span)
    if loop:
        print(f"\n  🔄 CYCLE DETECTED at call #{i+1}!")
        print(f"     Type: {loop.sub_type}")
        print(f"     Pattern: {loop.evidence['pattern']}")
        print(f"\n     → Agent is alternating between tools without progress.")
        print(f"       Conduit injects: 'You are cycling between search_web and")
        print(f"       read_file. Synthesize results from previous calls instead.'")
        break

print("""
--- WHAT THIS SOLVES ---
Without Conduit: Agent burns 10-50 LLM calls retrying the same failed action.
                 Cost: $0.50-5.00 per loop. User waits minutes for nothing.

With Conduit:    Loop detected at call #3 (configurable threshold).
                 Recovery context injected — agent adapts strategy.
                 Saves tokens, time, and user frustration.
""")
