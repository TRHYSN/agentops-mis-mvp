-- Align durable workspace roles with the Human Session authorization model.
-- The migration runner owns the transaction and exact receipt.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE workspace_memberships
DROP CONSTRAINT IF EXISTS workspace_memberships_role_check;

ALTER TABLE workspace_memberships
ADD CONSTRAINT workspace_memberships_role_check
CHECK(
    role IN (
        'viewer','operator','approver','reviewer','workspace-admin','owner'
    )
) NOT VALID;

ALTER TABLE workspace_memberships
VALIDATE CONSTRAINT workspace_memberships_role_check;
