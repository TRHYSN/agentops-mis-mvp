-- One-shot Human-authorized challenges are the sole database capability for
-- commercial workspace-entitlement administration. The migration runner owns
-- this transaction and later binds the dedicated entitlement-admin login role.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS entitlement_admin_challenges (
    challenge_id TEXT NOT NULL,
    token_sha256 TEXT NOT NULL,
    request_json JSONB NOT NULL,
    request_sha256 TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    operator_user_id TEXT NOT NULL,
    human_session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    bound_admin_role TEXT NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_action TEXT,
    CONSTRAINT entitlement_admin_challenges_pkey PRIMARY KEY(challenge_id),
    CONSTRAINT entitlement_admin_challenges_token_sha256_key
        UNIQUE(token_sha256),
    CONSTRAINT entitlement_admin_challenges_token_sha256_check
        CHECK(token_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT entitlement_admin_challenges_request_sha256_check
        CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT entitlement_admin_challenges_request_object_check
        CHECK(jsonb_typeof(request_json)='object'),
    CONSTRAINT entitlement_admin_challenges_binding_check CHECK(
        challenge_id ~ '^entc_[a-f0-9]{32}$'
        AND workspace_id<>''
        AND operator_user_id<>''
        AND human_session_id<>''
        AND bound_admin_role<>''
    ),
    CONSTRAINT entitlement_admin_challenges_mode_check
        CHECK(mode IN ('plan','confirm')),
    CONSTRAINT entitlement_admin_challenges_window_check
        CHECK(expires_at>issued_at AND expires_at<=issued_at+INTERVAL '90 seconds'),
    CONSTRAINT entitlement_admin_challenges_consumption_check CHECK(
        (consumed_at IS NULL AND consumed_action IS NULL)
        OR (
            consumed_at IS NOT NULL
            AND consumed_action IN ('plan','apply')
            AND consumed_at>=issued_at
        )
    ),
    CONSTRAINT entitlement_admin_challenges_operator_fkey
        FOREIGN KEY(operator_user_id) REFERENCES users(user_id),
    CONSTRAINT entitlement_admin_challenges_session_fkey
        FOREIGN KEY(human_session_id) REFERENCES human_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_entitlement_admin_challenges_expiry_v11
ON entitlement_admin_challenges(expires_at)
WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_entitlement_admin_challenges_binding_v11
ON entitlement_admin_challenges(
    workspace_id,operator_user_id,human_session_id,mode
);

REVOKE ALL PRIVILEGES ON TABLE entitlement_admin_challenges FROM PUBLIC;

DO $migration$
DECLARE
  v_schema TEXT := current_schema();
  v_owner TEXT := current_user;
BEGIN
  IF v_schema IS NULL OR v_schema IN ('pg_catalog','information_schema') THEN
    RAISE EXCEPTION USING
      ERRCODE='3F000',
      MESSAGE='entitlement_admin_application_schema_required';
  END IF;

  IF (
    SELECT tableowner<>v_owner
    FROM pg_tables
    WHERE schemaname=v_schema
      AND tablename='entitlement_admin_challenges'
  ) IS DISTINCT FROM FALSE THEN
    RAISE EXCEPTION USING
      ERRCODE='42501',
      MESSAGE='entitlement_admin_challenge_owner_invalid';
  END IF;

  -- This is the SQL equivalent of ledger.ts canonicalValue/pythonJson for
  -- JSON values that have crossed the TypeScript JSON serialization boundary.
  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_canonical_json_number_v1(
      p_value NUMERIC
    )
    RETURNS TEXT
    LANGUAGE plpgsql
    IMMUTABLE
    STRICT
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_absolute NUMERIC := abs(p_value);
      v_exponent INTEGER;
      v_mantissa NUMERIC;
      v_text TEXT;
    BEGIN
      IF p_value=0 THEN
        RETURN '0';
      END IF;
      IF v_absolute>=1e21 OR v_absolute<1e-6 THEN
        v_exponent=floor(log(v_absolute))::INTEGER;
        v_mantissa=p_value/power(10::NUMERIC,v_exponent);
        v_text=trim_scale(v_mantissa)::TEXT;
        IF position('.' IN v_text)>0 THEN
          v_text=rtrim(rtrim(v_text,'0'),'.');
        END IF;
        RETURN v_text || 'e'
          || CASE WHEN v_exponent>=0 THEN '+' ELSE '' END
          || v_exponent::TEXT;
      END IF;
      v_text=trim_scale(p_value)::TEXT;
      IF position('.' IN v_text)>0 THEN
        v_text=rtrim(rtrim(v_text,'0'),'.');
      END IF;
      RETURN v_text;
    END
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_canonical_json_text_v1(
      p_value JSONB
    )
    RETURNS TEXT
    LANGUAGE plpgsql
    IMMUTABLE
    STRICT
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_type TEXT := jsonb_typeof(p_value);
      v_result TEXT;
      v_number NUMERIC;
    BEGIN
      IF v_type='null' THEN RETURN 'null'; END IF;
      IF v_type='boolean' OR v_type='string' THEN
        RETURN p_value::TEXT;
      END IF;
      IF v_type='number' THEN
        RETURN %1$I.agentops_canonical_json_number_v1(
          (p_value::TEXT)::NUMERIC
        );
      END IF;
      IF v_type='array' THEN
        SELECT '[' || COALESCE(string_agg(
          %1$I.agentops_canonical_json_text_v1(item.value),
          ', ' ORDER BY item.ordinality
        ),'') || ']'
        INTO v_result
        FROM jsonb_array_elements(p_value)
          WITH ORDINALITY AS item(value,ordinality);
        RETURN v_result;
      END IF;
      IF v_type='object' THEN
        IF p_value ? '__agentops_python_float__' THEN
          IF jsonb_typeof(p_value->'__agentops_python_float__')<>'number' THEN
            RAISE EXCEPTION USING
              ERRCODE='22023',
              MESSAGE='stable_hash_python_float_invalid';
          END IF;
          v_number=(p_value->>'__agentops_python_float__')::NUMERIC;
          v_result=%1$I.agentops_canonical_json_number_v1(v_number);
          IF trunc(v_number)=v_number
            AND position('e' IN v_result)=0
          THEN
            v_result=v_result || '.0';
          END IF;
          RETURN v_result;
        END IF;
        SELECT '{' || COALESCE(string_agg(
          to_jsonb(item.key)::TEXT || ': '
            || %1$I.agentops_canonical_json_text_v1(item.value),
          ', ' ORDER BY item.key COLLATE "C"
        ),'') || '}'
        INTO v_result
        FROM jsonb_each(p_value) AS item(key,value);
        RETURN v_result;
      END IF;
      RAISE EXCEPTION USING
        ERRCODE='22023',
        MESSAGE='stable_hash_json_type_invalid';
    END
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_stable_hash_v1(
      p_value JSONB
    )
    RETURNS TEXT
    LANGUAGE sql
    IMMUTABLE
    STRICT
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
      SELECT encode(
        sha256(convert_to(
          %1$I.agentops_canonical_json_text_v1($1),
          'UTF8'
        )),
        'hex'
      )
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_validate_entitlement_request_v11(
      p_request JSONB,
      p_workspace_id TEXT,
      p_operator_user_id TEXT,
      p_mode TEXT
    )
    RETURNS JSONB
    LANGUAGE plpgsql
    STABLE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_configuration JSONB;
      v_guard JSONB;
      v_cost NUMERIC;
      v_effective TIMESTAMPTZ;
      v_expires TIMESTAMPTZ;
      v_integer_key TEXT;
      v_now TIMESTAMPTZ := clock_timestamp();
      v_enrollment_enabled BOOLEAN;
      v_session_enabled BOOLEAN;
      v_run_enabled BOOLEAN;
      v_max_agents INTEGER;
      v_max_enrollments INTEGER;
      v_max_sessions INTEGER;
      v_max_concurrent INTEGER;
      v_max_runs INTEGER;
    BEGIN
      IF jsonb_typeof(p_request)<>'object'
        OR p_request->>'contract'
          IS DISTINCT FROM 'agentops_workspace_entitlement_administration_v2'
        OR p_request->>'workspace_id' IS DISTINCT FROM p_workspace_id
        OR p_request->>'operator_user_id' IS DISTINCT FROM p_operator_user_id
        OR p_request->>'mode' IS DISTINCT FROM p_mode
        OR p_mode NOT IN ('plan','confirm')
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_request_binding_invalid';
      END IF;
      IF (
        SELECT count(*)
        FROM jsonb_object_keys(p_request)
      )<>6 OR NOT (
        p_request ?& ARRAY[
          'contract','workspace_id','operator_user_id','mode','guard',
          'configuration'
        ]
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_request_shape_invalid';
      END IF;

      v_guard=p_request->'guard';
      v_configuration=p_request->'configuration';
      IF jsonb_typeof(v_guard)<>'object'
        OR jsonb_typeof(v_configuration)<>'object'
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_request_shape_invalid';
      END IF;
      IF v_guard->>'kind' NOT IN (
        'none','expect_absent','expected_revision'
      ) OR (
        v_guard->>'kind'='expected_revision'
        AND COALESCE(v_guard->>'revision','') !~ '^[a-f0-9]{64}$'
      ) OR (
        v_guard->>'kind'='expected_revision'
        AND (SELECT count(*) FROM jsonb_object_keys(v_guard))<>2
      ) OR (
        v_guard->>'kind'<>'expected_revision'
        AND (
          v_guard ? 'revision'
          OR (SELECT count(*) FROM jsonb_object_keys(v_guard))<>1
        )
      ) OR (
        p_mode='confirm'
        AND v_guard->>'kind' NOT IN ('expect_absent','expected_revision')
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_guard_invalid';
      END IF;

      IF (
        SELECT count(*)
        FROM jsonb_object_keys(v_configuration)
      )<>11 OR NOT (
        v_configuration ?& ARRAY[
          'edition','status','capabilities','max_agents',
          'max_active_enrollments','max_active_sessions_per_agent',
          'max_concurrent_runs','max_monthly_runs','max_monthly_cost_usd',
          'effective_at','expires_at'
        ]
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_configuration_shape_invalid';
      END IF;
      IF v_configuration->>'edition' NOT IN (
        'free_local','pro_workspace','team_governance','enterprise_byoc'
      ) OR v_configuration->>'status' NOT IN (
        'active','inactive','suspended','expired'
      ) OR jsonb_typeof(v_configuration->'capabilities')<>'object'
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_configuration_invalid';
      END IF;
      IF (
        SELECT count(*)
        FROM jsonb_object_keys(v_configuration->'capabilities')
      )<>3 OR NOT (
        (v_configuration->'capabilities') ?& ARRAY[
          'enrollment_issue','session_issue','run_start'
        ]
      ) OR EXISTS (
        SELECT 1
        FROM jsonb_each(v_configuration->'capabilities') AS capability
        WHERE jsonb_typeof(capability.value)<>'boolean'
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_capabilities_invalid';
      END IF;

      FOREACH v_integer_key IN ARRAY ARRAY[
        'max_agents','max_active_enrollments',
        'max_active_sessions_per_agent','max_concurrent_runs',
        'max_monthly_runs'
      ] LOOP
        IF jsonb_typeof(v_configuration->v_integer_key)<>'number'
          OR (v_configuration->>v_integer_key) !~ '^[0-9]+$'
          OR (v_configuration->>v_integer_key)::NUMERIC>2147483647
        THEN
          RAISE EXCEPTION USING
            ERRCODE='22023',
            MESSAGE='entitlement_admin_integer_quota_invalid';
        END IF;
      END LOOP;

      IF jsonb_typeof(v_configuration->'max_monthly_cost_usd')<>'string'
        OR (v_configuration->>'max_monthly_cost_usd')
          !~ '^(0|[1-9][0-9]{0,9})\.[0-9]{6}$'
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_cost_quota_invalid';
      END IF;
      v_cost=(v_configuration->>'max_monthly_cost_usd')::NUMERIC;
      IF trunc(v_cost)>1000000000 THEN
        RAISE EXCEPTION USING
          ERRCODE='22003',
          MESSAGE='entitlement_admin_cost_quota_invalid';
      END IF;

      IF jsonb_typeof(v_configuration->'effective_at')<>'string'
        OR (v_configuration->>'effective_at')
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'
        OR (
          v_configuration->'expires_at'<>'null'::JSONB
          AND (
            jsonb_typeof(v_configuration->'expires_at')<>'string'
            OR (v_configuration->>'expires_at')
              !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'
          )
        )
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_effective_window_invalid';
      END IF;
      v_effective=(v_configuration->>'effective_at')::TIMESTAMPTZ;
      v_expires=CASE
        WHEN v_configuration->'expires_at'='null'::JSONB THEN NULL
        ELSE (v_configuration->>'expires_at')::TIMESTAMPTZ
      END;
      IF to_char(
          v_effective AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
        )<>v_configuration->>'effective_at'
        OR (
          v_expires IS NOT NULL
          AND to_char(
            v_expires AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
          )<>v_configuration->>'expires_at'
        )
        OR (v_expires IS NOT NULL AND v_expires<=v_effective)
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_effective_window_invalid';
      END IF;

      v_enrollment_enabled=
        (v_configuration#>>'{capabilities,enrollment_issue}')::BOOLEAN;
      v_session_enabled=
        (v_configuration#>>'{capabilities,session_issue}')::BOOLEAN;
      v_run_enabled=
        (v_configuration#>>'{capabilities,run_start}')::BOOLEAN;
      v_max_agents=(v_configuration->>'max_agents')::INTEGER;
      v_max_enrollments=
        (v_configuration->>'max_active_enrollments')::INTEGER;
      v_max_sessions=
        (v_configuration->>'max_active_sessions_per_agent')::INTEGER;
      v_max_concurrent=
        (v_configuration->>'max_concurrent_runs')::INTEGER;
      v_max_runs=(v_configuration->>'max_monthly_runs')::INTEGER;

      IF v_configuration->>'edition'='free_local'
        AND v_configuration->>'status'='active'
      THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='free_local_commercial_active_forbidden';
      END IF;
      IF v_configuration->>'status'='active' AND (
        v_effective>v_now
        OR (v_expires IS NOT NULL AND v_expires<=v_now)
        OR NOT (
          v_enrollment_enabled OR v_session_enabled OR v_run_enabled
        )
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='active_entitlement_invariant_invalid';
      END IF;
      IF v_configuration->>'status'='expired' AND (
        v_expires IS NULL OR v_expires>v_now
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='expired_entitlement_window_invalid';
      END IF;
      IF v_configuration->>'status' NOT IN ('active','expired')
        AND v_expires IS NOT NULL
        AND v_expires<=v_now
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_status_window_mismatch';
      END IF;
      IF v_session_enabled AND NOT v_enrollment_enabled THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='session_capability_requires_enrollment';
      END IF;
      IF (
        v_enrollment_enabled
        AND (v_max_agents=0 OR v_max_enrollments=0)
      ) OR (
        NOT v_enrollment_enabled
        AND (v_max_agents<>0 OR v_max_enrollments<>0)
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='enrollment_capability_quota_invalid';
      END IF;
      IF (
        v_session_enabled AND v_max_sessions=0
      ) OR (
        NOT v_session_enabled AND v_max_sessions<>0
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='session_capability_quota_invalid';
      END IF;
      IF (
        v_run_enabled
        AND (v_max_concurrent=0 OR v_max_runs=0 OR v_cost=0)
      ) OR (
        NOT v_run_enabled
        AND (v_max_concurrent<>0 OR v_max_runs<>0 OR v_cost<>0)
      ) THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='run_capability_quota_invalid';
      END IF;
      RETURN v_configuration;
    END
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_entitlement_desired_v11(
      p_request JSONB
    )
    RETURNS JSONB
    LANGUAGE sql
    IMMUTABLE
    STRICT
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
      SELECT jsonb_build_object(
        'workspace_id',$1->>'workspace_id',
        'edition',$1#>>'{configuration,edition}',
        'status',$1#>>'{configuration,status}',
        'capabilities_json',$1#>'{configuration,capabilities}',
        'max_agents',($1#>>'{configuration,max_agents}')::INTEGER,
        'max_active_enrollments',
          ($1#>>'{configuration,max_active_enrollments}')::INTEGER,
        'max_active_sessions_per_agent',
          ($1#>>'{configuration,max_active_sessions_per_agent}')::INTEGER,
        'max_concurrent_runs',
          ($1#>>'{configuration,max_concurrent_runs}')::INTEGER,
        'max_monthly_runs',
          ($1#>>'{configuration,max_monthly_runs}')::INTEGER,
        'max_monthly_cost_usd',
          trim_scale(
            ($1#>>'{configuration,max_monthly_cost_usd}')::NUMERIC
          )::TEXT,
        'effective_at',$1#>>'{configuration,effective_at}',
        'expires_at',$1#>'{configuration,expires_at}'
      )
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_entitlement_stored_v11(
      p_workspace_id TEXT
    )
    RETURNS JSONB
    LANGUAGE sql
    STABLE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
      SELECT jsonb_build_object(
        'workspace_id',entitlement.workspace_id,
        'edition',entitlement.edition,
        'status',entitlement.status,
        'capabilities_json',entitlement.capabilities_json,
        'max_agents',entitlement.max_agents,
        'max_active_enrollments',entitlement.max_active_enrollments,
        'max_active_sessions_per_agent',
          entitlement.max_active_sessions_per_agent,
        'max_concurrent_runs',entitlement.max_concurrent_runs,
        'max_monthly_runs',entitlement.max_monthly_runs,
        'max_monthly_cost_usd',
          trim_scale(entitlement.max_monthly_cost_usd)::TEXT,
        'effective_at',
          to_char(
            entitlement.effective_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
          ),
        'expires_at',CASE
          WHEN entitlement.expires_at IS NULL THEN 'null'::JSONB
          ELSE to_jsonb(to_char(
            entitlement.expires_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
          ))
        END
      )
      FROM %1$I.workspace_entitlements AS entitlement
      WHERE entitlement.workspace_id=$1
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_entitlement_revision_v11(
      p_workspace_id TEXT,
      p_updated_at TIMESTAMPTZ
    )
    RETURNS TEXT
    LANGUAGE sql
    IMMUTABLE
    STRICT
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
      SELECT encode(sha256(convert_to(
        'agentops_workspace_entitlement_administration_v2:'
          || $1 || ':'
          || to_char(
            $2 AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
          ),
        'UTF8'
      )),'hex')
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_assert_entitlement_operator_v11(
      p_workspace_id TEXT,
      p_operator_user_id TEXT
    )
    RETURNS VOID
    LANGUAGE plpgsql
    STABLE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    BEGIN
      IF NOT EXISTS (
        SELECT 1
        FROM %1$I.users AS app_user
        JOIN %1$I.workspace_memberships AS membership
          ON membership.user_id=app_user.user_id
         AND membership.workspace_id=p_workspace_id
        WHERE app_user.user_id=p_operator_user_id
          AND app_user.role IN ('operator','owner','workspace-admin')
          AND membership.status='active'
          AND membership.role IN ('operator','owner','workspace-admin')
      ) OR NOT EXISTS (
        SELECT 1
        FROM %1$I.human_login_credentials AS credential
        WHERE credential.user_id=p_operator_user_id
          AND credential.status='active'
      )
      THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='entitlement_admin_operator_authority_invalid';
      END IF;
    END
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_assert_restricted_login_role_v11(
      p_role_name TEXT
    )
    RETURNS VOID
    LANGUAGE plpgsql
    STABLE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_role RECORD;
    BEGIN
      SELECT
        rolcanlogin,
        rolsuper,
        rolcreaterole,
        rolcreatedb,
        rolreplication,
        rolbypassrls,
        rolinherit
      INTO v_role
      FROM pg_catalog.pg_roles
      WHERE rolname=p_role_name;
      IF NOT FOUND
        OR NOT v_role.rolcanlogin
        OR v_role.rolsuper
        OR v_role.rolcreaterole
        OR v_role.rolcreatedb
        OR v_role.rolreplication
        OR v_role.rolbypassrls
        OR v_role.rolinherit
        OR EXISTS (
          SELECT 1
          FROM pg_catalog.pg_auth_members membership
          JOIN pg_catalog.pg_roles member_role
            ON member_role.oid=membership.member
          JOIN pg_catalog.pg_roles granted_role
            ON granted_role.oid=membership.roleid
          WHERE member_role.rolname=p_role_name
             OR granted_role.rolname=p_role_name
        )
      THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='entitlement_admin_database_role_invalid';
      END IF;
    END
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_issue_entitlement_admin_challenge_core_v11(
      p_human_session_id TEXT,
      p_workspace_id TEXT,
      p_operator_user_id TEXT,
      p_mode TEXT,
      p_request JSONB,
      p_token_sha256 TEXT,
      p_ttl INTERVAL,
      p_bound_admin_role TEXT
    )
    RETURNS JSONB
    LANGUAGE plpgsql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_now TIMESTAMPTZ := clock_timestamp();
      v_session %1$I.human_sessions%%ROWTYPE;
      v_challenge_id TEXT;
      v_request_sha256 TEXT;
    BEGIN
      IF NULLIF(btrim(p_human_session_id),'') IS NULL
        OR NULLIF(btrim(p_workspace_id),'') IS NULL
        OR NULLIF(btrim(p_operator_user_id),'') IS NULL
        OR p_token_sha256 !~ '^[a-f0-9]{64}$'
        OR p_ttl<INTERVAL '1 second'
        OR p_ttl>INTERVAL '90 seconds'
        OR NULLIF(btrim(p_bound_admin_role),'') IS NULL
        OR p_bound_admin_role='__agentops_entitlement_admin_role_unbound__'
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_challenge_binding_invalid';
      END IF;

      PERFORM %1$I.agentops_validate_entitlement_request_v11(
        p_request,p_workspace_id,p_operator_user_id,p_mode
      );
      PERFORM %1$I.agentops_assert_restricted_login_role_v11(session_user);
      PERFORM %1$I.agentops_assert_restricted_login_role_v11(
        p_bound_admin_role
      );
      IF session_user=p_bound_admin_role THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='entitlement_admin_role_separation_invalid';
      END IF;

      SELECT *
      INTO v_session
      FROM %1$I.human_sessions
      WHERE session_id=p_human_session_id
      FOR UPDATE;
      IF NOT FOUND
        OR v_session.user_id<>p_operator_user_id
        OR v_session.status<>'active'
        OR v_session.expires_at::TIMESTAMPTZ<=v_now
        OR v_session.created_at::TIMESTAMPTZ<v_now-INTERVAL '5 minutes'
      THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='entitlement_admin_human_session_invalid';
      END IF;
      PERFORM %1$I.agentops_assert_entitlement_operator_v11(
        p_workspace_id,p_operator_user_id
      );

      v_request_sha256=%1$I.agentops_stable_hash_v1(p_request);
      v_challenge_id='entc_'
        || replace(gen_random_uuid()::TEXT,'-','');
      UPDATE %1$I.human_sessions
      SET
        status='revoked',
        revoked_at=to_char(
          v_now AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
        ),
        last_seen_at=to_char(
          v_now AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
        )
      WHERE session_id=p_human_session_id;

      INSERT INTO %1$I.entitlement_admin_challenges(
        challenge_id,token_sha256,request_json,request_sha256,workspace_id,
        operator_user_id,human_session_id,mode,bound_admin_role,issued_at,
        expires_at,consumed_at,consumed_action
      ) VALUES(
        v_challenge_id,p_token_sha256,p_request,v_request_sha256,
        p_workspace_id,p_operator_user_id,p_human_session_id,p_mode,
        p_bound_admin_role,v_now,v_now+p_ttl,NULL,NULL
      );

      RETURN jsonb_build_object(
        'contract','agentops_workspace_entitlement_admin_challenge_v11',
        'ok',true,
        'challenge_id',v_challenge_id,
        'mode',p_mode,
        'workspace_id',p_workspace_id,
        'operator_user_id',p_operator_user_id,
        'request_sha256',v_request_sha256,
        'issued_at',to_char(
          v_now AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
        ),
        'expires_at',to_char(
          (v_now+p_ttl) AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
        ),
        'single_use',true,
        'session_revoked',true,
        'credentials_omitted',true,
        'token_omitted',true,
        'raw_config_omitted',true
      );
    END
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_issue_workspace_entitlement_admin_challenge_v11(
      p_human_session_id TEXT,
      p_workspace_id TEXT,
      p_operator_user_id TEXT,
      p_mode TEXT,
      p_request JSONB,
      p_token_sha256 TEXT,
      p_ttl INTERVAL
    )
    RETURNS JSONB
    LANGUAGE sql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    SET agentops.entitlement_admin_role=
      '__agentops_entitlement_admin_role_unbound__'
    AS $function$
      SELECT %1$I.agentops_issue_entitlement_admin_challenge_core_v11(
        $1,$2,$3,$4,$5,$6,$7,
        current_setting('agentops.entitlement_admin_role',true)
      )
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_claim_entitlement_admin_challenge_v11(
      p_challenge_id TEXT,
      p_challenge_token TEXT,
      p_request JSONB,
      p_expected_mode TEXT,
      p_action TEXT
    )
    RETURNS %1$I.entitlement_admin_challenges
    LANGUAGE plpgsql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_challenge %1$I.entitlement_admin_challenges%%ROWTYPE;
      v_token_sha256 TEXT;
      v_request_sha256 TEXT;
    BEGIN
      IF p_expected_mode NOT IN ('plan','confirm')
        OR p_action NOT IN ('plan','apply')
        OR NULLIF(p_challenge_token,'') IS NULL
      THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_challenge_claim_invalid';
      END IF;
      SELECT *
      INTO v_challenge
      FROM %1$I.entitlement_admin_challenges
      WHERE challenge_id=p_challenge_id
      FOR UPDATE;
      IF NOT FOUND THEN
        RAISE EXCEPTION USING
          ERRCODE='P0002',
          MESSAGE='entitlement_admin_challenge_not_found';
      END IF;
      v_token_sha256=encode(
        sha256(convert_to(p_challenge_token,'UTF8')),
        'hex'
      );
      v_request_sha256=%1$I.agentops_stable_hash_v1(p_request);
      IF session_user<>v_challenge.bound_admin_role THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='entitlement_admin_session_role_invalid';
      END IF;
      PERFORM %1$I.agentops_assert_restricted_login_role_v11(session_user);
      IF v_challenge.mode<>p_expected_mode
        OR v_challenge.token_sha256<>v_token_sha256
        OR v_challenge.request_sha256<>v_request_sha256
        OR v_challenge.request_json<>p_request
      THEN
        RAISE EXCEPTION USING
          ERRCODE='42501',
          MESSAGE='entitlement_admin_challenge_binding_mismatch';
      END IF;
      IF v_challenge.consumed_at IS NOT NULL THEN
        RAISE EXCEPTION USING
          ERRCODE='55000',
          MESSAGE='entitlement_admin_challenge_replayed';
      END IF;
      IF v_challenge.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION USING
          ERRCODE='55000',
          MESSAGE='entitlement_admin_challenge_expired';
      END IF;
      PERFORM %1$I.agentops_validate_entitlement_request_v11(
        p_request,v_challenge.workspace_id,v_challenge.operator_user_id,
        v_challenge.mode
      );
      PERFORM %1$I.agentops_assert_entitlement_operator_v11(
        v_challenge.workspace_id,v_challenge.operator_user_id
      );
      UPDATE %1$I.entitlement_admin_challenges
      SET consumed_at=clock_timestamp(),consumed_action=p_action
      WHERE challenge_id=v_challenge.challenge_id
      RETURNING * INTO v_challenge;
      RETURN v_challenge;
    END
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_plan_workspace_entitlement_core_v11(
      p_challenge_id TEXT,
      p_challenge_token TEXT,
      p_request JSONB
    )
    RETURNS JSONB
    LANGUAGE plpgsql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_challenge %1$I.entitlement_admin_challenges%%ROWTYPE;
      v_existing %1$I.workspace_entitlements%%ROWTYPE;
      v_desired JSONB;
      v_previous JSONB;
      v_desired_hash TEXT;
      v_previous_hash TEXT;
      v_revision TEXT;
      v_outcome TEXT;
      v_required_guard TEXT;
      v_guard_kind TEXT;
      v_guard_revision TEXT;
    BEGIN
      v_challenge=%1$I.agentops_claim_entitlement_admin_challenge_v11(
        p_challenge_id,p_challenge_token,p_request,'plan','plan'
      );
      PERFORM pg_advisory_xact_lock(hashtextextended(
        'agentops:workspace-entitlement:' || v_challenge.workspace_id,0
      ));
      SELECT *
      INTO v_existing
      FROM %1$I.workspace_entitlements
      WHERE workspace_id=v_challenge.workspace_id;
      v_desired=%1$I.agentops_entitlement_desired_v11(p_request);
      v_desired_hash=%1$I.agentops_stable_hash_v1(v_desired);
      v_guard_kind=p_request#>>'{guard,kind}';
      v_guard_revision=p_request#>>'{guard,revision}';
      IF FOUND THEN
        v_previous=%1$I.agentops_entitlement_stored_v11(
          v_challenge.workspace_id
        );
        v_previous_hash=%1$I.agentops_stable_hash_v1(v_previous);
        v_revision=%1$I.agentops_entitlement_revision_v11(
          v_challenge.workspace_id,v_existing.updated_at
        );
        IF v_guard_kind='expect_absent' THEN
          RETURN jsonb_build_object(
            'contract','agentops_workspace_entitlement_administration_v2',
            'ok',false,
            'mode','plan',
            'workspace_id',v_challenge.workspace_id,
            'error_code','entitlement_already_exists',
            'challenge_consumed',true,
            'audit_appended',false,
            'raw_config_omitted',true,
            'credentials_omitted',true,
            'token_omitted',true
          );
        END IF;
        IF v_guard_kind='expected_revision'
          AND v_guard_revision IS DISTINCT FROM v_revision
        THEN
          RETURN jsonb_build_object(
            'contract','agentops_workspace_entitlement_administration_v2',
            'ok',false,
            'mode','plan',
            'workspace_id',v_challenge.workspace_id,
            'error_code','entitlement_revision_stale',
            'challenge_consumed',true,
            'audit_appended',false,
            'raw_config_omitted',true,
            'credentials_omitted',true,
            'token_omitted',true
          );
        END IF;
        IF v_previous_hash=v_desired_hash THEN
          v_outcome='unchanged';
          v_required_guard=NULL;
        ELSE
          v_outcome='would_update';
          v_required_guard='expected_revision';
        END IF;
      ELSE
        IF v_guard_kind='expected_revision' THEN
          RETURN jsonb_build_object(
            'contract','agentops_workspace_entitlement_administration_v2',
            'ok',false,
            'mode','plan',
            'workspace_id',v_challenge.workspace_id,
            'error_code','entitlement_absent',
            'challenge_consumed',true,
            'audit_appended',false,
            'raw_config_omitted',true,
            'credentials_omitted',true,
            'token_omitted',true
          );
        END IF;
        v_previous=NULL;
        v_previous_hash=NULL;
        v_revision=NULL;
        v_outcome='would_create';
        v_required_guard='expect_absent';
      END IF;
      RETURN jsonb_build_object(
        'contract','agentops_workspace_entitlement_administration_v2',
        'ok',true,
        'mode','plan',
        'outcome',v_outcome,
        'workspace_id',v_challenge.workspace_id,
        'desired_config_hash',v_desired_hash,
        'previous_config_hash',v_previous_hash,
        'revision',v_revision,
        'required_guard',v_required_guard,
        'challenge_consumed',true,
        'audit_appended',false,
        'raw_config_omitted',true,
        'credentials_omitted',true,
        'token_omitted',true
      );
    END
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_plan_workspace_entitlement_v11(
      p_challenge_id TEXT,
      p_challenge_token TEXT,
      p_request JSONB
    )
    RETURNS JSONB
    LANGUAGE sql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
      SELECT %1$I.agentops_plan_workspace_entitlement_core_v11($1,$2,$3)
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_append_entitlement_audit_v11(
      p_workspace_id TEXT,
      p_operator_user_id TEXT,
      p_operation TEXT,
      p_guard_kind TEXT,
      p_desired JSONB,
      p_previous JSONB,
      p_desired_hash TEXT,
      p_previous_hash TEXT,
      p_previous_revision TEXT
    )
    RETURNS TEXT
    LANGUAGE plpgsql
    VOLATILE
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_operator_ref TEXT;
      v_request_hash TEXT;
      v_metadata JSONB;
      v_before_hash TEXT;
      v_after_hash TEXT;
      v_previous_chain TEXT;
      v_previous_created TIMESTAMPTZ;
      v_created_at TIMESTAMPTZ;
      v_chain_hash TEXT;
      v_audit_id TEXT;
    BEGIN
      IF p_operation NOT IN ('created','updated') THEN
        RAISE EXCEPTION USING
          ERRCODE='22023',
          MESSAGE='entitlement_admin_audit_operation_invalid';
      END IF;
      v_operator_ref='operator_ref_' || substr(encode(sha256(convert_to(
        'operator:' || p_operator_user_id,'UTF8'
      )),'hex'),1,16);
      v_request_hash=%1$I.agentops_stable_hash_v1(jsonb_build_object(
        'contract','agentops_workspace_entitlement_administration_v2',
        'workspace_id',p_workspace_id,
        'operator_ref',v_operator_ref,
        'operation',p_operation,
        'desired_config_hash',p_desired_hash,
        'previous_revision',CASE
          WHEN p_previous_revision IS NULL THEN 'null'::JSONB
          ELSE to_jsonb(p_previous_revision)
        END
      ));
      v_metadata=jsonb_build_object(
        'administration_contract',
          'agentops_workspace_entitlement_administration_v2',
        'operation',p_operation,
        'operator_ref',v_operator_ref,
        'desired_config_hash',p_desired_hash,
        'previous_config_hash',CASE
          WHEN p_previous_hash IS NULL THEN 'null'::JSONB
          ELSE to_jsonb(p_previous_hash)
        END,
        'previous_revision',CASE
          WHEN p_previous_revision IS NULL THEN 'null'::JSONB
          ELSE to_jsonb(p_previous_revision)
        END,
        'optimistic_guard',p_guard_kind,
        'credentials_omitted',true,
        'dsn_omitted',true,
        'raw_config_omitted',true,
        'request_hash',v_request_hash,
        'workspace_id',p_workspace_id
      );
      v_before_hash=CASE
        WHEN p_previous IS NULL THEN NULL
        ELSE %1$I.agentops_stable_hash_v1(p_previous)
      END;
      v_after_hash=%1$I.agentops_stable_hash_v1(p_desired);

      PERFORM pg_advisory_xact_lock(1095779668);
      SELECT audit.tamper_chain_hash,audit.created_at::TIMESTAMPTZ
      INTO v_previous_chain,v_previous_created
      FROM %1$I.audit_logs AS audit
      ORDER BY audit.created_at DESC,audit.audit_id DESC
      LIMIT 1;
      v_created_at=clock_timestamp();
      IF v_previous_created IS NOT NULL
        AND v_previous_created>=v_created_at
      THEN
        v_created_at=v_previous_created+INTERVAL '1 millisecond';
      END IF;
      v_chain_hash=%1$I.agentops_stable_hash_v1(jsonb_build_object(
        'actor_type','user',
        'actor_id',p_operator_user_id,
        'action','workspace_entitlement.' || p_operation,
        'entity_type','workspace_entitlements',
        'entity_id',p_workspace_id,
        'before_hash',CASE
          WHEN v_before_hash IS NULL THEN 'null'::JSONB
          ELSE to_jsonb(v_before_hash)
        END,
        'after_hash',v_after_hash,
        'metadata_json',v_metadata,
        'previous',COALESCE(v_previous_chain,'genesis')
      ));
      v_audit_id='aud_' || substr(
        replace(gen_random_uuid()::TEXT,'-',''),1,12
      );
      INSERT INTO %1$I.audit_logs(
        audit_id,workspace_id,actor_type,actor_id,action,entity_type,entity_id,
        before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
      ) VALUES(
        v_audit_id,p_workspace_id,'user',p_operator_user_id,
        'workspace_entitlement.' || p_operation,'workspace_entitlements',
        p_workspace_id,v_before_hash,v_after_hash,v_metadata::TEXT,
        v_chain_hash,to_char(
          v_created_at AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
        )
      );
      RETURN v_audit_id;
    END
    $function$;
  $ddl$,v_schema);

  EXECUTE format($ddl$
    CREATE OR REPLACE FUNCTION %1$I.agentops_apply_workspace_entitlement_core_v11(
      p_challenge_id TEXT,
      p_challenge_token TEXT,
      p_request JSONB
    )
    RETURNS JSONB
    LANGUAGE plpgsql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
    DECLARE
      v_challenge %1$I.entitlement_admin_challenges%%ROWTYPE;
      v_existing %1$I.workspace_entitlements%%ROWTYPE;
      v_desired JSONB;
      v_previous JSONB;
      v_desired_hash TEXT;
      v_previous_hash TEXT;
      v_previous_revision TEXT;
      v_next_revision TEXT;
      v_guard_kind TEXT;
      v_guard_revision TEXT;
      v_operation TEXT;
      v_audit_id TEXT;
      v_updated_at TIMESTAMPTZ;
    BEGIN
      v_challenge=%1$I.agentops_claim_entitlement_admin_challenge_v11(
        p_challenge_id,p_challenge_token,p_request,'confirm','apply'
      );
      PERFORM pg_advisory_xact_lock(hashtextextended(
        'agentops:workspace-entitlement:' || v_challenge.workspace_id,0
      ));
      SELECT *
      INTO v_existing
      FROM %1$I.workspace_entitlements
      WHERE workspace_id=v_challenge.workspace_id
      FOR UPDATE;
      v_desired=%1$I.agentops_entitlement_desired_v11(p_request);
      v_desired_hash=%1$I.agentops_stable_hash_v1(v_desired);
      v_guard_kind=p_request#>>'{guard,kind}';
      v_guard_revision=p_request#>>'{guard,revision}';

      IF FOUND THEN
        v_previous=%1$I.agentops_entitlement_stored_v11(
          v_challenge.workspace_id
        );
        v_previous_hash=%1$I.agentops_stable_hash_v1(v_previous);
        v_previous_revision=%1$I.agentops_entitlement_revision_v11(
          v_challenge.workspace_id,v_existing.updated_at
        );
        IF v_guard_kind<>'expected_revision'
          OR v_guard_revision IS DISTINCT FROM v_previous_revision
        THEN
          RETURN jsonb_build_object(
            'contract','agentops_workspace_entitlement_administration_v2',
            'ok',false,'mode','confirmed',
            'workspace_id',v_challenge.workspace_id,
            'error_code',CASE
              WHEN v_guard_kind='expect_absent'
                THEN 'entitlement_already_exists'
              ELSE 'entitlement_revision_stale'
            END,
            'challenge_consumed',true,'audit_appended',false,
            'raw_config_omitted',true,'credentials_omitted',true,
            'token_omitted',true
          );
        END IF;
        IF v_previous_hash=v_desired_hash THEN
          RETURN jsonb_build_object(
            'contract','agentops_workspace_entitlement_administration_v2',
            'ok',true,'mode','confirmed','outcome','unchanged',
            'workspace_id',v_challenge.workspace_id,
            'desired_config_hash',v_desired_hash,
            'previous_config_hash',v_previous_hash,
            'revision',v_previous_revision,
            'required_guard','null'::JSONB,
            'challenge_consumed',true,'audit_appended',false,
            'raw_config_omitted',true,'credentials_omitted',true,
            'token_omitted',true
          );
        END IF;
        UPDATE %1$I.workspace_entitlements
        SET
          edition=p_request#>>'{configuration,edition}',
          status=p_request#>>'{configuration,status}',
          capabilities_json=p_request#>'{configuration,capabilities}',
          max_agents=(p_request#>>'{configuration,max_agents}')::INTEGER,
          max_active_enrollments=
            (p_request#>>'{configuration,max_active_enrollments}')::INTEGER,
          max_active_sessions_per_agent=
            (p_request#>>'{configuration,max_active_sessions_per_agent}')::INTEGER,
          max_concurrent_runs=
            (p_request#>>'{configuration,max_concurrent_runs}')::INTEGER,
          max_monthly_runs=
            (p_request#>>'{configuration,max_monthly_runs}')::INTEGER,
          max_monthly_cost_usd=
            (p_request#>>'{configuration,max_monthly_cost_usd}')::NUMERIC,
          effective_at=
            (p_request#>>'{configuration,effective_at}')::TIMESTAMPTZ,
          expires_at=CASE
            WHEN p_request#>'{configuration,expires_at}'='null'::JSONB
              THEN NULL
            ELSE (p_request#>>'{configuration,expires_at}')::TIMESTAMPTZ
          END,
          updated_at=GREATEST(
            clock_timestamp(),updated_at+INTERVAL '1 microsecond'
          ),
          updated_by_user_id=v_challenge.operator_user_id
        WHERE workspace_id=v_challenge.workspace_id
        RETURNING updated_at INTO v_updated_at;
        v_operation='updated';
      ELSE
        v_previous=NULL;
        v_previous_hash=NULL;
        v_previous_revision=NULL;
        IF v_guard_kind<>'expect_absent' THEN
          RETURN jsonb_build_object(
            'contract','agentops_workspace_entitlement_administration_v2',
            'ok',false,'mode','confirmed',
            'workspace_id',v_challenge.workspace_id,
            'error_code','entitlement_absent',
            'challenge_consumed',true,'audit_appended',false,
            'raw_config_omitted',true,'credentials_omitted',true,
            'token_omitted',true
          );
        END IF;
        INSERT INTO %1$I.workspace_entitlements(
          workspace_id,edition,status,capabilities_json,max_agents,
          max_active_enrollments,max_active_sessions_per_agent,
          max_concurrent_runs,max_monthly_runs,max_monthly_cost_usd,
          effective_at,expires_at,created_at,updated_at,updated_by_user_id
        ) VALUES(
          v_challenge.workspace_id,
          p_request#>>'{configuration,edition}',
          p_request#>>'{configuration,status}',
          p_request#>'{configuration,capabilities}',
          (p_request#>>'{configuration,max_agents}')::INTEGER,
          (p_request#>>'{configuration,max_active_enrollments}')::INTEGER,
          (p_request#>>'{configuration,max_active_sessions_per_agent}')::INTEGER,
          (p_request#>>'{configuration,max_concurrent_runs}')::INTEGER,
          (p_request#>>'{configuration,max_monthly_runs}')::INTEGER,
          (p_request#>>'{configuration,max_monthly_cost_usd}')::NUMERIC,
          (p_request#>>'{configuration,effective_at}')::TIMESTAMPTZ,
          CASE
            WHEN p_request#>'{configuration,expires_at}'='null'::JSONB
              THEN NULL
            ELSE (p_request#>>'{configuration,expires_at}')::TIMESTAMPTZ
          END,
          clock_timestamp(),clock_timestamp(),v_challenge.operator_user_id
        )
        RETURNING updated_at INTO v_updated_at;
        v_operation='created';
      END IF;

      v_audit_id=%1$I.agentops_append_entitlement_audit_v11(
        v_challenge.workspace_id,v_challenge.operator_user_id,v_operation,
        v_guard_kind,v_desired,v_previous,v_desired_hash,v_previous_hash,
        v_previous_revision
      );
      v_next_revision=%1$I.agentops_entitlement_revision_v11(
        v_challenge.workspace_id,v_updated_at
      );
      RETURN jsonb_build_object(
        'contract','agentops_workspace_entitlement_administration_v2',
        'ok',true,'mode','confirmed','outcome',v_operation,
        'workspace_id',v_challenge.workspace_id,
        'desired_config_hash',v_desired_hash,
        'previous_config_hash',v_previous_hash,
        'revision',v_next_revision,
        'required_guard','null'::JSONB,
        'challenge_consumed',true,'audit_appended',true,
        'audit_ref','audit_ref_' || substr(encode(sha256(convert_to(
          'audit:' || v_audit_id,'UTF8'
        )),'hex'),1,16),
        'raw_config_omitted',true,'credentials_omitted',true,
        'token_omitted',true
      );
    END
    $function$;

    CREATE OR REPLACE FUNCTION %1$I.agentops_apply_workspace_entitlement_v11(
      p_challenge_id TEXT,
      p_challenge_token TEXT,
      p_request JSONB
    )
    RETURNS JSONB
    LANGUAGE sql
    VOLATILE
    STRICT
    SECURITY DEFINER
    SET search_path=pg_catalog,%1$I,pg_temp
    AS $function$
      SELECT %1$I.agentops_apply_workspace_entitlement_core_v11($1,$2,$3)
    $function$;
  $ddl$,v_schema);

  -- No helper or public wrapper is ambiently executable. Provisioning grants
  -- access only to separate runtime_api wrappers that call these private cores.
  EXECUTE (
    SELECT string_agg(
      format(
        'REVOKE ALL ON FUNCTION %I.%I(%s) FROM PUBLIC',
        namespace_row.nspname,
        procedure_row.proname,
        pg_get_function_identity_arguments(procedure_row.oid)
      ),
      '; '
    )
    FROM pg_proc AS procedure_row
    JOIN pg_namespace AS namespace_row
      ON namespace_row.oid=procedure_row.pronamespace
    WHERE namespace_row.nspname=v_schema
      AND (
        procedure_row.proname LIKE 'agentops_%%entitlement%%v11'
        OR procedure_row.proname IN (
          'agentops_canonical_json_number_v1',
          'agentops_canonical_json_text_v1',
          'agentops_stable_hash_v1',
          'agentops_issue_workspace_entitlement_admin_challenge_v11',
          'agentops_plan_workspace_entitlement_v11',
          'agentops_apply_workspace_entitlement_v11'
        )
      )
  );
END
$migration$;
