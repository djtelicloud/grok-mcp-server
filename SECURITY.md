# Security Policy

## Supported Releases

Security fixes are applied to the latest `0.6.x` release line. Users should
upgrade to the newest patch release before reporting an issue.

The stable Core gateway is supported under the deployment boundary below.
Contributor-only Forge/Swarm features are beta and require an explicitly
trusted workspace; they do not carry the same remote/multi-user guarantee.

## Reporting a Vulnerability

Please use GitHub's private vulnerability reporting or a private Security
Advisory for this repository. Do not open a public issue containing exploit
details, API keys, bearer tokens, Grok CLI credentials, private prompts, or
runtime logs with user data.

Include the affected version or commit, deployment mode, reproduction steps,
impact, and any suggested mitigation. If private reporting is unavailable,
contact the repository maintainer privately and wait for a coordinated fix
before public disclosure.

The project aims to acknowledge complete reports within three business days
and provide an initial severity/status assessment within seven. Remediation
and disclosure timing depend on impact and provider coordination. When a
release contains a qualifying vulnerability fix, the maintainer will publish
a GitHub Security Advisory and request a CVE when appropriate.

## Deployment Boundary

UniGrok binds Docker Compose to `127.0.0.1` by default. Before binding to a LAN
or public interface, configure `UNIGROK_API_KEYS`, terminate TLS at a trusted
proxy, restrict allowed origins, and rotate any credential that may have been
exposed. Keep local git mutation and container restart capabilities disabled
unless the process is running in a trusted local environment.

See [docs/threat-model.md](docs/threat-model.md) for actors, assets, identity
composition, credential flows, and residual risks.
