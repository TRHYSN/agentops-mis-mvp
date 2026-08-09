#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

workspace_id=
username=
display_name=
password_stdin=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace-id|--username|--display-name)
      [ "$#" -ge 2 ] || fail "owner_init_argument_incomplete"
      case "$1" in
        --workspace-id) workspace_id=$2 ;;
        --username) username=$2 ;;
        --display-name) display_name=$2 ;;
      esac
      shift 2
      ;;
    --password-stdin)
      [ "$password_stdin" = false ] || fail "owner_password_stdin_duplicate"
      password_stdin=true
      shift
      ;;
    --password*|--pass*)
      fail "owner_password_argv_forbidden"
      ;;
    *)
      fail "owner_init_argument_invalid"
      ;;
  esac
done

[ -n "$workspace_id" ] || fail "owner_workspace_id_required"
[ -n "$username" ] || fail "owner_username_required"

password=
confirmation=
restore_tty() {
  if [ -n "${owner_tty_state:-}" ]; then
    stty "$owner_tty_state" < /dev/tty 2>/dev/null || true
    owner_tty_state=
  fi
  password=
  confirmation=
}
trap restore_tty EXIT HUP INT TERM

if [ "$password_stdin" = true ]; then
  IFS= read -r password || [ -n "$password" ] || fail "owner_password_stdin_empty"
  if IFS= read -r confirmation || [ -n "$confirmation" ]; then
    fail "owner_password_stdin_multiple_lines"
  fi
else
  [ -r /dev/tty ] && [ -w /dev/tty ] || fail "owner_interactive_tty_required"
  owner_tty_state=$(stty -g < /dev/tty) || fail "owner_interactive_tty_required"
  stty -echo < /dev/tty
  printf 'Owner password: ' > /dev/tty
  IFS= read -r password < /dev/tty || fail "owner_password_input_failed"
  printf '\nConfirm password: ' > /dev/tty
  IFS= read -r confirmation < /dev/tty || fail "owner_password_input_failed"
  printf '\n' > /dev/tty
  stty "$owner_tty_state" < /dev/tty
  owner_tty_state=
  [ "$password" = "$confirmation" ] || fail "owner_password_confirmation_mismatch"
fi
[ -n "$password" ] || fail "owner_password_empty"

release_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
compose_file="${release_root}/deploy/byoc/compose.yaml"
env_file="${release_root}/deploy/byoc/.env"
[ -f "$compose_file" ] || fail "owner_release_compose_missing"
[ -f "$env_file" ] || fail "owner_install_required"

owner_arguments=(
  --workspace-id "$workspace_id"
  --username "$username"
)
if [ -n "$display_name" ]; then
  owner_arguments+=(--display-name "$display_name")
fi
owner_arguments+=(--password-stdin)

printf '%s\n' "$password" | docker compose \
  --env-file "$env_file" \
  -f "$compose_file" \
  --profile owner-bootstrap \
  run --rm --no-deps --no-build --pull never -T owner-bootstrap \
  node /usr/local/lib/agentops/owner-bootstrap-entrypoint.mjs \
  "${owner_arguments[@]}"

restore_tty
trap - EXIT HUP INT TERM
