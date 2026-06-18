"""Tests for .sentrux/rules.toml configuration integrity.

Ensures the sentrux configuration is consistent with the actual codebase structure.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = PROJECT_ROOT / ".sentrux" / "rules.toml"


@pytest.fixture()
def rules():
    """Load and parse .sentrux/rules.toml."""
    assert RULES_FILE.exists(), ".sentrux/rules.toml must exist"
    with open(RULES_FILE, "rb") as fh:
        return tomllib.load(fh)


class TestLayerPathsExist:
    """Every layer defined in rules.toml must reference at least one real path."""

    def test_core_layer_paths_exist(self, rules):
        """The core layer should point to actual files."""
        layer = _get_layer(rules, "core")
        if layer is not None:
            _assert_layer_files_exist(layer, "core")

    def test_activities_layer_paths_exist(self, rules):
        """The activities layer should point to actual files."""
        layer = _get_layer(rules, "activities")
        if layer is not None:
            _assert_layer_files_exist(layer, "activities")

    def test_workflows_layer_paths_exist(self, rules):
        """The workflows layer should point to actual files."""
        layer = _get_layer(rules, "workflows")
        if layer is not None:
            _assert_layer_files_exist(layer, "workflows")

    def test_infrastructure_layer_paths_exist(self, rules):
        """The infrastructure layer should point to actual files."""
        layer = _get_layer(rules, "infrastructure")
        if layer is not None:
            _assert_layer_files_exist(layer, "infrastructure")

    def test_tools_layer_paths_exist(self, rules):
        """The tools layer should point to actual files."""
        layer = _get_layer(rules, "tools")
        if layer is not None:
            _assert_layer_files_exist(layer, "tools")

    def test_messaging_layer_removed(self, rules):
        """The messaging layer must not be defined — src/devloop/messaging does not exist."""
        layer = _get_layer(rules, "messaging")
        assert layer is None, (
            "The 'messaging' layer references src/devloop/messaging/* which does not exist. "
            "Remove it from .sentrux/rules.toml."
        )


class TestBoundaryPaths:
    """Boundary rules must reference paths that are part of defined layers."""

    def test_no_boundary_references_messaging(self, rules):
        """No boundary rule should reference the deleted messaging layer."""
        for boundary in rules.get("boundaries", []):
            from_path = boundary.get("from", "")
            to_path = boundary.get("to", "")
            assert "messaging" not in from_path, (
                f"Boundary 'from' path '{from_path}' references messaging, which has been removed."
            )
            assert "messaging" not in to_path, (
                f"Boundary 'to' path '{to_path}' references messaging, which has been removed."
            )

    def test_boundary_from_referenced_layer(self, rules):
        """Every boundary 'from' path should correspond to a defined layer name."""
        layer_names = {layer["name"] for layer in rules.get("layers", [])}
        for boundary in rules.get("boundaries", []):
            from_path = boundary.get("from", "")
            # Patterns like "src/devloop/workflows/*" embed the layer name
            assert any(lname in from_path for lname in layer_names), (
                f"Boundary 'from' '{from_path}' does not reference a known layer ({layer_names})"
            )

    def test_boundary_to_referenced_layer(self, rules):
        """Every boundary 'to' path should correspond to a defined layer name."""
        layer_names = {layer["name"] for layer in rules.get("layers", [])}
        for boundary in rules.get("boundaries", []):
            to_path = boundary.get("to", "")
            assert any(lname in to_path for lname in layer_names), (
                f"Boundary 'to' '{to_path}' does not reference a known layer ({layer_names})"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_layer(rules: dict, name: str):
    """Return the first layer dict with the given name, or None."""
    for layer in rules.get("layers", []):
        if layer.get("name") == name:
            return layer
    return None


def _assert_layer_files_exist(layer, name: str):
    """Assert that every glob pattern in the layer matches at least one file."""
    for pattern in layer.get("paths", []):
        _assert_glob_has_match(PROJECT_ROOT, pattern)


def _assert_glob_has_match(root: Path, pattern: str):
    """Raise if the glob pattern matches zero files in the project root."""
    matches = list(root.glob(pattern))
    assert matches, (
        f"Path pattern '{pattern}' in rules.toml matches no files. "
        "Either create the files or remove the reference."
    )
