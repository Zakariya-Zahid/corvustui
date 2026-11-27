"""Runtime safety guard used by the ReAct loop and MCP tool adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from corvustui.safety.approval import (
    ApprovalRequest,
    Approver,
    DenyApprover,
)
from corvustui.safety.audit import AuditLogger
from corvustui.safety.policy import (
    NETWORK_TOOLS,
    PATH_ARGUMENTS,
    SafetyPolicy,
)
from corvustui.safety.redact import redact_secrets
from corvustui.tools.base import Tool


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """Result of evaluating a tool call against the policy."""

    allowed: bool
    reason: str = ""
    rule: str = ""
    requires_approval: bool = False
    approved: bool | None = None


class SafetyGuard:
    """Enforce filesystem, shell, network, approval, and audit rules."""

    def __init__(
        self,
        policy: SafetyPolicy | None = None,
        approver: Approver | None = None,
        *,
        prompt_outside_roots: bool = False,
    ):
        self.policy = policy or SafetyPolicy()
        self.approver = approver or DenyApprover()
        self.audit_logger = AuditLogger(self.policy.audit_path)
        self.last_audit_error: str | None = None
        self.prompt_outside_roots = prompt_outside_roots

    async def authorize(
        self,
        tool: Tool,
        arguments: dict[str, Any] | None,
    ) -> SafetyDecision:
        """Evaluate a tool call, including an approval prompt when needed."""

        args = arguments or {}
        if not self.policy.enabled:
            return SafetyDecision(True, rule="disabled")

        name = tool.name
        outside_root_reason: str | None = None

        # Filesystem scope.  Shell cwd is handled by shell_decision below so
        # the error message can distinguish a bad cwd from a bad command.
        for key in PATH_ARGUMENTS:
            if key == "cwd" and name == "shell":
                continue
            value = args.get(key)
            if not isinstance(value, (str, Path)) or not str(value):
                continue
            allowed, reason, rule = self.policy.path_decision(value)
            if not allowed:
                if rule == "outside_allowed_root" and self.prompt_outside_roots:
                    outside_root_reason = outside_root_reason or reason
                    continue
                return SafetyDecision(False, reason, rule)

        if name == "shell":
            command = str(args.get("command") or args.get("sh") or "")
            cwd = args.get("cwd")
            cwd_path = Path(cwd) if isinstance(cwd, (str, Path)) and str(cwd) else None
            allowed, reason, rule = self.policy.shell_decision(
                command,
                cwd=cwd_path,
            )
            if not allowed:
                if rule == "outside_allowed_root" and self.prompt_outside_roots:
                    # Ensure sensitive paths, catastrophic shell patterns, and
                    # network gates still block even when outside roots prompt.
                    relaxed = replace(self.policy, allow_outside_roots=True)
                    r_allowed, r_reason, r_rule = relaxed.shell_decision(
                        command,
                        cwd=cwd_path,
                    )
                    if not r_allowed:
                        return SafetyDecision(False, r_reason, r_rule)
                    outside_root_reason = outside_root_reason or reason
                else:
                    return SafetyDecision(False, reason, rule)

        wants_network = name in NETWORK_TOOLS or bool(
            getattr(tool, "requires_network", False)
        )
        if wants_network and not self.policy.network_enabled:
            return SafetyDecision(
                False,
                f"Network access is disabled for tool '{name}'.",
                "network_disabled",
            )

        if outside_root_reason is not None:
            if self.approver is None:
                return SafetyDecision(
                    False,
                    outside_root_reason,
                    "outside_allowed_root",
                    requires_approval=True,
                )
            request = ApprovalRequest(
                tool=name,
                arguments=args,
                reason=outside_root_reason,
            )
            try:
                approved = await self.approver.approve(request)
            except Exception as exc:
                return SafetyDecision(
                    False,
                    f"Approval for tool '{name}' failed: {exc}",
                    "approval_error",
                    requires_approval=True,
                )
            if not approved:
                return SafetyDecision(
                    False,
                    outside_root_reason,
                    "outside_allowed_root",
                    requires_approval=True,
                    approved=False,
                )
            return SafetyDecision(
                True,
                rule="outside_root_approved",
                requires_approval=True,
                approved=True,
            )

        requires_approval = self.policy.require_approval and (
            bool(getattr(tool, "requires_approval", False))
            or name in set(self.policy.approval_tools)
            or name.startswith("mcp_")
        )
        if not requires_approval:
            return SafetyDecision(True, rule="allowed")

        if self.policy.auto_approve:
            return SafetyDecision(
                True,
                rule="auto_approved",
                requires_approval=True,
                approved=True,
            )

        if self.approver is None:
            return SafetyDecision(
                False,
                f"Tool '{name}' requires approval, but no approver is configured.",
                "approval_required",
                requires_approval=True,
            )

        request = ApprovalRequest(
            tool=name,
            arguments=args,
            reason="This tool can modify files, run commands, or call an external server.",
        )
        try:
            approved = await self.approver.approve(request)
        except Exception as exc:
            return SafetyDecision(
                False,
                f"Approval for tool '{name}' failed: {exc}",
                "approval_error",
                requires_approval=True,
            )

        if not approved:
            return SafetyDecision(
                False,
                f"Tool '{name}' was not approved.",
                "approval_denied",
                requires_approval=True,
                approved=False,
            )
        return SafetyDecision(
            True,
            rule="approval_granted",
            requires_approval=True,
            approved=True,
        )

    def redact(self, text: str) -> str:
        if not self.policy.redact_secrets:
            return text
        return redact_secrets(text)

    async def record(
        self,
        *,
        tool: str,
        arguments: dict[str, Any] | None,
        decision: SafetyDecision,
        success: bool | None = None,
        output: str | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Append an audit event.  Audit failures never break tool execution."""

        if not self.audit_logger.enabled:
            return

        safe_output = self.redact(output or "")
        if len(safe_output) > self.policy.max_audit_preview:
            safe_output = safe_output[: self.policy.max_audit_preview] + "... [truncated]"

        event: dict[str, Any] = {
            "event": "tool_call",
            "tool": tool,
            "arguments": self._safe_arguments(arguments),
            "allowed": decision.allowed,
            "rule": decision.rule,
            "reason": decision.reason,
            "requires_approval": decision.requires_approval,
            "approved": decision.approved,
        }
        if success is not None:
            event["success"] = success
        if output is not None:
            event["output_preview"] = safe_output
        if error is not None:
            event["error"] = self.redact(error)
        if duration_ms is not None:
            event["duration_ms"] = round(duration_ms, 3)

        try:
            await asyncio.to_thread(self.audit_logger.write, event)
            self.last_audit_error = None
        except Exception as exc:  # pragma: no cover - filesystem failure path
            self.last_audit_error = str(exc)

    def _safe_arguments(self, arguments: dict[str, Any] | None) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in (arguments or {}).items():
            safe[key] = self.redact(str(value))
        return safe
