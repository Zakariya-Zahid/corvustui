"""Safety policy: filesystem scope, shell rules, network access, approval.

The policy is intentionally data-driven so it can be tested directly and
loaded from environment settings.  It is a guardrail, not a kernel sandbox:
a shell command can still do things the token inspection cannot see, which is
why shell/write/edit tools additionally require explicit approval by default.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

ShellMode = Literal["deny_dangerous", "allowlist", "unrestricted"]

PATH_ARGUMENTS = ("path", "file_path", "output_file", "directory", "cwd")
NETWORK_TOOLS = {"web_search", "duckduckgo_search"}

# Files and directories that should never be exposed to a model by default.
_SENSITIVE_NAMES = {
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "service-account.json",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
_SENSITIVE_DIRS = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.kube",
    "~/.docker",
    "~/.config/gcloud",
    "/etc/ssh",
    "/etc/sudoers.d",
    "/root/.ssh",
    "/proc",
    "/sys/firmware",
)
_SENSITIVE_FILES = ("/etc/shadow", "/etc/sudoers", "/etc/gshadow")
_ENV_FILE = re.compile(r"^\.env(?:\.(?:local|development|production|test|staging))?$")

# Catastrophic commands.  These are blocked even after approval unless the
# policy is explicitly set to "unrestricted".
DEFAULT_SHELL_DENY_PATTERNS = (
    r"(?:^|[;&|]\s*)rm\s+(?:-[A-Za-z]*[rRfF][A-Za-z]*\s+)+"
    r"(?:/|~|\$HOME|\$\{HOME\})(?:\s|$|/\*|\*)",
    # ``rm -rf .`` / ``rm -rf *`` destroy the working tree just as
    # effectively as targeting ``/``.  Named paths inside the root remain
    # allowed and are still approval-gated.
    r"(?:^|[;&|]\s*)rm\s+(?:-[A-Za-z]*[rRfF][A-Za-z]*\s+)+"
    r"(?:\.|\.\.|\*|\./\*|\.\./\*)(?:\s|$)",
    r"(?:^|[;&|]\s*)(?:mkfs(?:\.[A-Za-z0-9]+)?|fdisk|parted|wipefs)\b",
    r"\bdd\b[^\n]*\bof=/dev/",
    r"(?:^|[;&|]\s*)(?:shutdown|reboot|halt|poweroff)\b",
    r"(?:^|[;&|]\s*)init\s+0\b",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bchmod\s+-R\s+777\s+/(?:\s|$)",
    r"\bchown\s+-R\s+[^\s]+\s+/(?:\s|$)",
    r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|fish)\b",
    r"(?:^|[;&|]\s*)(?:sudo|doas|pkexec|runuser|setpriv)\b",
    r"(?:^|[;&|]\s*)su(?:\s|$)",
    r"(?:^|[;&|]\s*)(?:chroot|unshare|nsenter|pivot_root|mount|umount|"
    r"swapon|swapoff)\b",
    r"\b(?:shred|truncate)\b[^\n]*\b/dev/",
)

_NETWORK_COMMANDS = {
    "curl",
    "wget",
    "ssh",
    "scp",
    "sftp",
    "nc",
    "ncat",
    "socat",
    "telnet",
    "ftp",
    "ping",
    "dig",
    "nslookup",
    "host",
    "traceroute",
    "aria2c",
}

_REDIRECT_PREFIXES = (">>", "2>>", "&>>", ">", "<", "2>", "&>")
_SAFE_DEVICE_PATHS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero"}

# Shell operators that start a new command.  shlex is asked for punctuation
# tokens so ``echo a&&curl b`` is split correctly even without spaces.
_SEGMENT_SEPARATORS = {
    ";",
    ";;",
    ";&",
    ";;&",
    "&&",
    "||",
    "|",
    "|&",
    "&",
    "(",
    ")",
    "{",
    "}",
}
_COMMAND_WRAPPERS = {
    "!",
    "env",
    "command",
    "exec",
    "nohup",
    "time",
    "builtin",
    "timeout",
    "nice",
    "ionice",
    "stdbuf",
    "xargs",
    "busybox",
    "toybox",
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "while",
    "until",
    "do",
    "done",
    "esac",
    "function",
}
_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_COMMANDS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "ash", "mksh"}
_MAX_NESTED_SHELL_DEPTH = 4
# Wrapper options that consume the following token.  Without this, a command
# such as ``timeout -s KILL 5 curl ...`` would hide ``curl`` behind the
# signal name and bypass the network policy.
_WRAPPER_ARG_OPTIONS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "exec": {"-a"},
    "time": {"-f", "--format", "-o", "--output"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {
        "-c",
        "--class",
        "-n",
        "--classdata",
        "-p",
        "--pid",
        "-P",
        "--pgid",
        "-u",
        "--uid",
    },
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "xargs": {
        "-a",
        "--arg-file",
        "-d",
        "--delimiter",
        "-E",
        "--eof",
        "-I",
        "--replace",
        "-L",
        "--max-lines",
        "-n",
        "--max-args",
        "-P",
        "--max-procs",
        "-s",
        "--max-chars",
    },
}
_DURATION_PATTERN = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_CRITICAL_ROOTS = {
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib64",
    "/opt",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/srv",
    "/sys",
    "/tmp",
    "/usr",
    "/var",
    "/var/tmp",
}


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """Configuration for the safety guard."""

    enabled: bool = True
    allowed_roots: tuple[Path, ...] = ()
    allow_outside_roots: bool = False
    deny_sensitive_paths: bool = True
    shell_mode: ShellMode = "deny_dangerous"
    shell_allowlist: tuple[str, ...] = ()
    shell_denylist: tuple[str, ...] = ()
    network_enabled: bool = True
    require_approval: bool = True
    approval_tools: tuple[str, ...] = ()
    auto_approve: bool = False
    redact_secrets: bool = True
    audit_path: Path | None = None
    max_audit_preview: int = 2000

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_roots",
            tuple(_normalise_path(p) for p in self.allowed_roots),
        )
        object.__setattr__(
            self,
            "shell_allowlist",
            tuple(str(item) for item in self.shell_allowlist),
        )
        object.__setattr__(
            self,
            "shell_denylist",
            tuple(str(item) for item in self.shell_denylist),
        )
        object.__setattr__(
            self,
            "approval_tools",
            tuple(str(item) for item in self.approval_tools),
        )
        if self.audit_path is not None:
            object.__setattr__(self, "audit_path", _normalise_path(self.audit_path))
        if self.shell_mode not in {"deny_dangerous", "allowlist", "unrestricted"}:
            raise ValueError(
                "shell_mode must be 'deny_dangerous', 'allowlist', or 'unrestricted'"
            )
        # Compile custom patterns early so a bad policy fails at startup.
        for pattern in self.shell_denylist:
            re.compile(pattern)

    @classmethod
    def from_settings(
        cls,
        settings: Any | None = None,
        *,
        cwd: Path | None = None,
        allowed_paths: Iterable[str | Path] = (),
        audit_path: Path | None = None,
        auto_approve: bool | None = None,
        enabled: bool | None = None,
        shell_mode: ShellMode | None = None,
    ) -> SafetyPolicy:
        """Build a policy from application settings plus CLI overrides."""

        if settings is None:
            from corvustui.config.settings import get_settings

            settings = get_settings()

        roots: list[Path] = [cwd or Path.cwd()]
        roots.extend(settings.safety_allowed_paths)
        roots.extend(Path(p) for p in allowed_paths)

        configured_audit = (
            Path(audit_path)
            if audit_path is not None
            else settings.safety_audit_path
        )
        if configured_audit is None:
            configured_audit = Path(settings.data_dir) / "audit.jsonl"

        return cls(
            enabled=settings.safety_enabled if enabled is None else enabled,
            allowed_roots=tuple(roots),
            allow_outside_roots=settings.safety_allow_outside_paths,
            deny_sensitive_paths=settings.safety_deny_sensitive_paths,
            shell_mode=shell_mode or settings.safety_shell_mode,  # type: ignore[arg-type]
            shell_allowlist=tuple(settings.safety_shell_allowlist),
            shell_denylist=tuple(settings.safety_shell_denylist),
            network_enabled=settings.safety_network_enabled,
            require_approval=settings.safety_require_approval,
            approval_tools=tuple(settings.safety_approval_tools),
            auto_approve=(
                settings.safety_auto_approve if auto_approve is None else auto_approve
            ),
            redact_secrets=settings.safety_redact_secrets,
            audit_path=configured_audit,
            max_audit_preview=settings.safety_audit_preview_chars,
        )

    # -- filesystem ---------------------------------------------------------

    def path_decision(
        self,
        raw_path: str | Path,
        *,
        base: Path | None = None,
    ) -> tuple[bool, str, str]:
        """Return (allowed, reason, rule) for a filesystem path."""

        if not self.enabled:
            return True, "", "disabled"

        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (base or Path.cwd()) / candidate
        resolved = _normalise_path(candidate)

        if self.deny_sensitive_paths and is_sensitive_path(resolved):
            return False, f"Path is protected by the safety policy: {resolved}", "sensitive_path"

        if self.allow_outside_roots:
            return True, "", "outside_roots_allowed"

        roots = self.allowed_roots or (Path.cwd(),)
        for root in roots:
            if resolved == root or resolved.is_relative_to(root):
                return True, "", "inside_allowed_root"

        allowed = ", ".join(str(root) for root in roots)
        return (
            False,
            f"Path is outside the allowed roots ({allowed}): {resolved}",
            "outside_allowed_root",
        )

    # -- shell --------------------------------------------------------------

    def shell_decision(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        base: Path | None = None,
    ) -> tuple[bool, str, str]:
        """Return (allowed, reason, rule) for a shell command."""

        if not self.enabled:
            return True, "", "disabled"

        if cwd is not None:
            allowed, reason, rule = self.path_decision(cwd, base=base)
            if not allowed:
                return False, f"Shell working directory rejected: {reason}", rule

        if self.shell_mode == "unrestricted":
            return True, "", "shell_unrestricted"

        stripped = command.strip()
        if not stripped:
            return True, "", "empty"

        patterns = list(DEFAULT_SHELL_DENY_PATTERNS)
        patterns.extend(self.shell_denylist)
        for pattern in patterns:
            if re.search(pattern, stripped, re.IGNORECASE | re.MULTILINE):
                return (
                    False,
                    f"Shell command matches a blocked pattern: {pattern}",
                    "shell_denylist",
                )

        try:
            segments = _expand_command(stripped)
        except ValueError as exc:
            return False, f"Shell command could not be parsed safely: {exc}", "shell_parse"

        if self.shell_mode == "allowlist":
            if not self.shell_allowlist:
                return (
                    False,
                    "Shell allowlist mode is active but no commands are allowlisted.",
                    "shell_allowlist_empty",
                )
            if "$(" in stripped or "`" in stripped:
                return (
                    False,
                    "Shell command substitution is not allowed in allowlist mode.",
                    "shell_substitution",
                )
            allowlist = set(self.shell_allowlist)
            for segment in segments:
                command_name = _command_name(segment)
                if command_name is None:
                    continue
                if command_name not in allowlist:
                    return (
                        False,
                        f"Shell command '{command_name}' is not in the allowlist.",
                        "shell_not_allowlisted",
                    )

        if not self.network_enabled:
            for segment in segments:
                network_reason = _network_command_reason(segment)
                if network_reason:
                    return (
                        False,
                        f"Network access is disabled, but the command uses "
                        f"{network_reason}.",
                        "network_disabled",
                    )

        for segment in segments:
            destructive = _destructive_rm_reason(
                segment,
                protected=(base or Path.cwd(), *self.allowed_roots),
            )
            if destructive:
                return (
                    False,
                    f"Recursive removal of '{destructive}' is blocked by the "
                    "safety policy.",
                    "shell_denylist",
                )

            command_name = _command_name(segment)
            command_index = _command_index(segment)
            if command_name in {"cd", "pushd", "popd"} and command_index is not None:
                args = segment[command_index + 1:]
                target, error_rule = _directory_change_target(args)
                if command_name == "popd":
                    return (
                        False,
                        "Shell directory stack changes are not allowed.",
                        "shell_cd_unknown",
                    )
                if error_rule:
                    return (
                        False,
                        "Shell directory change to an unknown previous "
                        "directory is not allowed.",
                        error_rule,
                    )
                if target is None:
                    if command_name == "pushd":
                        return (
                            False,
                            "Shell directory stack changes without an explicit "
                            "path are not allowed.",
                            "shell_cd_unknown",
                        )
                    target = str(Path.home())
                expanded = os.path.expandvars(target)
                if "$" in expanded:
                    return (
                        False,
                        f"Shell directory change uses an unexpanded variable: "
                        f"{target}",
                        "shell_variable_path",
                    )
                allowed, reason, rule = self.path_decision(expanded, base=base)
                if not allowed:
                    return False, f"Shell directory change rejected: {reason}", rule

            paths = _path_tokens(segment)
            if command_index is not None:
                command_token = segment[command_index]
                if _looks_like_path(command_token):
                    paths.append(command_token)
            for token in paths:
                expanded = os.path.expandvars(token)
                if "$" in expanded:
                    return (
                        False,
                        f"Shell path uses an unexpanded variable: {token}",
                        "shell_variable_path",
                    )
                allowed, reason, rule = self.path_decision(expanded, base=base)
                if not allowed:
                    return False, f"Shell path rejected: {reason}", rule

        return True, "", "shell_allowed"


def is_sensitive_path(path: Path) -> bool:
    """Return True for credential files and protected system locations."""

    resolved = _normalise_path(path)
    lowered = resolved.name.lower()

    if _ENV_FILE.match(lowered):
        return True
    if lowered in _SENSITIVE_NAMES:
        return True
    if lowered.endswith(_SENSITIVE_SUFFIXES):
        return True

    for raw in _SENSITIVE_DIRS:
        directory = _normalise_path(Path(raw).expanduser())
        if resolved == directory or resolved.is_relative_to(directory):
            return True

    for raw in _SENSITIVE_FILES:
        if resolved == Path(raw):
            return True

    return False


def _normalise_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def _directory_change_target(args: list[str]) -> tuple[str | None, str | None]:
    """Return the directory argument, skipping shell builtin options."""

    position = 0
    while (
        position < len(args)
        and args[position].startswith("-")
        and args[position] != "-"
    ):
        if args[position] == "--":
            position += 1
            break
        position += 1

    if position >= len(args):
        return None, None
    if args[position] == "-":
        return None, "shell_cd_unknown"
    return args[position], None


def _shell_tokens(command: str) -> list[str]:
    """Tokenise a shell command, including operators and unquoted newlines."""

    normalised = _split_unquoted_newlines(command)
    lexer = shlex.shlex(normalised, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # Do not treat ``#`` as a comment: a model may pass it inside a command
    # and the safety decision should see every token.
    lexer.commenters = ""
    return list(lexer)


def _split_unquoted_newlines(command: str) -> str:
    """Turn unquoted newlines into ``;`` so multi-line commands are split."""

    result: list[str] = []
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            result.append(char)
            escaped = True
            continue
        if quote is not None:
            result.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(char)
        elif char in {"\n", "\r"}:
            result.append(" ; ")
        else:
            result.append(char)
    return "".join(result)


def _command_segments(tokens: list[str]) -> list[list[str]]:
    """Split tokens on shell control operators."""

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _expand_command(command: str, depth: int = 0) -> list[list[str]]:
    """Tokenise a command and expand nested shells and substitutions."""

    tokens = _shell_tokens(command)
    if not tokens:
        return []

    segments = _expand_nested_segments(_command_segments(tokens), depth)
    if depth < _MAX_NESTED_SHELL_DEPTH:
        for substitution in _command_substitutions(command):
            segments.extend(_expand_command(substitution, depth + 1))
    return segments


def _expand_nested_segments(
    segments: list[list[str]],
    depth: int = 0,
) -> list[list[str]]:
    """Expand commands hidden in ``sh -c``, ``eval``, and ``find -exec``."""

    expanded: list[list[str]] = []
    for segment in segments:
        expanded.append(segment)
        if depth >= _MAX_NESTED_SHELL_DEPTH:
            continue

        for subcommand in _exec_subcommands(segment):
            expanded.extend(_expand_nested_segments([subcommand], depth + 1))

        for command in _env_split_commands(segment):
            expanded.extend(_expand_command(command, depth + 1))

        for command in _nested_shell_commands(segment):
            expanded.extend(_expand_command(command, depth + 1))
    return expanded


def _command_substitutions(command: str) -> list[str]:
    """Return commands inside ``$(...)`` and backticks.

    The raw string is scanned because shlex treats a quoted substitution as a
    single word, which would otherwise hide ``"$(curl ...)"`` from the policy.
    """

    found: list[str] = []
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\":
            index += 2
            continue
        if command.startswith("$(", index):
            end = _matching_paren(command, index + 1)
            if end is None:
                index += 2
                continue
            inner = command[index + 2:end]
            # ``$((...))`` is arithmetic, not a command substitution.
            if inner.strip() and not inner.lstrip().startswith("("):
                found.append(inner)
            index = end + 1
            continue
        if char == "`":
            end = index + 1
            while end < len(command):
                if command[end] == "\\":
                    end += 2
                    continue
                if command[end] == "`":
                    break
                end += 1
            if end >= len(command):
                index += 1
                continue
            inner = command[index + 1:end]
            if inner.strip():
                found.append(inner)
            index = end + 1
            continue
        index += 1
    return found


def _matching_paren(command: str, start: int) -> int | None:
    """Return the index of the ``)`` matching the ``(`` at ``start``."""

    depth = 0
    quote: str | None = None
    index = start
    while index < len(command):
        char = command[index]
        if char == "\\":
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _exec_subcommands(tokens: list[str]) -> list[list[str]]:
    """Return commands passed to ``find -exec`` / ``-ok``."""

    index = _command_index(tokens)
    if index is None or Path(tokens[index]).name != "find":
        return []

    args = tokens[index + 1:]
    subcommands: list[list[str]] = []
    position = 0
    while position < len(args):
        if args[position] in {"-exec", "-execdir", "-ok", "-okdir"}:
            end = position + 1
            subcommand: list[str] = []
            while end < len(args) and args[end] not in {"+", ";"}:
                subcommand.append(args[end])
                end += 1
            if subcommand:
                subcommands.append(subcommand)
            position = end + 1
        else:
            position += 1
    return subcommands


def _env_split_commands(tokens: list[str]) -> list[str]:
    """Return commands packed into ``env -S '...'``."""

    index = 0
    while index < len(tokens) and _ASSIGNMENT_PATTERN.match(tokens[index]):
        index += 1
    if index >= len(tokens) or Path(tokens[index]).name != "env":
        return []

    args = tokens[index + 1:]
    for position, arg in enumerate(args):
        if arg in {"-S", "--split-string"}:
            if position + 1 < len(args):
                return [args[position + 1]]
            return []
        if arg.startswith("--split-string="):
            return [arg.split("=", 1)[1]]
        if arg.startswith("-") and not arg.startswith("--"):
            cluster = arg[1:]
            marker = cluster.find("S")
            if marker != -1 and all(ch in "i0vS" for ch in cluster[:marker]):
                value = cluster[marker + 1:]
                if value:
                    return [value]
                if position + 1 < len(args):
                    return [args[position + 1]]
                return []
    return []


def _nested_shell_commands(tokens: list[str]) -> list[str]:
    """Return command strings passed to a shell's ``-c`` or to ``eval``."""

    index = _command_index(tokens)
    if index is None:
        return []

    name = Path(tokens[index]).name
    args = tokens[index + 1:]
    if name == "eval":
        return [" ".join(args)] if args else []
    if name not in _SHELL_COMMANDS:
        return []

    for position, arg in enumerate(args):
        if arg == "--":
            break
        if arg == "-c" or (
            arg.startswith("-")
            and not arg.startswith("--")
            and "c" in arg[1:]
        ):
            if position + 1 < len(args):
                return [args[position + 1]]
            return []
    return []


def _destructive_rm_reason(
    tokens: list[str],
    protected: Iterable[Path] = (),
) -> str | None:
    """Return the target when ``rm -r`` targets a whole tree."""

    index = _command_index(tokens)
    if index is None or Path(tokens[index]).name != "rm":
        return None

    recursive = False
    end_of_options = False
    targets: list[str] = []
    for arg in tokens[index + 1:]:
        if not end_of_options and arg == "--":
            end_of_options = True
            continue
        if not end_of_options and arg.startswith("-") and arg != "-":
            if arg.startswith("--"):
                if arg == "--recursive":
                    recursive = True
            elif "r" in arg[1:] or "R" in arg[1:]:
                recursive = True
            continue
        targets.append(arg)

    if not recursive:
        return None

    roots = tuple(_normalise_path(path) for path in protected)
    home = _normalise_path(Path.home())
    wildcards = {
        ".",
        "..",
        "*",
        "**",
        "/*",
        "../*",
        "{}",
        str(home),
        f"{home}/*",
    }

    if not targets:
        # ``find . | xargs rm -rf`` receives its targets on stdin.
        for token in tokens[:index]:
            if Path(token).name in {"xargs", "parallel"}:
                return "<input>"
        return None

    for target in targets:
        expanded = os.path.expanduser(os.path.expandvars(target))
        pwd = str(protected[0]) if protected else os.getcwd()
        env_pwd = os.environ.get("PWD")
        if env_pwd and ("$PWD" in target or "${PWD}" in target):
            # The guard's base is authoritative even when the process PWD
            # points somewhere else (as in a test harness).
            expanded = expanded.replace(env_pwd, pwd)
        if target in {"$OLDPWD", "${OLDPWD}"}:
            return target
        if "$" in expanded or "`" in expanded:
            # An unexpanded variable or command substitution cannot be
            # checked statically; recursive removal must not proceed.
            return target
        normalised = os.path.normpath(expanded)
        if normalised in wildcards:
            return target

        candidate = Path(normalised)
        if not candidate.is_absolute():
            candidate = (roots[0] if roots else Path.cwd()) / candidate
        candidate = _normalise_path(candidate)

        if str(candidate) in _CRITICAL_ROOTS or candidate == home:
            return target
        for root in roots:
            if root.is_relative_to(candidate):
                return target
            if str(candidate) in {f"{root}/*", f"{root}/**"}:
                return target
    return None


def _command_index(tokens: list[str]) -> int | None:
    """Return the index of the command word, skipping wrappers/assignments."""

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _ASSIGNMENT_PATTERN.match(token):
            index += 1
            continue

        name = Path(token).name
        if (
            name == "command"
            and index + 1 < len(tokens)
            and tokens[index + 1] in {"-v", "-V"}
        ):
            # ``command -v curl`` only checks availability; it does not run
            # curl and should not be treated as network access.
            return None
        if name in _COMMAND_WRAPPERS:
            index += 1
            arg_options = _WRAPPER_ARG_OPTIONS.get(name, set())
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if token in arg_options:
                    index += 2
                    continue
                if token.startswith("-") and token != "-":
                    index += 1
                    continue
                if (
                    name in {"timeout", "nice", "ionice"}
                    and _DURATION_PATTERN.fullmatch(token)
                ):
                    index += 1
                    continue
                break
            continue
        return index
    return None


def _command_name(tokens: list[str]) -> str | None:
    index = _command_index(tokens)
    if index is None:
        return None
    return Path(tokens[index]).name


def _network_command_reason(tokens: list[str]) -> str | None:
    index = _command_index(tokens)
    if index is None:
        return None

    name = Path(tokens[index]).name
    args = tokens[index + 1:]
    if name in _NETWORK_COMMANDS:
        return name
    if name == "git" and args and args[0] in {"clone", "fetch", "pull", "push"}:
        return f"git {args[0]}"
    if name in {"pip", "pip3"} and args and args[0] in {
        "install",
        "download",
    }:
        return f"pip {args[0]}"
    if name in {"npm", "pnpm", "yarn"} and args and args[0] in {
        "install",
        "add",
        "update",
    }:
        return f"{name} {args[0]}"
    return None


def _path_tokens(tokens: list[str]) -> list[str]:
    """Extract path-like arguments, including redirections and assignments."""

    paths: list[str] = []
    expect_path = False

    for token in tokens[1:]:
        if expect_path:
            paths.append(token)
            expect_path = False
            continue

        if token in {"<<", "<<-", "<<<"}:
            continue

        if token in _REDIRECT_PREFIXES:
            expect_path = True
            continue

        for prefix in _REDIRECT_PREFIXES:
            if token.startswith(prefix) and len(token) > len(prefix):
                paths.append(token[len(prefix):])
                break
        else:
            if "=" in token and not token.startswith("-"):
                key, value = token.split("=", 1)
                if key and value and not key.startswith("--"):
                    paths.append(value)
                    continue

            if token.startswith("--") and "=" in token:
                value = token.split("=", 1)[1]
                if _looks_like_path(value):
                    paths.append(value)
                continue

            # Options can carry a path directly, e.g. ``wget -O/tmp/x`` or
            # ``gcc -I./include``.  Extract the path portion instead of
            # ignoring the whole token as a flag.
            if token.startswith("-") and not token.startswith("--"):
                slash = token.find("/")
                if slash > 1:
                    candidate = token[slash:]
                    if _looks_like_path(candidate):
                        paths.append(candidate)
                        continue

            if _looks_like_path(token):
                paths.append(token)
                continue

            # Catch embedded paths such as python -c "open('/etc/passwd')".
            for match in re.finditer(r"(?<![\w:/])(?:~|/)[^\s\"'`,;|&<>)]+", token):
                candidate = match.group(0).rstrip(".,;:")
                if "://" in token:
                    continue
                if _looks_like_path(candidate):
                    paths.append(candidate)

    return [path for path in paths if not _is_safe_device_path(path)]


def _looks_like_path(token: str) -> bool:
    if not token or token == "-" or token in _SAFE_DEVICE_PATHS:
        return False
    if "://" in token:
        return False
    if token in {".", "..", "~"}:
        return True
    if re.fullmatch(r"\$(?:\{?[A-Za-z_][A-Za-z0-9_]*\}?)", token):
        return True
    if token.startswith(("/", "~/", "./", "../", "$HOME/", "${HOME}/")):
        return True
    if token.startswith("~") and len(token) > 1:
        return True
    # Relative paths with a separator.  Code fragments such as
    # ``open('/etc/passwd')`` are handled by the embedded-path scan instead.
    if any(char in token for char in "(){}[]\"'`"):
        return False
    return "/" in token and not token.startswith("-")


def _is_safe_device_path(token: str) -> bool:
    if token in _SAFE_DEVICE_PATHS:
        return True
    if token.startswith("/dev/fd/") or token.startswith("/proc/self/fd/"):
        return True
    return False
