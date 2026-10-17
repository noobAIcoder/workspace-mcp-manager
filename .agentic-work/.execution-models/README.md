# Execution Model Catalog

Execution Models are canonical framework artifacts describing execution topology: accountable actors, tool allocation, delegation, capability constraints, and repository/workspace mechanics. Work Models own workflow obligations; project-designated Model Guidance owns generic authorization, execution, validation, completion, and output policy.

## Runtime activation

Repository presence does not activate an Execution Model.

For normal project execution, the operator selects one canonical model, supplies that file as a project source, and designates that source as the active Execution Model in project settings.

The runtime uses only that designated source. It MUST NOT inspect this framework directory to discover, compare, select, or load an Execution Model. Available capabilities affect executable operations, not model identity; changing the active model is an operator configuration action. If no active Execution Model is available, topology-dependent work is blocked.

## Canonical models

| Canonical model | Topology |
| --- | --- |
| [ChatGPT + GitHub](ChatGPT+GitHub/ChatGPT+GitHub_execution_model.md) | ChatGPT works through GitHub; operator performs unavailable target-side execution |
| [ChatGPT + MCP + GitHub](ChatGPT+MCP+GitHub/ChatGPT+MCP+GitHub_execution_model.md) | ChatGPT operates workspace/runtime through MCP and repository operations through GitHub |
| [ChatGPT + MCP + Codex + GitHub](ChatGPT+MCP+Codex+GitHub/ChatGPT+MCP+Codex+GitHub_execution_model.md) | Adds bounded Codex implementation/execution delegated by ChatGPT |
| [ChatGPT + MCP + RLM + Codex + GitHub](ChatGPT+MCP+RLM+Codex+GitHub/ChatGPT+MCP+RLM+Codex+GitHub_execution_model.md) | Adds RLM investigation/orchestration with bounded Codex execution |

Each canonical file is standalone: it does not require this catalog or another framework file for runtime topology semantics.

## Framework maintenance

The canonical framework copy and the configured project-source copy are distinct. Installing or updating canonical files MUST NOT modify project-source configuration, replace the configured source, change active-model designation, or treat a newer framework copy as active.

Normal execution MUST NOT compare the configured source with the framework copy unless explicitly requested.

This README is a catalog and maintenance document, not runtime input.
