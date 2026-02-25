# Obsidian Update

Date: 2026-02-25

## Resiliency hardening applied

- Lock safety for reconciliation paths (token ownership, safe release/renew).
- Overlay convergence now runs every reconciliation cycle; reservation repair remains periodic.
- Declare-state now supports explicit lab scope (`declared_labs`) across API -> agent.
- Agent orphan cleanup is scoped and conservative for untracked VXLAN ports.
- Deferred node migration cleanup queue added and integrated into reconciliation/agent registration.
- Migration cleanup now reclaims stale `running` claims after worker interruption.
- State enforcement skip metrics now include reason labels.
- Post-operation lifecycle cleanup now triggers immediate per-lab link reconciliation.
- Hot-disconnect link parsing now fails closed on ambiguous/unresolvable IDs.
- OVS stale-port detection now has a host-side fallback when container PID is unavailable.

## Validation snapshot

- Agent targeted tests: pass
- API targeted tests (containerized): pass
- Lint (`ruff check`) on changed files: pass
