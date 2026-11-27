import Base

# ============================================================================
# CorvusTUI laws
#
# These laws are the machine-checkable specification for the behaviours that
# were audited and fixed in the Python implementation.  The companion file
# PROOF.bend proves every claim with the bundled Bend compiler:
#
#     ./bend/bin/bend PROOF.bend
#
# Mapping to the implementation:
#   visible                -> cli._model_status / model listing
#   resolve                -> tools.builtin parameter aliases
#   first_nonempty         -> DuckDuckGo backend fallback chain
#   observe                -> react.loop multi-tool observations
#   take                   -> tools.builtin._truncate / observation limits
#   reset                  -> react.loop per-run state reset
#   dispatch               -> react.loop unknown-tool handling
# ============================================================================


# --- Model listing ---------------------------------------------------------
# Models are only shown when the provider is live.  When it is offline the
# visible list is empty: the CLI must never fall back to canned/sample names.
def visible(online: Bool, models: List<Nat>) -> List<Nat>:
  match online:
    case True{}:
      models
    case False{}:
      Nil{}

law never_fabricate_models:
  for models: List<Nat>
  {visible(False{}, models) == Nil{} : List<Nat>}

law live_models_passthrough:
  for models: List<Nat>
  {visible(True{}, models) == models : List<Nat>}


# --- Tool parameter aliases ------------------------------------------------
# 0n is the "argument omitted" sentinel.  The primary parameter wins; the
# alias is used only when the primary is absent.
def resolve(primary: Nat, alias: Nat) -> Nat:
  match primary:
    case 0n:
      alias
    case 1n+p:
      primary

law primary_parameter_wins:
  for p: Nat
  for alias: Nat
  {resolve(1n+p, alias) == 1n+p : Nat}

law alias_used_when_primary_missing:
  for alias: Nat
  {resolve(0n, alias) == alias : Nat}


# --- Search backend fallback ----------------------------------------------
# Try the first backend; use the second only when the first returned nothing.
def first_nonempty(xs: List<Nat>, ys: List<Nat>) -> List<Nat>:
  match xs:
    case Nil{}:
      ys
    case Con{h, t}:
      xs

law fallback_uses_first_results:
  for h: Nat
  for t: List<Nat>
  for ys: List<Nat>
  {first_nonempty(Con{h, t}, ys) == Con{h, t} : List<Nat>}

law fallback_uses_second_when_first_empty:
  for ys: List<Nat>
  {first_nonempty(Nil{}, ys) == ys : List<Nat>}


# --- ReAct: every tool call gets an observation ---------------------------
# A response containing several tool calls must produce one observation per
# call, in the same order.
def observe(names: List<Nat>) -> List<Nat>:
  match names:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      Con{h, observe(t)}

law every_tool_call_observed:
  for h: Nat
  for t: List<Nat>
  {observe(Con{h, t}) == Con{h, observe(t)} : List<Nat>}

law no_tool_calls_no_observations:
  {observe(Nil{}) == Nil{} : List<Nat>}


# --- Bounded observations --------------------------------------------------
# Truncation keeps at most n elements and therefore cannot grow the context
# without bound.
def take(n: Nat, xs: List<Nat>) -> List<Nat>:
  match n:
    case 0n:
      Nil{}
    case 1n+p:
      match xs:
        case Nil{}:
          Nil{}
        case Con{h, t}:
          Con{h, take(p, t)}

law truncation_empty_at_zero:
  for xs: List<Nat>
  {take(0n, xs) == Nil{} : List<Nat>}

law truncation_stops_at_empty:
  for n: Nat
  {take(1n+n, Nil{}) == Nil{} : List<Nat>}


# --- ReAct run isolation ---------------------------------------------------
# Interactive mode reuses one ReActLoop.  A new run always starts with empty
# per-run state, regardless of how many steps the previous run recorded.
def reset(previous: Nat) -> Nat:
  0n

law run_starts_empty:
  for previous: Nat
  {reset(previous) == 0n : Nat}


# --- Tool dispatch ---------------------------------------------------------
# Only known tools execute (1n); unknown names are rejected (0n).
def dispatch(known: Bool) -> Nat:
  match known:
    case True{}:
      1n
    case False{}:
      0n

law unknown_tool_not_executed:
  {dispatch(False{}) == 0n : Nat}


# ============================================================================
# Phase 2 laws: MCP exposure and safety guardrails
#
# Mapping to the implementation:
#   mcp_tools       -> mcp.manager / mcp.client tool discovery
#   safety_decide   -> safety.guard.SafetyGuard.authorize
#   path_allowed    -> safety.policy.SafetyPolicy.path_decision
#   network_allowed -> safety.policy.SafetyPolicy network gate
#   redact          -> safety.redact.redact_secrets
#   audit           -> safety.guard.SafetyGuard.record
#   shell_allowed   -> safety.policy.SafetyPolicy.shell_decision
#   mcp_status      -> mcp.tools.MCPTool.execute
# ============================================================================


# --- MCP tools are only exposed when a server is connected -----------------
def mcp_tools(connected: Bool, tools: List<Nat>) -> List<Nat>:
  match connected:
    case True{}:
      tools
    case False{}:
      Nil{}

law mcp_never_fabricates_tools:
  for tools: List<Nat>
  {mcp_tools(False{}, tools) == Nil{} : List<Nat>}

law mcp_lists_live_tools:
  for tools: List<Nat>
  {mcp_tools(True{}, tools) == tools : List<Nat>}


# --- Safety decision precedence -------------------------------------------
# A denied call stays denied even if approval was granted; an approved,
# non-denied call executes.  Bend only matches one binder per function, so
# the second predicate is converted by a helper.
def bool_nat(value: Bool) -> Nat:
  match value:
    case True{}:
      1n
    case False{}:
      0n

def safety_decide(denied: Bool, approved: Bool) -> Nat:
  match denied:
    case True{}:
      0n
    case False{}:
      bool_nat(approved)

law safety_deny_overrides_approval:
  for approved: Bool
  {safety_decide(True{}, approved) == 0n : Nat}

law safety_approved_call_allowed:
  {safety_decide(False{}, True{}) == 1n : Nat}

law safety_unapproved_call_blocked:
  {safety_decide(False{}, False{}) == 0n : Nat}


# --- Filesystem scope ------------------------------------------------------
def path_allowed(inside: Bool, sensitive: Bool) -> Nat:
  match sensitive:
    case True{}:
      0n
    case False{}:
      bool_nat(inside)

law sensitive_path_denied:
  for inside: Bool
  {path_allowed(inside, True{}) == 0n : Nat}

law inside_path_allowed:
  {path_allowed(True{}, False{}) == 1n : Nat}

law outside_path_denied:
  {path_allowed(False{}, False{}) == 0n : Nat}


# --- Network policy --------------------------------------------------------
def network_allowed(enabled: Bool, wants_network: Bool) -> Nat:
  match wants_network:
    case True{}:
      bool_nat(enabled)
    case False{}:
      1n

law network_disabled_blocks_network_tool:
  {network_allowed(False{}, True{}) == 0n : Nat}

law network_disabled_allows_local_tool:
  {network_allowed(False{}, False{}) == 1n : Nat}

law network_enabled_allows_network_tool:
  {network_allowed(True{}, True{}) == 1n : Nat}


# --- Redaction -------------------------------------------------------------
# 0n is a secret; any positive value is public.  Secrets become 9n.
def redact(value: Nat) -> Nat:
  match value:
    case 0n:
      9n
    case 1n+p:
      1n+p

law redaction_hides_secret:
  {redact(0n) == 9n : Nat}

law redaction_preserves_public:
  for p: Nat
  {redact(1n+p) == 1n+p : Nat}


# --- Audit log -------------------------------------------------------------
def audit(events: List<Nat>, decision: Nat) -> List<Nat>:
  Con{decision, events}

law audit_records_denied:
  for events: List<Nat>
  {audit(events, 0n) == Con{0n, events} : List<Nat>}

law audit_records_allowed:
  for events: List<Nat>
  {audit(events, 1n) == Con{1n, events} : List<Nat>}


# --- Shell policy ----------------------------------------------------------
def shell_mode(allowlist_mode: Bool, allowlisted: Bool) -> Nat:
  match allowlist_mode:
    case True{}:
      bool_nat(allowlisted)
    case False{}:
      1n

def shell_allowed(denied: Bool, allowlisted: Bool, allowlist_mode: Bool) -> Nat:
  match denied:
    case True{}:
      0n
    case False{}:
      shell_mode(allowlist_mode, allowlisted)

law shell_denylist_always_blocks:
  for allowlisted: Bool
  for allowlist_mode: Bool
  {shell_allowed(True{}, allowlisted, allowlist_mode) == 0n : Nat}

law shell_allowlist_requires_match:
  {shell_allowed(False{}, False{}, True{}) == 0n : Nat}

law shell_allowlist_match_runs:
  {shell_allowed(False{}, True{}, True{}) == 1n : Nat}


# --- MCP result status -----------------------------------------------------
def mcp_status(is_error: Bool) -> Nat:
  match is_error:
    case True{}:
      0n
    case False{}:
      1n

law mcp_error_is_failure:
  {mcp_status(True{}) == 0n : Nat}

law mcp_success_is_success:
  {mcp_status(False{}) == 1n : Nat}
