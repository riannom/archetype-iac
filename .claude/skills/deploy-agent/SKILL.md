---
name: deploy-agent
description: Deploy code to a remote archetype agent by pushing a git commit SHA
disable-model-invocation: true
---

# Deploy Agent

Push current or specified commit to a remote archetype agent host.

## Arguments

- `agent_id` (optional): Agent ID to deploy to. Defaults to Agent-01 (`614fc24c`).
- `sha` (optional): Git commit SHA to deploy. Defaults to current HEAD.

## Known Agents

| Name | ID | Address | Mode |
|------|----|---------|------|
| Agent-01 | `614fc24c` | `10.14.23.181:8001` | systemd |

## Steps

1. Determine the target SHA:
   - If a SHA was provided as argument, use it
   - Otherwise, run `git rev-parse HEAD` to get the current commit

2. Determine the agent ID:
   - If an agent ID was provided, use it
   - Otherwise, default to `614fc24c` (Agent-01)

3. Get the API base URL from the `.env` file (`INTERNAL_URL` value, e.g. `http://10.14.23.93:8000`)

4. Trigger the update:
   ```bash
   curl -X POST http://<api_host>:8000/agents/<agent_id>/update \
     -H "Content-Type: application/json" \
     -d '{"target_version": "<sha>"}'
   ```

5. Poll for completion (every 5 seconds, up to 2 minutes):
   ```bash
   curl http://<api_host>:8000/agents/<agent_id>/update-status
   ```

6. Report the final status to the user (success, failed, or timeout).

## Prerequisites

- `INTERNAL_URL` in `.env` must be the host IP (e.g., `http://10.14.23.93:8000`), NOT localhost
- The target commit must be pushed to the remote repository first
- Agent must be registered and reachable from the API
