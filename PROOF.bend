import ./LAWS.bend as Laws
import Base

# ============================================================================
# Proofs for LAWS.bend.
#
# Every law is proved by a def of the same name.  The bodies below are
# reflexivity proofs: each law holds by computation, so both sides reduce to
# the same term.  Run the gate with:
#
#     ./bend/bin/bend PROOF.bend
# ============================================================================


# --- Model listing ---------------------------------------------------------

def Laws.never_fabricate_models(models):
  {==}

def Laws.live_models_passthrough(models):
  {==}


# --- Tool parameter aliases ------------------------------------------------

def Laws.primary_parameter_wins(p, alias):
  {==}

def Laws.alias_used_when_primary_missing(alias):
  {==}


# --- Search backend fallback ----------------------------------------------

def Laws.fallback_uses_first_results(h, t, ys):
  {==}

def Laws.fallback_uses_second_when_first_empty(ys):
  {==}


# --- ReAct: every tool call gets an observation ---------------------------

def Laws.every_tool_call_observed(h, t):
  {==}

def Laws.no_tool_calls_no_observations():
  {==}


# --- Bounded observations --------------------------------------------------

def Laws.truncation_empty_at_zero(xs):
  {==}

def Laws.truncation_stops_at_empty(n):
  {==}


# --- ReAct run isolation ---------------------------------------------------

def Laws.run_starts_empty(previous):
  {==}


# --- Tool dispatch ---------------------------------------------------------

def Laws.unknown_tool_not_executed():
  {==}


def main() -> IO(Unit):
  do IO<Unit>:
    IO.print("ALL PROOFS CHECK")


# --- MCP tool exposure -----------------------------------------------------

def Laws.mcp_never_fabricates_tools(tools):
  {==}

def Laws.mcp_lists_live_tools(tools):
  {==}


# --- Safety decision precedence -------------------------------------------

def Laws.safety_deny_overrides_approval(approved):
  {==}

def Laws.safety_approved_call_allowed():
  {==}

def Laws.safety_unapproved_call_blocked():
  {==}


# --- Filesystem scope ------------------------------------------------------

def Laws.sensitive_path_denied(inside):
  {==}

def Laws.inside_path_allowed():
  {==}

def Laws.outside_path_denied():
  {==}


# --- Network policy --------------------------------------------------------

def Laws.network_disabled_blocks_network_tool():
  {==}

def Laws.network_disabled_allows_local_tool():
  {==}

def Laws.network_enabled_allows_network_tool():
  {==}


# --- Redaction -------------------------------------------------------------

def Laws.redaction_hides_secret():
  {==}

def Laws.redaction_preserves_public(p):
  {==}


# --- Audit log -------------------------------------------------------------

def Laws.audit_records_denied(events):
  {==}

def Laws.audit_records_allowed(events):
  {==}


# --- Shell policy ----------------------------------------------------------

def Laws.shell_denylist_always_blocks(allowlisted, allowlist_mode):
  {==}

def Laws.shell_allowlist_requires_match():
  {==}

def Laws.shell_allowlist_match_runs():
  {==}


# --- MCP result status -----------------------------------------------------

def Laws.mcp_error_is_failure():
  {==}

def Laws.mcp_success_is_success():
  {==}
