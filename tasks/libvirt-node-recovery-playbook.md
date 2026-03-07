# Libvirt Node Recovery Playbook

## Goal
Recover a libvirt-backed node that is present or starting in the controller but missing runtime identity, OVS-facing interfaces, or healthy links.

## Preconditions
- The controller API is reachable.
- The target agent is online.
- The node has a `Node` definition and, ideally, a `NodePlacement`.

## Recovery Sequence

1. Inspect the node diagnostic bundle.
   - `GET /labs/{lab_id}/nodes/{node_id}/interface-diagnostics`
   - Confirm:
     - controller `actual_state` / `is_ready`
     - placement host / stored `runtime_id`
     - agent `/status` identity (`node_definition_id`, `runtime_id`)
     - live port-state (`ovs_port_name`, `vlan_tag`)
     - stored `InterfaceMapping`
     - affected `LinkState` rows

2. If runtime identity is missing but the VM exists, backfill metadata first.
   - `POST /runtime-identity-backfill?dry_run=true`
   - `POST /runtime-identity-backfill?dry_run=false`

3. Refresh only the target node's interface mappings.
   - `POST /labs/{lab_id}/nodes/{node_id}/interface-mappings/sync`

4. If the VM is running but the expected data interface still has no OVS port:
   - perform a targeted stop/start or redeploy of that node
   - do not bounce unrelated nodes

5. Re-run the node diagnostic bundle.
   - Verify the agent now reports:
     - matching `node_definition_id`
     - non-empty `runtime_id`
     - live `ovs_port_name` on expected data interfaces

6. Reconcile links after the node is healthy.
   - first refresh interface mappings if needed
   - then run the targeted or lab-level link reconciliation flow
   - for cross-host links, ensure overlay convergence runs before final link verification

## Decision Hints

- If agent status is missing the node entirely:
  - treat this as provisioning/runtime failure, not just a link issue.

- If agent status shows the node but `runtime_id` or `node_definition_id` is missing:
  - treat this as runtime identity failure.

- If identity is present but `eth1` has no live OVS port:
  - treat this as VM interface attachment failure.

- If live port-state is correct but the link is still down/error:
  - treat this as interface-mapping or link reconciliation drift.

## Do Not

- Do not restart the whole lab to fix one libvirt node.
- Do not trust deterministic domain-name lookup alone.
- Do not accept provisioning success until agent status exposes identity fields.
