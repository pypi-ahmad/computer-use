"""Engine Certification Framework.

Validates every engine and every tool declared in ``engine_capabilities.json``
against the live runtime environment.  Produces a structured diagnostic report
suitable for CI gating and pre-deployment checks.

Usage::

    # Programmatic
    from backend.certifier import EngineCertifier
    certifier = EngineCertifier()
    report = certifier.run_full_certification(deep=False)

    # CLI
    python -m backend.certifier --deep
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

_SCHEMA_FILENAME = "engine_capabilities.json"
# The schema file ships alongside this module inside ``backend/`` — use
# ``parent`` (not ``parent.parent``). The previous resolution pointed at
# the repo root and broke ``python -m backend.certifier`` on any clean
# checkout. Kept aligned with ``backend.engine_capabilities`` so the
# two discover the same file.
_DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / _SCHEMA_FILENAME

# ── Binary mappings derived from environment_requirements prose ───────────────

_REQUIREMENT_BINARY_MAP: Dict[str, str] = {
    "xdotool": "xdotool",
    "wmctrl": "wmctrl",
    "scrot": "scrot",
    "xclip": "xclip",
    "node.js": "node",
    "node": "node",
}

_ENV_CHECKS: Dict[str, str] = {
    "xdotool": "DISPLAY",
    "scrot": "DISPLAY",
    "wmctrl": "DISPLAY",
}


# ── Result dataclasses ───────────────────────────────────────────────────────

@dataclass
class EngineReport:
    """Certification result for a single engine."""

    engine: str
    healthy: bool = True
    schema_issues: List[str] = field(default_factory=list)
    missing_dependencies: List[str] = field(default_factory=list)
    missing_env_vars: List[str] = field(default_factory=list)
    invalid_actions: List[str] = field(default_factory=list)
    execution_probe: str = "skipped"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "engine": self.engine,
            "healthy": self.healthy,
            "schema_issues": self.schema_issues,
            "missing_dependencies": self.missing_dependencies,
            "missing_env_vars": self.missing_env_vars,
            "invalid_actions": self.invalid_actions,
            "execution_probe": self.execution_probe,
        }


@dataclass
class CertificationReport:
    """Aggregate certification result for all engines."""

    schema_version: str = "unknown"
    platform: str = ""
    engine_count: int = 0
    all_healthy: bool = True
    engines: List[EngineReport] = field(default_factory=list)
    global_issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "schema_version": self.schema_version,
            "platform": self.platform,
            "engine_count": self.engine_count,
            "all_healthy": self.all_healthy,
            "engines": [e.to_dict() for e in self.engines],
            "global_issues": self.global_issues,
        }


# ── Certifier ─────────────────────────────────────────────────────────────────

class EngineCertifier:
    """Engine and tool validation framework.

    Loads ``engine_capabilities.json`` and performs multi-phase validation:

    1. Schema integrity (structure, required fields)
    2. Engine registration (all engines present and well-formed)
    3. Environment requirements (binaries, env vars)
    4. Action consistency (categories <-> allowed_actions parity)
    5. Optional deep execution probes (safe, non-destructive)
    """

    def __init__(self, schema_path: str | Path | None = None) -> None:
        self._path = Path(schema_path) if schema_path else _DEFAULT_SCHEMA_PATH
        if not self._path.exists():
            raise FileNotFoundError(f"Schema not found: {self._path}")

        with open(self._path, "r", encoding="utf-8") as fh:
            self._raw: Dict[str, Any] = json.load(fh)

        self._version: str = self._raw.get("version", "unknown")
        self._engines_raw: Dict[str, Any] = self._raw.get("engines", {})

    # ── Phase 1: Schema Integrity ─────────────────────────────────────────

    def validate_schema_integrity(self) -> List[str]:
        """Verify top-level schema structure and required fields per engine."""
        issues: List[str] = []

        if "version" not in self._raw:
            issues.append("Missing top-level 'version' field")
        if "engines" not in self._raw:
            issues.append("Missing top-level 'engines' field")
            return issues

        for name, block in self._engines_raw.items():
            if not isinstance(block, dict):
                issues.append(f"[{name}] Engine block is not a dict")
                continue

            if "display_name" not in block:
                issues.append(f"[{name}] Missing 'display_name'")
            if "categories" not in block:
                issues.append(f"[{name}] Missing 'categories'")
            if "allowed_actions" not in block:
                issues.append(f"[{name}] Missing 'allowed_actions'")

        return issues

    # ── Phase 2: Engine Registration ──────────────────────────────────────

    def validate_engine_registration(self) -> List[str]:
        """Verify all engines are properly registered."""
        issues: List[str] = []

        if not self._engines_raw:
            issues.append("No engines defined in schema")

        for name, block in self._engines_raw.items():
            if not isinstance(block, dict):
                issues.append(f"[{name}] Invalid engine block type")
                continue

            if not block.get("display_name", "").strip():
                issues.append(f"[{name}] Empty or missing display_name")

            cats = block.get("categories", {})
            if isinstance(cats, dict):
                for cat_name, actions in cats.items():
                    if not isinstance(actions, list):
                        issues.append(f"[{name}] Category '{cat_name}' is not a list")
            elif not isinstance(cats, dict):
                issues.append(f"[{name}] categories is not a dict")

        return issues

    # ── Phase 3: Environment Requirements ─────────────────────────────────

    def validate_environment_requirements(self, engine_name: str) -> EngineReport:
        """Check binary and env-var deps for one engine."""
        report = EngineReport(engine=engine_name)
        block = self._engines_raw.get(engine_name)
        if block is None:
            report.healthy = False
            report.schema_issues.append(f"Engine '{engine_name}' not in schema")
            return report

        reqs: List[str] = block.get("environment_requirements", [])
        self._check_binary_deps(reqs, report)
        self._check_env_vars(reqs, report)

        if report.missing_dependencies or report.missing_env_vars:
            report.healthy = False

        return report

    def validate_binary_dependencies(self, engine_name: str) -> List[str]:
        """Return list of missing binary names for the given engine."""
        block = self._engines_raw.get(engine_name, {})
        reqs: List[str] = block.get("environment_requirements", [])
        missing: List[str] = []

        for req_text in reqs:
            req_lower = req_text.lower()
            for keyword, binary in _REQUIREMENT_BINARY_MAP.items():
                if keyword in req_lower and shutil.which(binary) is None:
                    if binary not in missing:
                        missing.append(binary)

        return missing

    def _check_binary_deps(self, reqs: List[str], report: EngineReport) -> None:
        """Populate *report* with any missing binary dependencies from *reqs*."""
        for req_text in reqs:
            req_lower = req_text.lower()
            for keyword, binary in _REQUIREMENT_BINARY_MAP.items():
                if keyword in req_lower and shutil.which(binary) is None:
                    if binary not in report.missing_dependencies:
                        report.missing_dependencies.append(binary)

    def _check_env_vars(self, reqs: List[str], report: EngineReport) -> None:
        """Populate *report* with any missing environment variables from *reqs*."""
        for req_text in reqs:
            req_lower = req_text.lower()
            for keyword, env_var in _ENV_CHECKS.items():
                if keyword in req_lower and not os.environ.get(env_var):
                    if env_var not in report.missing_env_vars:
                        report.missing_env_vars.append(env_var)

    # ── Phase 4: Action Consistency ───────────────────────────────────────

    def validate_allowed_actions(self, engine_name: str) -> List[str]:
        """Verify categories <-> allowed_actions parity for one engine."""
        issues: List[str] = []
        block = self._engines_raw.get(engine_name)
        if block is None:
            return [f"Engine '{engine_name}' not in schema"]

        raw_cats = block.get("categories", {})
        raw_actions = block.get("allowed_actions", [])

        if not isinstance(raw_cats, dict) or not isinstance(raw_actions, list):
            return issues

        cat_actions: Set[str] = set()
        for cat_name, action_list in raw_cats.items():
            if isinstance(action_list, list):
                cat_actions.update(action_list)

        allowed_set = set(raw_actions)

        for action in sorted(cat_actions - allowed_set):
            issues.append(
                f"[{engine_name}] Action '{action}' in categories but not in allowed_actions"
            )
        for action in sorted(allowed_set - cat_actions):
            issues.append(
                f"[{engine_name}] Action '{action}' in allowed_actions but not in any category"
            )

        seen: Set[str] = set()
        for action in raw_actions:
            if action in seen:
                issues.append(f"[{engine_name}] Duplicate action in allowed_actions: '{action}'")
            seen.add(action)

        return issues

    # ── Phase 5: Execution Probes ─────────────────────────────────────────

    def probe_execution(self, engine_name: str) -> str:
        """Run a safe, non-destructive execution probe for *engine_name*.

        Returns one of: ``"pass"``, ``"fail:<reason>"``, ``"skip:<reason>"``.
        """
        probe_fn = self._PROBES.get(engine_name)
        if probe_fn is None:
            return "skip:no probe defined"
        try:
            return probe_fn(self)
        except Exception as exc:
            return f"fail:{exc}"

    def _probe_computer_use(self) -> str:
        """Check xdotool/scrot availability and DISPLAY env for CU engine."""
        missing: List[str] = []
        for binary in ("xdotool", "scrot"):
            if shutil.which(binary) is None:
                missing.append(binary)
        if missing:
            return f"skip:missing binaries — {', '.join(missing)}"
        if not os.environ.get("DISPLAY"):
            return "skip:DISPLAY not set"
        return "pass"

    _PROBES: Dict[str, Any] = {
        "computer_use": _probe_computer_use,
    }

    # ── Full Certification Run ────────────────────────────────────────────

    def run_full_certification(self, deep: bool = False) -> CertificationReport:
        """Execute all validation phases and return an aggregate report."""
        report = CertificationReport(
            schema_version=self._version,
            platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
            engine_count=len(self._engines_raw),
        )

        schema_issues = self.validate_schema_integrity()
        if schema_issues:
            report.global_issues.extend(schema_issues)
            report.all_healthy = False

        reg_issues = self.validate_engine_registration()
        if reg_issues:
            report.global_issues.extend(reg_issues)
            report.all_healthy = False

        for engine_name in sorted(self._engines_raw):
            eng_report = self._certify_single_engine(engine_name, deep=deep)
            report.engines.append(eng_report)
            if not eng_report.healthy:
                report.all_healthy = False

        return report

    def _certify_single_engine(
        self, engine_name: str, deep: bool = False
    ) -> EngineReport:
        """Run all validation phases for a single engine and return its report."""
        eng_report = EngineReport(engine=engine_name)

        env_report = self.validate_environment_requirements(engine_name)
        eng_report.missing_dependencies = env_report.missing_dependencies
        eng_report.missing_env_vars = env_report.missing_env_vars

        action_issues = self.validate_allowed_actions(engine_name)
        eng_report.invalid_actions = action_issues

        if deep:
            eng_report.execution_probe = self.probe_execution(engine_name)

        if eng_report.invalid_actions:
            eng_report.healthy = False
        if eng_report.execution_probe.startswith("fail"):
            eng_report.healthy = False

        return eng_report


# ── CLI entry point ──────────────────────────────────────────────────────────

def _print_table(report: CertificationReport) -> None:
    header = f"{'Engine':<20} {'Healthy':<10} {'Missing Deps':<30} {'Execution Probe':<20}"
    sep = "-" * len(header)

    print(f"\n  CUA Engine Certification Report (schema v{report.schema_version})")
    print(f"  Platform: {report.platform}")
    print(f"  Engines:  {report.engine_count}")
    print()
    print(f"  {header}")
    print(f"  {sep}")

    for eng in report.engines:
        healthy_str = "YES" if eng.healthy else "NO"
        deps_str = ", ".join(eng.missing_dependencies) if eng.missing_dependencies else "-"
        probe_str = eng.execution_probe

        if len(deps_str) > 28:
            deps_str = deps_str[:25] + "..."
        if len(probe_str) > 18:
            probe_str = probe_str[:15] + "..."

        print(f"  {eng.engine:<20} {healthy_str:<10} {deps_str:<30} {probe_str:<20}")

    print(f"\n  {sep}")
    overall = "ALL HEALTHY" if report.all_healthy else "ISSUES DETECTED"
    print(f"  Overall: {overall}")

    if report.global_issues:
        print("\n  Global Issues:")
        for issue in report.global_issues:
            print(f"    - {issue}")

    for eng in report.engines:
        detail_lines: List[str] = []
        if eng.invalid_actions:
            detail_lines.extend(f"    Action: {a}" for a in eng.invalid_actions)
        if eng.missing_env_vars:
            detail_lines.extend(f"    Env: {v}" for v in eng.missing_env_vars)

        if detail_lines:
            print(f"\n  [{eng.engine}] Details:")
            for line in detail_lines:
                print(line)

    print()


def main() -> None:
    """CLI entry point for ``python -m backend.certifier``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="CUA Engine Certification — validate all engines and tools",
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="Run execution probes (requires live environment)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output raw JSON report instead of table",
    )
    parser.add_argument(
        "--schema", type=str, default=None,
        help="Path to engine_capabilities.json (default: auto-detect)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    certifier = EngineCertifier(schema_path=args.schema)
    report = certifier.run_full_certification(deep=args.deep)

    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_table(report)

    sys.exit(0 if report.all_healthy else 1)


if __name__ == "__main__":
    main()
