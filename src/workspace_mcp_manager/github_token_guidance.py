from __future__ import annotations

from typing import Any


PROVIDER_TOKEN_URL = "https://github.com/settings/tokens"

# Recommended least-privilege baseline for the coding-tools repository workflow.
# This is deliberately broader than the permissions needed solely for PM3.1.1's
# account/repository-read verification probes.
FINE_GRAINED_PAT_REPOSITORY_PERMISSIONS = (
    ("Contents", "read_write", False),
    ("Issues", "read_write", False),
    ("Metadata", "read_only", True),
    ("Pull requests", "read_write", False),
)


def fine_grained_pat_guidance(repository: str | None = None) -> dict[str, Any]:
    normalized_repository = repository.strip() if isinstance(repository, str) and repository.strip() else None
    resource_owner = normalized_repository.split("/", 1)[0] if normalized_repository and "/" in normalized_repository else None
    return {
        "token_type": "fine_grained_personal_access_token",
        "resource_owner": resource_owner,
        "repository_access": "only_select_repositories",
        "repositories": [normalized_repository] if normalized_repository else [],
        "repository_permissions": [
            {"name": name, "access": access, "required": required}
            for name, access, required in FINE_GRAINED_PAT_REPOSITORY_PERMISSIONS
        ],
        "purpose": "recommended_coding_tools_repository_access",
    }


def fine_grained_pat_instruction_lines(repository: str | None = None) -> tuple[str, ...]:
    guidance = fine_grained_pat_guidance(repository)
    owner = guidance["resource_owner"] or "the repository owner"
    repositories = guidance["repositories"]
    selected = repositories[0] if repositories else "the repository used by this MCP instance"
    return (
        "Fine-grained personal access token settings:",
        f"  Resource owner: {owner}",
        "  Repository access: Only select repositories",
        f"  Repository: {selected}",
        "  Contents: Read and write",
        "  Issues: Read and write",
        "  Metadata: Read-only (required)",
        "  Pull requests: Read and write",
    )


__all__ = [
    "FINE_GRAINED_PAT_REPOSITORY_PERMISSIONS",
    "PROVIDER_TOKEN_URL",
    "fine_grained_pat_guidance",
    "fine_grained_pat_instruction_lines",
]
