# ChatGPT + MCP + RLM + Codex + GitHub Execution Model

**Status:** Theoretical; not operationally qualified.

ChatGPT coordinates the work and may operate directly through MCP and GitHub. For delegated work, ChatGPT delegates to RLM; RLM may invoke Codex for bounded implementation or execution.

## Allocation

* ChatGPT: coordination, analysis, planning, specification work, and review.
* MCP: workspace and runtime access for ChatGPT.
* GitHub: repository inspection, modification, and delivery for ChatGPT.
* RLM: delegated investigation and task orchestration.
* Codex: bounded implementation or execution invoked by RLM.
* Operator: human-only or otherwise unavailable operations.

## Delegation chain

* ChatGPT → RLM: bounded objective, applicable authority, source or work scope, and required return.
* RLM → Codex: bounded implementation or execution derived from the delegated RLM task.

Unavailable RLM or Codex capability removes the dependent delegation path without changing the active Execution Model.
