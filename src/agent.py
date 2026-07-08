"""FleetOps agent — single orchestrator with Anthropic tool calling.

A manual tool-use loop (rather than the SDK tool runner) so the eval harness
can capture the full tool-call trajectory for deterministic tool-selection
scoring. Loop pattern follows the Anthropic manual-agentic-loop reference.

Usage:
    ANTHROPIC_API_KEY=... python src/agent.py "Which trucks are overdue for service?"

Environment:
    FLEETOPS_AGENT_MODEL  (default: claude-opus-4-8)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from anthropic import Anthropic

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.tools import TOOL_FUNCTIONS, TOOL_SPECS
else:
    from .tools import TOOL_FUNCTIONS, TOOL_SPECS

DEFAULT_MODEL = os.environ.get("FLEETOPS_AGENT_MODEL", "claude-opus-4-8")
MAX_TURNS = 8

SYSTEM_PROMPT = """\
You are FleetOps, a fleet-maintenance operations assistant for a trucking company.
You answer questions using ONLY the tools provided over a synthetic SQLite database.
Today's date (the dataset's anchor date) is 2026-07-08.

## Database schema
- trucks(truck_id, vin, make, model, year, mileage, status[active|in_shop|retired],
  home_depot, last_service_date, next_service_due_date, next_service_due_miles)
  * A truck is OVERDUE for service when status='active' AND
    (next_service_due_date < '2026-07-08' OR next_service_due_miles < mileage).
- maintenance_log(log_id, truck_id, event_date, event_type[preventive|repair|inspection|fault],
  fault_code, description, part_id, labor_hours, cost, technician, odometer)
- fault_codes(code, description, severity[low|medium|high|critical], system,
  recommended_action, related_part_id)
- parts(part_id, part_name, part_number, unit_cost, stock_qty, min_stock,
  supplier, lead_time_days)
  * A part is LOW STOCK when stock_qty < min_stock.

## Tools
- run_sql: read-only SELECT queries (single statement, 50-row cap).
- lookup_part: fault-code meaning + recommended part with live stock status.
- generate_report: produce a downloadable maintenance summary document.

## How to work
1. Think step by step about which tool(s) answer the question; prefer one
   aggregated SQL query over many small ones.
2. For fault-code, part, or stock questions use lookup_part; write SQL only for
   questions lookup_part cannot answer.
3. Use generate_report only when the user asks for a report/document/export.
4. Ground every number in a tool result — never estimate or invent data.
5. End your answer with a short "Sources:" line citing which tool calls the
   answer used, e.g. "Sources: run_sql (overdue truck query)".

## Hard rules
- You have NO ability to modify data. If asked to update, delete, insert, or
  otherwise change records, refuse briefly and explain the system is read-only.
- If a question cannot be answered from the database, say so plainly.
"""


@dataclass
class AgentResult:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)  # [{"name", "input", "result"}]
    stop_reason: str = ""
    turns: int = 0


def run_agent(question: str, model: str = DEFAULT_MODEL,
              client: Anthropic | None = None, max_turns: int = MAX_TURNS) -> AgentResult:
    """Run the orchestrator loop for one question and return the answer plus
    the full tool-call trajectory (used by the eval harness and dashboard)."""
    client = client or Anthropic()
    messages: list[dict] = [{"role": "user", "content": question}]
    trajectory: list[dict] = []

    for turn in range(1, max_turns + 1):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            tools=TOOL_SPECS,
            messages=messages,
        )

        if response.stop_reason == "refusal":
            return AgentResult(answer="The model declined to answer this request.",
                               tool_calls=trajectory, stop_reason="refusal", turns=turn)

        if response.stop_reason != "tool_use":
            answer = "".join(b.text for b in response.content if b.type == "text")
            return AgentResult(answer=answer, tool_calls=trajectory,
                               stop_reason=response.stop_reason or "", turns=turn)

        # Execute every requested tool; return all results in one user message.
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            func = TOOL_FUNCTIONS.get(block.name)
            result = (func(**block.input) if func
                      else {"error": f"Unknown tool: {block.name}"})
            trajectory.append({"name": block.name, "input": dict(block.input),
                               "result": result})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, default=str),
                "is_error": "error" in result,
            })
        messages.append({"role": "user", "content": tool_results})

    return AgentResult(answer="Reached the maximum number of agent turns without a final answer.",
                       tool_calls=trajectory, stop_reason="max_turns", turns=max_turns)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python src/agent.py "your question"')
    result = run_agent(" ".join(sys.argv[1:]))
    print(result.answer)
    print(f"\n[tools used: {[c['name'] for c in result.tool_calls] or 'none'}]")
