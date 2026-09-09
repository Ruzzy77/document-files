# Security

Document Files processes untrusted documents. Parsers, OCR and model runtimes are
not security sandboxes. Keep original files immutable; run unfamiliar inputs under
an unprivileged account or the isolated container profile. Model output is data,
not authority to execute code, access arbitrary files or change server routing.

## Reporting

Use the repository's **Security → Report a vulnerability** private channel when
available. If it is unavailable, open an issue asking for a private contact without
including an exploit, document contents, credentials or personal data. No private
contact address, response SLA or maintained historical release line is promised.
Include affected product and pack versions, platform, a minimal public/synthetic
reproducer and the impact. Never upload a customer's original document by default.

## Trust boundaries

- Pack import requires an independently obtained SHA256. Import verifies every
  regular file, paths, archive limits, source metadata, licenses and exact inventory.
  Symlinks, device members, traversal, duplicate/case-colliding names and existing
  version overwrites are rejected. A checksum establishes identity, **not trust**.
- Trust the publisher before installing executable packs. Verify the applicable
  GitHub attestation repository, workflow and commit, not merely that a signature
  exists. Verification-only attestations are not upstream build provenance.
- Native libraries and model weights have separate licenses and vulnerabilities.
  Do not substitute an old Torch/ORT on Intel Mac silently. The full recognition
  profile there uses a local Linux CPU container instead.
- Processing never acquires dependencies or models. Explicit cloud profiles alone
  authorize their configured server; there is no cloud fallback. Offline flags
  are not an OS firewall. Use the internal-network container for an egress boundary.
- Local llama.cpp uses an unpredictable token file in private storage, loopback,
  one CPU slot and no agent tools. The token is not included in endpoint repr,
  command arguments or application logs. It is not protection from another process
  with the same account privileges. Do not expose its raw server publicly.
- HTTP accepts uploaded bytes and administrator-defined profile IDs, not source
  URLs, server paths or caller-selected inference endpoints. Use TLS and an
  authenticated reverse proxy before deliberate remote exposure. The supplied
  Compose profile publishes only to the local host.
- Keep state and token files owner-only. Read-only pack mounts protect installed
  assets from workers. Results are retained unless explicitly deleted or an
  administrator has configured expiry. Rollback does not delete or reanalyse them.

## Release safety evidence

Before enterprise supply, review the exact dependency/component SBOM and license
notices, upstream security advisories, unresolved exceptions, source and model
provenance, platform installation evidence, and actual quality results. File-level
SBOM output alone is not a complete transitive dependency/vulnerability inventory.
Record decisions against the exact pack digests. A candidate build or passing mock
transport test does not demonstrate secure model inference, correctness or support.

There is no claim that version 1.8.0 or its optional packs are free of known
vulnerabilities, penetration-tested or covered by a security maintenance SLA.
