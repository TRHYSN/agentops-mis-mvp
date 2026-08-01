#!/bin/sh
set -eu

REPOSITORY="geogejoy107-jpg/agentops-mis-mvp"
TAG=""
DO_INIT=false
DO_START=false
PORT=18878

usage() {
  cat <<'EOF'
Usage: install-agentops-mis-private-host.sh --tag <release-tag> [--init] [--start] [--port <loopback-port>]

Downloads one fixed AgentOps MIS Private Host release from GitHub, verifies its
published SHA-256, installs it, and optionally initializes/starts the loopback
Host. The local browser performs explicit Owner creation; Tailscale publication
and live Runtime startup remain separate explicit actions.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      TAG=$2
      shift 2
      ;;
    --init)
      DO_INIT=true
      shift
      ;;
    --start)
      DO_START=true
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      PORT=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[ -n "$TAG" ] || { usage >&2; exit 2; }
case "$TAG" in
  v[abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-]*) ;;
  *) echo "invalid release tag" >&2; exit 2 ;;
esac
VERSION=${TAG#v}
case "$VERSION" in
  ""|*[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-]*)
    echo "invalid release version" >&2
    exit 2
    ;;
esac
case "$PORT" in
  ""|*[!0123456789]*) echo "invalid loopback port" >&2; exit 2 ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "invalid loopback port" >&2
  exit 2
fi
if [ "$(uname -s)" != "Darwin" ] && [ "${AGENTOPS_INSTALLER_TEST_MODE:-}" != "1" ]; then
  echo "this unsigned preview installer supports macOS only" >&2
  exit 2
fi
command -v python3 >/dev/null 2>&1 || { echo "Python 3.10+ is required" >&2; exit 2; }
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required")
PY

TMPDIR_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/agentops-private-host-install.XXXXXX")
chmod 700 "$TMPDIR_ROOT"
trap 'rm -rf "$TMPDIR_ROOT"' EXIT HUP INT TERM

PREFIX="agentops-mis-private-host-$VERSION"
ARCHIVE="$PREFIX.tar.gz"
CHECKSUMS="$PREFIX.sha256.json"
PROVENANCE="$PREFIX.provenance.json"
ARCHIVE_PATH="$TMPDIR_ROOT/$ARCHIVE"
CHECKSUM_PATH="$TMPDIR_ROOT/$CHECKSUMS"
PROVENANCE_PATH="$TMPDIR_ROOT/$PROVENANCE"

fetch_asset() {
  asset=$1
  destination=$2
  maximum_bytes=$3
  if [ "${AGENTOPS_INSTALLER_TEST_MODE:-}" = "1" ]; then
    source_dir=${AGENTOPS_INSTALLER_TEST_RELEASE_DIR:-}
    [ -n "$source_dir" ] || { echo "test release directory is required" >&2; exit 2; }
    cp "$source_dir/$asset" "$destination"
    return
  fi
  command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 2; }
  url="https://github.com/$REPOSITORY/releases/download/$TAG/$asset"
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --silent --show-error \
    --connect-timeout 15 --retry 5 --retry-all-errors --retry-delay 2 \
    --max-filesize "$maximum_bytes" --output "$destination" "$url"
}

fetch_asset "$CHECKSUMS" "$CHECKSUM_PATH" 1048576
fetch_asset "$PROVENANCE" "$PROVENANCE_PATH" 1048576
fetch_asset "$ARCHIVE" "$ARCHIVE_PATH" 536870912

EXPECTED_COMMIT=$(python3 - "$CHECKSUM_PATH" "$ARCHIVE_PATH" "$PROVENANCE_PATH" "$0" "$VERSION" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

checksum_path, archive_path, provenance_path, bootstrap_path = map(Path, sys.argv[1:5])
expected_version = sys.argv[5]
checksums = json.loads(checksum_path.read_text(encoding="utf-8"))
provenance = json.loads(provenance_path.read_text(encoding="utf-8"))

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

for path in (archive_path, provenance_path, bootstrap_path):
    expected = checksums.get(path.name)
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("release checksum entry is missing or invalid")
    if digest(path) != expected:
        raise SystemExit("release asset SHA-256 mismatch")

commit = provenance.get("git_commit")
artifacts = provenance.get("artifacts")
if (
    provenance.get("schema_version") != 1
    or provenance.get("version") != expected_version
    or provenance.get("source_clean") is not True
    or not isinstance(commit, str)
    or not re.fullmatch(r"[0-9a-f]{40}", commit)
    or not isinstance(provenance.get("ui_tree_sha256"), str)
    or not re.fullmatch(r"[0-9a-f]{64}", provenance["ui_tree_sha256"])
    or not isinstance(artifacts, dict)
):
    raise SystemExit("release provenance is missing or invalid")
for path in (archive_path, bootstrap_path):
    if artifacts.get(path.name) != digest(path):
        raise SystemExit("release provenance artifact mismatch")
print(commit)
PY
)

EXTRACT_ROOT="$TMPDIR_ROOT/extracted"
mkdir "$EXTRACT_ROOT"
python3 - "$ARCHIVE_PATH" "$PREFIX" "$EXTRACT_ROOT" <<'PY'
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path, expected_root, destination = sys.argv[1:]
destination_path = Path(destination).resolve()
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("release archive is empty")
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
            raise SystemExit("unsafe release archive path")
        if member.name in seen or not (member.isdir() or member.isfile()):
            raise SystemExit("unsafe release archive member")
        seen.add(member.name)
    for member in members:
        path = PurePosixPath(member.name)
        target = destination_path.joinpath(*path.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True, mode=0o755)
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("release archive file is unreadable")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, member.mode & 0o777)
        with source, os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(member.mode & 0o777)
PY
BUNDLE_DIR="$EXTRACT_ROOT/$PREFIX"
[ -f "$BUNDLE_DIR/install.sh" ] || { echo "release installer is missing" >&2; exit 1; }
sh "$BUNDLE_DIR/install.sh"

BIN_DIR=${AGENTOPS_BIN_DIR:-"$HOME/.local/bin"}
AGENTOPS="$BIN_DIR/agentops"
[ -x "$AGENTOPS" ] || { echo "installed agentops command is missing" >&2; exit 1; }
VERSION_JSON=$("$AGENTOPS" host version)
python3 - "$VERSION" "$EXPECTED_COMMIT" "$VERSION_JSON" <<'PY'
import json
import sys

expected_version = sys.argv[1]
expected_commit = sys.argv[2]
payload = json.loads(sys.argv[3])
if (
    payload.get("packaged_install") is not True
    or payload.get("version") != expected_version
    or payload.get("git_commit") != expected_commit
):
    raise SystemExit("installed release provenance mismatch")
PY

HOST_HOME=${AGENTOPS_HOST_HOME:-"$HOME/.agentops/host"}
if [ "$DO_INIT" = true ]; then
  if [ -f "$HOST_HOME/config.json" ] && [ -f "$HOST_HOME/secrets.json" ]; then
    echo "AgentOps MIS Host is already initialized; preserving existing Host state."
  elif [ -e "$HOST_HOME/config.json" ] || [ -e "$HOST_HOME/secrets.json" ]; then
    echo "partial Host initialization detected; refusing to overwrite it" >&2
    exit 1
  else
    "$AGENTOPS" host init --port "$PORT" >/dev/null
    echo "AgentOps MIS Host initialized on literal loopback."
  fi
fi

if [ "$DO_START" = true ]; then
  "$AGENTOPS" host start --no-workers
fi

LOCAL_CONSOLE_URL="http://127.0.0.1:$PORT/workspace"

cat <<EOF
AgentOps MIS Private Host $VERSION is installed.
CLI: $AGENTOPS
Next: $AGENTOPS host status
Console: $LOCAL_CONSOLE_URL
CLI recovery: $AGENTOPS host bootstrap-owner --confirm
EOF

if [ "$DO_START" = true ] \
  && [ "${AGENTOPS_INSTALLER_TEST_MODE:-}" != "1" ] \
  && [ "${AGENTOPS_NO_AUTO_OPEN:-}" != "1" ]; then
  "$AGENTOPS" host open-console >/dev/null 2>&1 || true
fi
