# ChatGPT + MCP + Codex + GitHub Execution Model

ChatGPT coordinates the work and may operate directly through MCP and GitHub. Codex performs bounded implementation or execution delegated by ChatGPT.

## Allocation

* ChatGPT: coordination, analysis, planning, specification work, review, and repository delivery.
* MCP: workspace and runtime access for ChatGPT and delegated Codex execution.
* GitHub: repository inspection, modification, and delivery for ChatGPT.
* Codex: bounded implementation or execution delegated by ChatGPT.
* Operator: human-only or otherwise unavailable operations.

## Codex handoff

* ChatGPT → Codex: bounded objective, applicable authority, workspace scope, permitted actions, and required return.
* Codex → ChatGPT: changed paths, executed commands/results, unresolved findings, and any required scope departure.

Unavailable Codex capability removes the dependent delegation path without changing the active Execution Model.
