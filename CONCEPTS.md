# Concepts

Shared domain vocabulary for this project — entities, named processes, and status
concepts with project-specific meaning. Seeded with core domain vocabulary, then
accretes as ce-compound and ce-compound-refresh process learnings; direct edits are
fine. Glossary only, not a spec or catch-all.

## Capability Verification

### Capability
A declared promise about what the project can do, written as a given/when/then
contract and proven by a real check rather than trusted from a report or an exit code.

### Ledger
The committed record of each Capability's most recent proof: its state, when it was
last proven, and the evidence supporting that state (a failure snippet, a waiver
reason, the fingerprints of the files it depends on).

### Gate
The enforcement mechanism that blocks a turn — or a CI run — when a Capability is not
proven and fresh.

### Freshness (Tier)
How long a Capability's proof stays valid. A cheap-tier Capability's proof stays valid
until its underlying code changes, checked via a fingerprint. A live-tier Capability's
proof expires after a fixed time window regardless of code changes, since its truth can
change with no code change at all — a revoked permission, a deleted external resource.

### Waiver
A time-boxed, reasoned acknowledgment that a Capability can't be proven right now — an
explicit, recorded exception in the Ledger, distinct from a Capability that has simply
never been proven.
