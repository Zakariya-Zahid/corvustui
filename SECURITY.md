# Security Policy

CorvusTUI runs local tools, parses user files, talks to model providers, and can
be extended with MCP servers. Treat all of those as security-sensitive.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |
| < 0.1   | No        |

Security fixes land on the latest release and, when practical, on `main`.

## Reporting a vulnerability

**Do not open a public issue, discussion, or PR for a security problem.**

Use GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include the details below.

If private reporting is unavailable, contact the maintainer through the address
on the GitHub profile and say that you are reporting a security issue.

### What to include

- Affected version or commit.
- A description of the issue and its impact.
- Reproduction steps or a minimal proof of concept.
- The provider, model, OS, and terminal, if relevant.
- Any suggested fix or mitigation.
- Whether you plan to publish the finding, and your preferred timeline.

Never include real API keys, tokens, or personal data in the report. Use fake
values; if a real secret was exposed, rotate it first.

### What to expect

- Acknowledgement within 5 working days.
- A triage decision and severity assessment within 10 working days.
- A fix or mitigation plan for confirmed issues, with credit if you want it.
- A coordinated disclosure date. We aim for 90 days or sooner after a fix.

## Scope

In scope:

- Safety-policy bypasses (filesystem scope, shell policy, network policy,
  approvals, redaction, audit logging).
- Path traversal, symlink escapes, and unsafe file handling.
- Command injection through tools, MCP servers, or attachment metadata.
- Prompt injection that causes a tool to run without approval or exfiltrates
  data.
- Secret leakage in logs, exports, error messages, or debug bundles.
- Provider request smuggling, SSRF, or unsafe redirect handling.
- Dependency vulnerabilities with a reachable path.
- Sandbox or parser escapes in document, image, audio, or video handling.

Out of scope:

- A model producing incorrect, biased, or unsafe text. That is a model and
  prompt-design issue, not a CorvusTUI vulnerability, unless it bypasses a
  safety control.
- Attacks that require an already-compromised local account or root.
- Missing hardening with no demonstrated impact.
- Rate limits or costs on a provider account you control.
- Social engineering of maintainers.

## Safe harbor

We will not pursue legal action for good-faith research that follows this
policy, avoids privacy violations and service disruption, and gives us a
reasonable chance to fix the issue before disclosure. Do not access data that
is not yours, do not run destructive tools against systems you do not own, and
do not test against a shared production deployment.

## Security design notes

- **Fail closed.** If an approval cannot be shown, the action is denied.
- **Least privilege.** Tools receive the narrowest scope; destructive actions
  require approval.
- **Untrusted content.** Model output, MCP resources, documents, and tool
  results are data, not instructions. Instructions inside them are ignored.
- **Bounded input.** Attachments and parsers are limited by size, time, and
  recursion depth.
- **Redaction.** Logs, exports, and debug bundles must not contain keys, tokens,
  or full document text by default.
- **Local first.** Cloud uploads require an explicit choice and show what will
  be sent.

See [ROADMAP.md](ROADMAP.md) for the security work planned through v1.0.
