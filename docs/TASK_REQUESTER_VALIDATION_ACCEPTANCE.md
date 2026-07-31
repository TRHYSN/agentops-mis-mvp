# Task Requester Validation Acceptance

## Scope

`POST /api/tasks` and `POST /api/agent-gateway/tasks` share
`create_task_api`. A supplied `requester_id` must name an existing row in
`users` before the task or any associated runtime/audit evidence is written.

An unknown requester returns:

```json
{
  "error": "requester_user_not_found",
  "message": "Task requester user does not exist.",
  "token_omitted": true
}
```

with HTTP `400`. The response does not reflect the rejected identifier or
storage-engine details. The existing default remains `usr_customer_demo`.

## Verification

```bash
python3 scripts/task_requester_validation_smoke.py
```

The isolated SQLite smoke verifies:

- an unknown requester returns the bounded `400` response instead of a
  foreign-key `500`;
- rejection creates no task, Runtime Event, or Audit Log;
- an invalid requester cannot partially update an existing task;
- an existing explicit requester and the default requester still succeed;
- raw rejected input, SQL details, and token-like material are omitted.

The smoke is part of the deterministic backend CI job. It does not exercise a
live model or external connector.
