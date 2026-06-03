"""Skill resolution module for Agent Execution Jobs (issue #32).

Resolves installed skills from the convergence directory and applies a
per-phase allowlist so each Agent Execution Job phase receives only the
skills it is permitted to use.

Convergence directory
---------------------
``AGENT_SKILLS_DIR`` env var → ``/usr/local/share/agent-skills/installed``

This mirrors the prompt-template convention (``AGENT_PROMPTS_DIR`` →
``/usr/local/share/agent-prompts``) so the two resource trees are deployed
side-by-side and both are overridable in tests.

Allowlist semantics
-------------------
``allowlist`` is an optional ``{phase: list[str] | []}`` mapping.

- Key absent  → all installed skills returned (no filtering for this phase).
- Key = ``[]`` → no skills returned (phase explicitly allows nothing).
- Key = names  → exactly those installed skills whose ``.name`` is listed.

Skills whose name is empty are always skipped and reported regardless of
the allowlist, because an un-named skill cannot be matched or used.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    # Avoid importing openhands at module load time so tests that don't need
    # the SDK can import skills.py without the package installed.
    pass

log = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR = "/usr/local/share/agent-skills/installed"


def _default_loader(installed_dir: Path) -> list[Any]:
    """Load installed skills from *installed_dir* via the openhands SDK."""
    from openhands.sdk.skills.installed import load_installed_skills

    return load_installed_skills(installed_dir=installed_dir)


def resolve_skills(
    phase: str,
    allowlist: dict | None,
    *,
    _loader: Callable[[Path], list[Any]] | None = None,
) -> tuple[list[Any], list[str]]:
    """Return ``(resolved_skills, skipped_report)`` for *phase*.

    Parameters
    ----------
    phase:
        Current agent phase name (e.g. ``"execute"``, ``"review"``).
    allowlist:
        Optional ``{phase: list[str] | []}`` mapping.  Pass ``None`` to allow
        all installed skills for every phase.
    _loader:
        Injected loader for testing.  Defaults to the real
        ``load_installed_skills`` call.

    Returns
    -------
    resolved_skills:
        List of ``Skill`` objects (or loader-returned objects) that pass all
        filters.
    skipped_report:
        List of human-readable strings describing each skipped entry.
    """
    if _loader is None:
        _loader = _default_loader

    skills_dir_env = os.environ.get("AGENT_SKILLS_DIR")
    installed_dir = Path(skills_dir_env if skills_dir_env else _DEFAULT_SKILLS_DIR)

    raw: list[Any] = _loader(installed_dir)

    resolved: list[Any] = []
    skipped: list[str] = []

    # Step 1: filter malformed (un-named) skills
    valid: list[Any] = []
    for skill in raw:
        name = getattr(skill, "name", None)
        if not name:
            skipped.append(f"unnamed skill skipped (no name attribute)")
            log.warning("skipping unnamed skill: %r", skill)
        else:
            valid.append(skill)

    # Step 2: apply per-phase allowlist
    if allowlist is None or phase not in allowlist:
        # No restriction for this phase — all valid skills pass through
        resolved = valid
    else:
        permitted: list[str] = allowlist[phase]
        if not permitted:
            # Explicit empty list → no skills for this phase
            resolved = []
            # Don't report valid skills as "skipped" — they're not in scope
        else:
            permitted_set = set(permitted)
            for skill in valid:
                if skill.name in permitted_set:
                    resolved.append(skill)
                else:
                    skipped.append(skill.name)
                    log.debug(
                        "skill %r not in allowlist for phase %r — skipping",
                        skill.name,
                        phase,
                    )

    log.info(
        "resolved %d skill(s) for phase %r; skipped %d",
        len(resolved),
        phase,
        len(skipped),
    )
    return resolved, skipped
