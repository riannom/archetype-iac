# Libvirt Provisioning Follow-Up Task List

## Goal
Get libvirt node provisioning to the point where initial deployment is trustworthy, and reconciliation only handles genuine drift or abnormal fallout.

## Why This Exists
During live recovery we hit multiple cases where provisioning, status, and link attachment disagreed:

- Per-node deploy could report success while hardened status did not report the VM.
- A libvirt domain could be found by deterministic name and considered "already running" while remaining invisible to metadata-based status.
- A VM could be `running`/`ready` in controller state while required OVS-facing interfaces were not resolvable.
- Interface mapping refresh did not reliably repair stale VM OVS port mappings.
- Recovery currently depends too much on reconciliation, backfill, and manual stop/start cycles.

## Current Outstanding Operational Issues

- [x] Recover `cisco_n9kv_4` cleanly
  - Current state:
    - completed
    - clean stop/start restored `cisco_n9kv_4:eth1`
    - link `ceos_5:eth4-cisco_n9kv_4:eth1` returned to `up`
  - Desired end state:
    - satisfied

- [x] Decide whether to recover or leave intentionally undeployed:
  - `cat9000v_uadp_8`
  - `cisco_iosxr_7`
  - Current state:
    - both are `desired_state=stopped`
    - both are `undeployed`
    - both have `Image not available on host`
  - These are not runtime-identity issues. They are image/runtime availability issues.
  - Decision:
    - leave both intentionally undeployed until the required images are available on their target hosts
  - Reason:
    - recovering them now would be image distribution work, not libvirt provisioning hardening

## Provisioning Reliability Tasks

- [x] Make libvirt create/start success depend on metadata-visible status
  - Problem:
    - create/start path can succeed because the domain exists by name
    - hardened status can still omit the VM because metadata is missing/unreadable
  - Required change:
    - after create/start, verify the node via the same metadata-based status/discovery path used by reconciliation
    - fail the operation if the VM cannot be rediscovered by metadata
  - Completed:
    - `create_node()` and `start_node()` now reject `already_running`/successful outcomes unless the domain is visible through metadata-backed status
    - provisioning no longer treats deterministic-name lookup as sufficient proof of success

- [x] Make libvirt create/start success depend on interface readiness for required data interfaces
  - Problem:
    - nodes can be marked `running`/`ready` while OVS-facing interfaces are not resolvable
  - Required change:
    - define readiness criteria for VM data interfaces
    - require post-start checks for expected interfaces such as `eth1`
    - do not declare success until required interfaces are resolvable, or explicitly degrade with a concrete error
  - Completed:
    - libvirt readiness now downgrades "boot complete" to a waiting state when the VM has expected data VLANs but `eth1` cannot yet resolve to an OVS port
    - the degraded readiness message is explicit and recovery-safe: `Waiting for data interface attachment`

- [x] Unify create-path identity lookup and status-path identity lookup
  - Problem:
    - create path can find a domain by deterministic runtime name
    - status path requires metadata and can disagree
  - Required change:
    - the create path should stamp metadata first and then re-resolve through the same metadata-based status codepath
    - avoid "already running" success unless metadata and runtime identity are visible through normal discovery
  - Completed:
    - libvirt create/start now re-checks running domains through the same metadata-based discovery logic used by hardened status
    - name-only presence is no longer accepted as a successful create/start outcome

- [x] Fail fast on metadata-missing libvirt domains after create/start
  - Problem:
    - missing metadata currently turns into a latent runtime-identity/status discrepancy
  - Required change:
    - if a managed libvirt domain exists but metadata is absent after start, treat that as provisioning failure
    - emit a clear audit/log event with domain name, expected `node_definition_id`, and host
  - Completed:
    - create/start now returns explicit provisioning failure when a running domain is not rediscoverable through metadata-backed status
  - Follow-up:
    - add structured audit logging for these failures instead of relying on error text alone

## Interface Mapping and OVS Resolution Tasks

- [x] Fix bulk interface mapping refresh for libvirt VMs
  - Problem:
    - lab-wide interface mapping sync uses Docker OVS plugin inventory
    - libvirt VM interfaces are not reliably refreshed there
    - stale `interface_mappings` rows can persist even when live per-interface agent probes disagree
  - Required change:
    - ensure VM interface mappings are refreshed from the agent's live per-interface/status data, not only Docker bulk OVS inventory
    - stale VM mappings must be overwritten when live data disagrees
  - Completed:
    - `populate_from_agent()` now merges provider-agnostic `port-state`
    - existing VM rows are updated from live data rather than left stale

- [x] Remove the "existing `ovs_port` means do nothing" behavior for stale VM mappings
  - Problem:
    - helper logic can skip repair because an `InterfaceMapping` row exists, even when that row is stale
  - Required change:
    - compare stored mapping against live agent-reported port details
    - update existing rows when OVS port or VLAN changed
  - Completed:
    - bulk mapping refresh now overwrites existing rows with live `port-state` data
    - broken-link mappings are no longer excluded from refresh

- [x] Make link creation use live endpoint resolution for libvirt when available
  - Problem:
    - link creation can fail on stale/missing cached port information
  - Required change:
    - before failing with `Cannot find OVS port`, query the live interface-resolution path used by the agent VLAN probe
    - keep cache-backed lookups as an optimization, not the sole source of truth
  - Completed:
    - `create_link()` now falls back to the live interface details probe before returning a missing-port error

- [x] Add a targeted "refresh one node's interface mappings" path
  - Problem:
    - current lab-wide sync is too blunt and can skip the exact VM we need to repair
  - Required change:
    - add a controller/agent path to refresh interface mappings for one node from live runtime data
    - use it in targeted recovery and post-start verification
  - Completed:
    - added node-scoped interface mapping refresh in the controller service and lab operations router
    - targeted recovery can now refresh one node's live port mappings without sweeping the whole lab

## Controller State Accuracy Tasks

- [x] Stop declaring nodes `ready` when required interfaces are absent
  - Problem:
    - controller state can say `running`/`ready` while OVS-facing interfaces are unresolved
  - Required change:
    - either gate readiness on interface checks for affected providers
    - or introduce a more precise post-boot transitional state instead of premature `ready`
  - Completed:
    - libvirt readiness now stays transitional until a required data interface is resolvable in OVS

- [x] Fix controller stale-state settlement after successful jobs
  - Problem:
    - we saw completed jobs while node/link state remained stale or contradictory
    - statement timeouts around `link_states` and related writes have contributed to bad settlement
  - Required change:
    - audit post-job state write paths for timeout sensitivity
    - ensure successful deploy/reconcile jobs cannot leave contradictory node/link state silently
  - Completed:
    - post-operation cleanup now records reconciliation/convergence failures explicitly
    - lifecycle finalization fails the job when state-settlement steps fail, instead of silently reporting success

- [x] Fix lab aggregate state accounting
  - Problem:
    - lab-level `error` can persist after node/runtime recovery
  - Required change:
    - reconcile lab aggregate state from current node/link truth
    - prevent stale failed-job residue from pinning the lab in `error`
  - Completed:
    - added lab-state recomputation from current node truth
    - successful node lifecycle finalization now clears stale job-driven lab error residue

## Runtime Identity Hardening Follow-Through

- [x] Make libvirt metadata backfill unnecessary for freshly provisioned nodes
  - Problem:
    - backfill successfully repaired `cat9000v_q200_9`, but that should have been true from first boot
  - Required change:
    - metadata stamping and metadata-read visibility must be correct on the initial provisioning path
  - Completed:
    - libvirt create/start now fails unless the running domain is rediscoverable through metadata-backed status
    - controller deploy/start now also requires the agent status round-trip to expose `node_definition_id` and `runtime_id`

- [x] Add post-start assertion that agent `/status` returns `node_definition_id` and `runtime_id`
  - Problem:
    - provisioning can currently "succeed" without proving those fields are visible to the controller path
  - Required change:
    - require a successful status round-trip before success is recorded
  - Completed:
    - deploy/start now verify agent lab status after successful create/start
    - success is only recorded when agent status exposes the expected `node_definition_id` and a non-empty `runtime_id`

- [x] Resolve remaining runtime identity drift on `cisco_n9kv_4`
  - Current signal:
    - runtime identity audit shows metadata-name/runtime mismatch drift for `cisco_n9kv_4`
  - Required change:
    - determine whether this is just stale placement/runtime ID, or a reused/replaced domain that needs explicit replacement handling
  - Completed:
    - reconciliation now adopts runtime replacement when controller node state still indicates an in-progress start
    - this closes the restart/replacement window that previously left `cisco_n9kv_4` flagged as drifted

## Testing Tasks

- [x] Add a regression test: libvirt domain exists by name but is missing metadata
  - Expectation:
    - create/start must not report success unless metadata becomes visible in status
  - Completed:
    - added create/start regressions covering `already_running` domains that are not visible through metadata-backed status

- [x] Add a regression test: libvirt node marked running but required interface cannot resolve to OVS
  - Expectation:
    - node does not become `ready`
    - operation fails or degrades with a precise error
  - Completed:
    - added readiness regression proving a VM stays non-ready with `Waiting for data interface attachment` when `eth1` cannot resolve

- [x] Add a regression test: stale `InterfaceMapping` row is overwritten by live VM port data
  - Scenario:
    - stored `vnetX` differs from live `vnetY`
  - Expectation:
    - refresh updates the row and downstream link creation uses the new value
  - Completed:
    - added service-level regression coverage for live port-state overriding stale stored VM interface mappings

- [x] Add a regression test: link creation falls back to live interface probe for libvirt endpoints
  - Expectation:
    - cached stale mapping does not cause false "Cannot find OVS port" failure if live lookup succeeds
  - Completed:
    - added agent router regression coverage for live interface-probe fallback during link creation

- [x] Add an end-to-end test: stop/start libvirt VM, then recreate same-host and cross-host links
  - Cover:
    - metadata visibility
    - runtime ID continuity/replacement
    - OVS port remap
    - link recovery without manual DB intervention
  - Completed:
    - added a controller-level recovery-flow test covering runtime replacement adoption, VM OVS-port remap refresh, and same-host plus cross-host convergence using the repaired state

## Operational Tooling Tasks

- [x] Add a targeted admin recovery playbook for libvirt nodes
  - Include:
    - metadata backfill
    - targeted stop/start
    - per-node interface mapping refresh
    - link refresh/reconcile order
  - Completed:
    - documented the focused recovery sequence in `tasks/libvirt-node-recovery-playbook.md`

- [x] Add a focused diagnostic endpoint or script for "running VM but missing OVS interface"
  - Include:
    - controller node state
    - agent `/status`
    - live interface probe
    - stored `InterfaceMapping`
    - current link detail
  - Completed:
    - added a node-scoped interface diagnostics endpoint bundling controller state, placement, agent status, live port-state, stored interface mappings, and related link rows

## Suggested Execution Order

1. Recover `cisco_n9kv_4` and restore the last live error link.
2. Fix libvirt provisioning success criteria:
   - metadata-visible status
   - interface readiness
3. Fix VM interface mapping refresh so stale OVS port rows cannot persist.
4. Make link creation use live libvirt endpoint resolution before failing.
5. Add regression coverage for the above.
6. Reassess whether reconciliation is now only handling abnormal drift rather than normal provisioning fallout.

## Success Criteria

- A freshly provisioned libvirt node is visible in hardened agent/controller status immediately.
- `runtime_id` and `node_definition_id` are present without backfill.
- Required VM interfaces resolve live in OVS before the node is declared ready.
- Link creation does not fail because of stale cached VM port mappings.
- Reconciliation is no longer needed to make normal first-time libvirt deploys become visible or connectable.
## Newly Discovered Tasks During Execution

- [x] Refresh interface mappings for broken desired-up links, not only healthy links
  - Problem:
    - the refresh path previously ignored `down/pending/error` desired-up links
    - that prevented stale VM OVS-port mappings from being corrected exactly when recovery needed them
  - Completed:
    - `refresh_interface_mappings()` now includes all desired-up links
