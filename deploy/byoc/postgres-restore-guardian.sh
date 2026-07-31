#!/bin/sh
set -eu
umask 077

if [ "$#" -ne 1 ]; then
  printf "%s\n" restore_guardian_database_required >&2
  exit 2
fi

# AGENTOPS_RESTORE_GUARDIAN_SCRIPT_BEGIN
lease_key=7157544864185932631
lease_directory=$(mktemp -d)
lease_control="$lease_directory/control"
lease_ready="$lease_directory/ready"
mkfifo "$lease_control"
lease_pid=
lease_backend_pid=
lease_backend_start=
lease_nonce=${lease_directory##*/}
guardian_application_name="agentops_restore_guardian_${lease_nonce}"
guardian_watch_pid=
lease_acquired=false
lease_input_open=false
cleanup_done=false
restore_pid=
restore_input_open=true
exec 3<&0

guardian_identity_count() {
  printf "%s\n" \
    "SELECT count(*) FROM pg_stat_activity" \
    "WHERE pid=:'guardian_pid'::integer" \
    "  AND backend_start=:'guardian_backend_start'::timestamptz" \
    "  AND application_name=:'guardian_application_name'" \
    "  AND backend_type='client backend'" \
    "  AND datname='postgres'" \
    "  AND usename=current_user;" |
    psql \
      --username "$POSTGRES_USER" \
      --dbname postgres \
      --no-psqlrc \
      --set ON_ERROR_STOP=1 \
      --tuples-only \
      --no-align \
      --set guardian_pid="$lease_backend_pid" \
      --set guardian_backend_start="$lease_backend_start" \
      --set guardian_application_name="$guardian_application_name" \
      2>/dev/null
}

wait_for_lease_release() {
  [ "$lease_acquired" = true ] || return 0
  attempt=0
  while [ "$attempt" -lt 100 ]; do
    released=$(
      psql \
        --username "$POSTGRES_USER" \
        --dbname postgres \
        --no-psqlrc \
        --set ON_ERROR_STOP=1 \
        --tuples-only \
        --no-align \
        --command "SELECT CASE WHEN pg_try_advisory_lock($lease_key) THEN pg_advisory_unlock($lease_key) ELSE false END" \
        2>/dev/null || :
    )
    if [ "$released" = t ]; then
      lease_acquired=false
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 0.1
  done
  printf "%s\n" restore_database_lease_release_failed >&2
  return 1
}

cleanup_lease() {
  if [ "$cleanup_done" = true ]; then
    return 0
  fi
  cleanup_done=true
  trap - 0 1 2 15
  cleanup_status=0
  if [ -n "$restore_pid" ]; then
    kill "$restore_pid" 2>/dev/null || :
    wait "$restore_pid" 2>/dev/null || :
    restore_pid=
  fi
  if [ -n "$guardian_watch_pid" ]; then
    kill "$guardian_watch_pid" 2>/dev/null || :
    wait "$guardian_watch_pid" 2>/dev/null || :
    guardian_watch_pid=
  fi
  if [ "$restore_input_open" = true ]; then
    exec 3<&-
    restore_input_open=false
  fi
  if [ "$lease_input_open" = true ]; then
    exec 4>&-
    lease_input_open=false
  fi
  if [ "$lease_acquired" = true ] && [ -n "$lease_backend_pid" ]; then
    guardian_identity=$(guardian_identity_count || :)
    case "$guardian_identity" in
      0) ;;
      1)
        termination_result=$(
          printf "%s\n" \
            "SELECT pg_terminate_backend(pid, 5000)" \
            "FROM pg_stat_activity" \
            "WHERE pid=:'guardian_pid'::integer" \
            "  AND backend_start=:'guardian_backend_start'::timestamptz" \
            "  AND application_name=:'guardian_application_name'" \
            "  AND backend_type='client backend'" \
            "  AND datname='postgres'" \
            "  AND usename=current_user;" |
            psql \
              --username "$POSTGRES_USER" \
              --dbname postgres \
              --no-psqlrc \
              --set ON_ERROR_STOP=1 \
              --tuples-only \
              --no-align \
              --set guardian_pid="$lease_backend_pid" \
              --set guardian_backend_start="$lease_backend_start" \
              --set guardian_application_name="$guardian_application_name" \
              2>/dev/null || :
        )
        [ "$termination_result" = t ] || cleanup_status=1
        ;;
      *) cleanup_status=1 ;;
    esac
  fi
  if [ -n "$lease_pid" ]; then
    kill "$lease_pid" 2>/dev/null || :
    wait "$lease_pid" 2>/dev/null || :
    lease_pid=
  fi
  wait_for_lease_release || cleanup_status=1
  rm -rf "$lease_directory"
  return "$cleanup_status"
}

trap cleanup_lease 0
trap "exit 129" 1
trap "exit 130" 2
trap "exit 143" 15

PGAPPNAME="$guardian_application_name" psql \
  --username "$POSTGRES_USER" \
  --dbname postgres \
  --no-psqlrc \
  --tuples-only \
  --no-align \
  --set ON_ERROR_STOP=1 \
  <"$lease_control" >/dev/null &
lease_pid=$!
exec 4>"$lease_control"
lease_input_open=true
printf "%s\n" \
  "SELECT pg_advisory_lock($lease_key);" \
  "\\o $lease_ready" \
  "SELECT pg_backend_pid()::text || '|' || backend_start::text FROM pg_stat_activity WHERE pid=pg_backend_pid();" \
  "\\o /dev/null" >&4

attempt=0
while [ ! -s "$lease_ready" ]; do
  if ! kill -0 "$lease_pid" 2>/dev/null; then
    printf "%s\n" restore_database_lease_acquire_failed >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 300 ]; then
    printf "%s\n" restore_database_lease_acquire_timeout >&2
    exit 1
  fi
  sleep 0.1
done

guardian_record=$(cat "$lease_ready")
case "$guardian_record" in
  *'|'*)
    lease_backend_pid=${guardian_record%%|*}
    lease_backend_start=${guardian_record#*|}
    ;;
  *)
    printf "%s\n" restore_database_lease_identity_invalid >&2
    exit 1
    ;;
esac
case "$lease_backend_pid" in
  ""|*[!0-9]*)
    printf "%s\n" restore_database_lease_identity_invalid >&2
    exit 1
    ;;
esac
if [ -z "$lease_backend_start" ]; then
  printf "%s\n" restore_database_lease_identity_invalid >&2
  exit 1
fi
guardian_identity=$(guardian_identity_count)
if [ "$guardian_identity" != 1 ]; then
  printf "%s\n" restore_database_lease_identity_invalid >&2
  exit 1
fi
lease_acquired=true

pg_restore \
  --username "$POSTGRES_USER" \
  --dbname "$1" \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  <&3 4>&- &
restore_pid=$!
(
  exec 3<&- 4>&-
  while [ "$(guardian_identity_count || :)" = 1 ]; do
    sleep 0.1
  done
  kill "$restore_pid" 2>/dev/null || :
  rm -rf "$lease_directory"
) &
guardian_watch_pid=$!

restore_status=0
wait "$restore_pid" || restore_status=$?
restore_pid=
kill "$guardian_watch_pid" 2>/dev/null || :
wait "$guardian_watch_pid" 2>/dev/null || :
guardian_watch_pid=
if [ "$restore_status" -eq 0 ] &&
  [ "$(guardian_identity_count || :)" != 1 ]
then
  printf "%s\n" restore_database_lease_guardian_lost >&2
  restore_status=1
fi
if [ "$restore_status" -ne 0 ]; then
  exit "$restore_status"
fi

cleanup_lease
trap - 0 1 2 15
# AGENTOPS_RESTORE_GUARDIAN_SCRIPT_END
