"""Crash-recoverable publication of complete campaign evidence generations.

SQLite and a filesystem cannot share one atomic transaction.  This module keeps
the final campaign tree behind a durable publication journal while the caller
records an outbox row in the same SQLite transaction as the MIS facts.  A
recovery pass can therefore choose exactly one outcome after process death:
restore the previous tree when the outbox did not commit, or finish the staged
tree when it did.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO

from open_cekura.domain.ids import stable_id

from .manifest import (
    EvidenceError,
    EvidenceInputError,
    EvidencePathError,
    EvidenceWriteError,
    absolute_safe_root,
    canonical_json_bytes,
    is_symlink_or_reparse,
    validate_path_component,
)


PUBLICATION_ROOT_NAME = ".open-cekura-publications"
PUBLICATION_JOURNAL_NAME = "publication.json"
PUBLICATION_SCHEMA_VERSION = 2
MAX_PUBLICATION_FILES = 10_000
MAX_PUBLICATION_BYTES = 2 * 1024 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


class PublicationError(EvidenceError):
    """A campaign generation could not be safely published or recovered."""


@dataclass(slots=True)
class _PublicationLease:
    path: Path
    stream: BinaryIO
    token: str


_LEASE_GUARD = Lock()
_ACTIVE_LEASES: dict[Path, _PublicationLease] = {}


@dataclass(frozen=True, slots=True)
class CampaignPublication:
    artifact_root: Path
    authority_id: str
    workspace_id: str
    campaign_id: str
    publication_id: str
    lease_token: str | None
    gate_id: str
    slot: Path
    stage_root: Path
    stage_campaign: Path
    backup_campaign: Path
    final_campaign: Path
    had_final: bool
    previous_tree_sha256: str | None
    expected_tree_sha256: str | None = None

    @property
    def journal_path(self) -> Path:
        return self.slot / PUBLICATION_JOURNAL_NAME


def publication_slot(
    root: str | Path, *, workspace_id: str, campaign_id: str
) -> Path:
    """Return the single recovery slot for one final campaign namespace."""

    validate_path_component(workspace_id, label="workspace_id")
    validate_path_component(campaign_id, label="campaign_id")
    artifact_root = absolute_safe_root(root)
    # The final evidence path is ``artifact_root / campaign_id``.  The lock and
    # recovery slot must use that exact namespace too; including workspace here
    # would permit two writers to target one final directory concurrently.
    slot_id = stable_id("ocpubslot", campaign_id.casefold())
    return artifact_root / PUBLICATION_ROOT_NAME / slot_id


def begin_publication(
    root: str | Path,
    *,
    authority_id: str,
    workspace_id: str,
    campaign_id: str,
    gate_id: str,
    publication_id: str,
    expected_previous_tree_sha256: str | None,
) -> CampaignPublication:
    """Create a private complete-generation staging area.

    The caller must first recover any existing slot while holding its SQLite
    writer lock.  Existing campaign content is copied without following links;
    the caller then updates and independently verifies the staged generation.
    """

    validate_path_component(authority_id, label="authority_id")
    validate_path_component(workspace_id, label="workspace_id")
    validate_path_component(campaign_id, label="campaign_id")
    validate_path_component(gate_id, label="gate_id")
    validate_path_component(publication_id, label="publication_id")
    if (
        expected_previous_tree_sha256 is not None
        and _SHA256.fullmatch(expected_previous_tree_sha256) is None
    ):
        raise EvidenceInputError("previous campaign tree hash is invalid")
    artifact_root = absolute_safe_root(root)
    try:
        artifact_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvidencePathError("artifact root cannot be created") from exc
    artifact_root = absolute_safe_root(artifact_root)
    if is_symlink_or_reparse(artifact_root) or not artifact_root.is_dir():
        raise EvidencePathError("artifact root must be a real directory")

    publications = _safe_publication_root(artifact_root, create=True)
    slot = publication_slot(
        artifact_root, workspace_id=workspace_id, campaign_id=campaign_id
    )
    if slot.parent != publications:
        raise EvidencePathError("publication slot escapes its private root")
    lease_token = _acquire_publication_lease(
        slot,
        artifact_root=artifact_root,
    )
    slot_created = False
    try:
        if slot.exists():
            raise PublicationError("campaign has a pending evidence publication")
        # The OS lease above is the cross-process lock.  This directory is the
        # durable recovery state and may outlive a crashed process.
        slot.mkdir()
        slot_created = True
        if is_symlink_or_reparse(slot) or absolute_safe_root(slot) != slot:
            raise EvidencePathError("publication recovery slot is unsafe")
        stage_root = slot / "stage"
        stage_campaign = stage_root / campaign_id
        backup_campaign = slot / "backup" / campaign_id
        final_campaign = artifact_root / campaign_id
        if is_symlink_or_reparse(final_campaign):
            raise EvidencePathError("campaign directory is a symlink or reparse point")
        had_final = final_campaign.exists()
        if had_final and not final_campaign.is_dir():
            raise EvidencePathError("campaign evidence path is not a directory")
        if had_final != (expected_previous_tree_sha256 is not None):
            raise PublicationError("campaign generation changed before publication lock")
        if (
            had_final
            and campaign_tree_sha256(final_campaign)
            != expected_previous_tree_sha256
        ):
            raise PublicationError("campaign generation changed before publication lock")
        stage_root.mkdir()
        if had_final:
            shutil.copytree(
                final_campaign,
                stage_campaign,
                symlinks=True,
                copy_function=_copy_regular_file,
            )
    except (EvidenceError, OSError, shutil.Error) as exc:
        if slot_created:
            _best_effort_remove_slot(slot, artifact_root=artifact_root)
        _release_publication_lease(slot, lease_token)
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceWriteError("campaign staging generation cannot be created") from exc
    except BaseException:
        if slot_created:
            _best_effort_remove_slot(slot, artifact_root=artifact_root)
        _release_publication_lease(slot, lease_token)
        raise
    return CampaignPublication(
        artifact_root=artifact_root,
        authority_id=authority_id,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        publication_id=publication_id,
        lease_token=lease_token,
        gate_id=gate_id,
        slot=slot,
        stage_root=stage_root,
        stage_campaign=stage_campaign,
        backup_campaign=backup_campaign,
        final_campaign=final_campaign,
        had_final=had_final,
        previous_tree_sha256=expected_previous_tree_sha256,
    )


def seal_publication(publication: CampaignPublication) -> CampaignPublication:
    """Hash the verified staged tree and durably write its recovery journal."""

    _require_publication(publication)
    _require_active_lease(publication)
    expected_hash = campaign_tree_sha256(publication.stage_campaign)
    sealed = replace(publication, expected_tree_sha256=expected_hash)
    payload = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "publication_id": sealed.publication_id,
        "authority_id": sealed.authority_id,
        "workspace_id": sealed.workspace_id,
        "campaign_id": sealed.campaign_id,
        "gate_id": sealed.gate_id,
        "had_final": sealed.had_final,
        "previous_tree_sha256": sealed.previous_tree_sha256,
        "expected_tree_sha256": expected_hash,
    }
    _atomic_write(sealed.journal_path, canonical_json_bytes(payload))
    return sealed


def stage_reference_campaign(
    publication: CampaignPublication, reference_campaign_id: str
) -> None:
    """Copy one verified sibling campaign needed for recursive gate verification."""

    _require_publication(publication)
    _require_active_lease(publication)
    validate_path_component(reference_campaign_id, label="reference_campaign_id")
    if reference_campaign_id == publication.campaign_id:
        return
    source = publication.artifact_root / reference_campaign_id
    destination = publication.stage_root / reference_campaign_id
    if (
        not source.is_dir()
        or is_symlink_or_reparse(source)
        or destination.exists()
        or is_symlink_or_reparse(destination)
    ):
        raise EvidencePathError("referenced campaign cannot be safely staged")
    try:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            copy_function=_copy_regular_file,
        )
    except (EvidenceError, OSError, shutil.Error) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceWriteError("referenced campaign cannot be staged") from exc


def load_pending_publication(
    root: str | Path,
    *,
    workspace_id: str,
    campaign_id: str,
    claim: bool = False,
) -> CampaignPublication | None:
    """Load and strictly validate a pending publication journal.

    ``claim=True`` retains the cross-process lease for recovery mutations.  A
    normal inspection releases it before returning.  An unsealed slot found
    after the lease is acquired cannot have a live owner and is therefore a
    stale pre-journal stage that is safe to discard.
    """

    artifact_root = absolute_safe_root(root)
    publications = _safe_publication_root(artifact_root, create=False)
    if not publications.exists():
        return None
    slot = publication_slot(
        artifact_root, workspace_id=workspace_id, campaign_id=campaign_id
    )
    if slot.parent != publications:
        raise EvidencePathError("publication slot escapes its private root")
    lease_token = _acquire_publication_lease(slot, artifact_root=artifact_root)
    retain_lease = False
    try:
        if not slot.exists():
            return None
        if is_symlink_or_reparse(slot) or not slot.is_dir():
            raise EvidencePathError("publication recovery slot is unsafe")
        if absolute_safe_root(slot) != slot:
            raise EvidencePathError("publication recovery slot is unsafe")
        journal_path = slot / PUBLICATION_JOURNAL_NAME
        if not journal_path.exists():
            _remove_slot(slot, artifact_root=artifact_root)
            return None
        if is_symlink_or_reparse(journal_path) or not journal_path.is_file():
            raise EvidencePathError("publication journal is unsafe")
        try:
            content = journal_path.read_bytes()
            if (
                len(content) > 64 * 1024
                or canonical_json_bytes(json.loads(content)) != content
            ):
                raise ValueError
            payload: Any = json.loads(content)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise PublicationError(
                "publication journal violates its canonical contract"
            ) from exc
        required = {
            "schema_version",
            "publication_id",
            "authority_id",
            "workspace_id",
            "campaign_id",
            "gate_id",
            "had_final",
            "previous_tree_sha256",
            "expected_tree_sha256",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != required
            or payload.get("schema_version") != PUBLICATION_SCHEMA_VERSION
            or payload.get("workspace_id") != workspace_id
            or payload.get("campaign_id") != campaign_id
            or type(payload.get("had_final")) is not bool
            or not isinstance(payload.get("publication_id"), str)
            or not isinstance(payload.get("authority_id"), str)
            or not isinstance(payload.get("gate_id"), str)
            or not isinstance(payload.get("expected_tree_sha256"), str)
            or _SHA256.fullmatch(payload["expected_tree_sha256"]) is None
        ):
            raise PublicationError("publication journal facts are invalid")
        validate_path_component(payload["publication_id"], label="publication_id")
        validate_path_component(payload["authority_id"], label="authority_id")
        validate_path_component(payload["gate_id"], label="gate_id")
        previous_tree_sha256 = payload["previous_tree_sha256"]
        if (
            payload["had_final"]
            and (
                not isinstance(previous_tree_sha256, str)
                or _SHA256.fullmatch(previous_tree_sha256) is None
            )
        ) or (not payload["had_final"] and previous_tree_sha256 is not None):
            raise PublicationError(
                "publication journal previous generation is invalid"
            )
        pending = CampaignPublication(
            artifact_root=artifact_root,
            authority_id=payload["authority_id"],
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            publication_id=payload["publication_id"],
            lease_token=lease_token if claim else None,
            gate_id=payload["gate_id"],
            slot=slot,
            stage_root=slot / "stage",
            stage_campaign=slot / "stage" / campaign_id,
            backup_campaign=slot / "backup" / campaign_id,
            final_campaign=artifact_root / campaign_id,
            had_final=payload["had_final"],
            previous_tree_sha256=previous_tree_sha256,
            expected_tree_sha256=payload["expected_tree_sha256"],
        )
        retain_lease = claim
        return pending
    finally:
        if not retain_lease:
            _release_publication_lease(slot, lease_token)


def swap_publication_to_final(publication: CampaignPublication) -> None:
    """Swap the sealed generation into the contract path before DB commit."""

    _require_sealed(publication)
    if campaign_tree_sha256(publication.stage_campaign) != publication.expected_tree_sha256:
        raise PublicationError("staged campaign changed after publication was sealed")
    if publication.backup_campaign.exists():
        raise PublicationError("publication backup path is already occupied")
    try:
        if publication.had_final:
            if (
                not publication.final_campaign.is_dir()
                or is_symlink_or_reparse(publication.final_campaign)
            ):
                raise EvidencePathError("current campaign path is unavailable or unsafe")
            publication.backup_campaign.parent.mkdir(parents=True)
            os.replace(publication.final_campaign, publication.backup_campaign)
        elif publication.final_campaign.exists():
            raise PublicationError("new campaign destination became occupied")
        os.replace(publication.stage_campaign, publication.final_campaign)
        _best_effort_directory_fsync(publication.artifact_root)
    except EvidenceError:
        raise
    except OSError as exc:
        raise EvidenceWriteError("campaign generation swap failed") from exc


def rollback_publication(publication: CampaignPublication) -> None:
    """Restore the previous generation, retaining recovery files for verification."""

    _require_sealed(publication)
    try:
        if publication.had_final and publication.backup_campaign.exists():
            if (
                campaign_tree_sha256(publication.backup_campaign)
                != publication.previous_tree_sha256
            ):
                raise PublicationError("publication backup generation hash changed")
            if publication.final_campaign.exists():
                if (
                    campaign_tree_sha256(publication.final_campaign)
                    != publication.expected_tree_sha256
                ):
                    raise PublicationError(
                        "publication rollback found a foreign final generation"
                    )
                aborted = publication.slot / "aborted" / publication.campaign_id
                aborted.parent.mkdir(parents=True, exist_ok=True)
                if aborted.exists():
                    raise PublicationError("publication abort path is occupied")
                os.replace(publication.final_campaign, aborted)
            os.replace(publication.backup_campaign, publication.final_campaign)
        elif (
            not publication.had_final
            and not publication.stage_campaign.exists()
            and publication.final_campaign.exists()
        ):
            if (
                campaign_tree_sha256(publication.final_campaign)
                != publication.expected_tree_sha256
            ):
                raise PublicationError(
                    "publication rollback found a foreign final generation"
                )
            aborted = publication.slot / "aborted" / publication.campaign_id
            aborted.parent.mkdir(parents=True, exist_ok=True)
            if aborted.exists():
                raise PublicationError("publication abort path is occupied")
            os.replace(publication.final_campaign, aborted)
        _best_effort_directory_fsync(publication.artifact_root)
    except EvidenceError:
        raise
    except OSError as exc:
        raise PublicationError("rolled-back publication could not be restored") from exc


def finish_committed_publication(publication: CampaignPublication) -> None:
    """Require the committed generation at final, retaining recovery files."""

    _require_sealed(publication)
    if not publication.final_campaign.exists() and publication.stage_campaign.exists():
        try:
            os.replace(publication.stage_campaign, publication.final_campaign)
        except OSError as exc:
            raise PublicationError("committed campaign could not be promoted") from exc
    elif publication.final_campaign.exists() and publication.stage_campaign.exists():
        if (
            campaign_tree_sha256(publication.final_campaign)
            != publication.expected_tree_sha256
        ):
            raise PublicationError("committed campaign destination is occupied")
    if campaign_tree_sha256(publication.final_campaign) != publication.expected_tree_sha256:
        raise PublicationError("committed campaign generation hash does not match outbox")


def cleanup_publication_recovery_files(publication: CampaignPublication) -> None:
    """Remove a committed publication's private stage/backup after DB finalization."""

    _require_sealed(publication)
    try:
        _remove_slot(publication.slot, artifact_root=publication.artifact_root)
    finally:
        release_publication_claim(publication)


def discard_unsealed_publication(publication: CampaignPublication) -> None:
    """Remove a private stage that never acquired a durable journal."""

    _require_publication(publication)
    _require_active_lease(publication)
    if publication.journal_path.exists():
        raise PublicationError("sealed publication must be recovered, not discarded")
    try:
        _remove_slot(publication.slot, artifact_root=publication.artifact_root)
    finally:
        release_publication_claim(publication)


def release_publication_claim(publication: CampaignPublication) -> None:
    """Release this process's lease without changing durable recovery state.

    Service orchestration uses this at transaction boundaries before a fresh
    recovery pass claims the journal.  Releasing an already released claim is
    harmless; attempting to release a different live owner's token fails.
    """

    if not isinstance(publication, CampaignPublication):
        raise TypeError("publication must be CampaignPublication")
    if publication.lease_token is None:
        return
    expected_slot = publication_slot(
        publication.artifact_root,
        workspace_id=publication.workspace_id,
        campaign_id=publication.campaign_id,
    )
    if publication.slot != expected_slot:
        raise EvidencePathError("publication slot identity changed")
    _release_publication_lease(publication.slot, publication.lease_token)


def campaign_tree_sha256(campaign_path: Path) -> str:
    """Hash a real campaign tree by relative path and streamed file bytes."""

    path = Path(os.path.abspath(os.fspath(campaign_path)))
    if is_symlink_or_reparse(path) or not path.is_dir():
        raise EvidencePathError("campaign generation must be a real directory")
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    try:
        for current, directory_names, file_names in os.walk(path, followlinks=False):
            current_path = Path(current)
            directory_names.sort()
            file_names.sort()
            for name in directory_names:
                child = current_path / name
                if is_symlink_or_reparse(child) or not child.is_dir():
                    raise EvidencePathError("campaign generation contains an unsafe directory")
            for name in file_names:
                child = current_path / name
                metadata = child.lstat()
                if is_symlink_or_reparse(child) or not stat.S_ISREG(metadata.st_mode):
                    raise EvidencePathError("campaign generation contains an unsafe file")
                file_count += 1
                if file_count > MAX_PUBLICATION_FILES:
                    raise EvidenceInputError("campaign generation exceeds publication limits")
                flags = os.O_RDONLY
                flags |= getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOINHERIT", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor: int | None = None
                try:
                    descriptor = os.open(child, flags)
                    opened_metadata = os.fstat(descriptor)
                    current_metadata = child.lstat()
                    if (
                        not stat.S_ISREG(opened_metadata.st_mode)
                        or not os.path.samestat(metadata, opened_metadata)
                        or is_symlink_or_reparse(child)
                        or not stat.S_ISREG(current_metadata.st_mode)
                        or not os.path.samestat(
                            opened_metadata,
                            current_metadata,
                        )
                    ):
                        raise EvidencePathError(
                            "campaign generation file identity changed before hashing"
                        )
                    if (
                        total_bytes + opened_metadata.st_size
                        > MAX_PUBLICATION_BYTES
                    ):
                        raise EvidenceInputError(
                            "campaign generation exceeds publication limits"
                        )
                    if not _same_file_snapshot(
                        metadata,
                        opened_metadata,
                    ) or not _same_file_snapshot(
                        opened_metadata,
                        current_metadata,
                    ):
                        raise EvidencePathError(
                            "campaign generation file changed before hashing"
                        )
                    relative = child.relative_to(path).as_posix().encode("utf-8")
                    digest.update(len(relative).to_bytes(4, "big"))
                    digest.update(relative)
                    digest.update(opened_metadata.st_size.to_bytes(8, "big"))
                    bytes_read = 0
                    with os.fdopen(descriptor, "rb", closefd=True) as stream:
                        descriptor = None
                        while chunk := stream.read(1024 * 1024):
                            bytes_read += len(chunk)
                            total_bytes += len(chunk)
                            if total_bytes > MAX_PUBLICATION_BYTES:
                                raise EvidenceInputError(
                                    "campaign generation exceeds publication limits"
                                )
                            digest.update(chunk)
                        final_metadata = os.fstat(stream.fileno())
                    path_metadata = child.lstat()
                    if (
                        bytes_read != opened_metadata.st_size
                        or not _same_file_snapshot(
                            opened_metadata,
                            final_metadata,
                        )
                        or is_symlink_or_reparse(child)
                        or not stat.S_ISREG(path_metadata.st_mode)
                        or not os.path.samestat(final_metadata, path_metadata)
                        or not _same_file_snapshot(final_metadata, path_metadata)
                    ):
                        raise EvidencePathError(
                            "campaign generation file changed while hashing"
                        )
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
    except EvidenceError:
        raise
    except (OSError, UnicodeError) as exc:
        raise EvidenceWriteError("campaign generation cannot be hashed") from exc
    return digest.hexdigest()


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return bool(
        os.path.samestat(left, right)
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _copy_regular_file(source: str, destination: str) -> str:
    source_path = Path(source)
    if is_symlink_or_reparse(source_path) or not source_path.is_file():
        raise EvidencePathError("campaign copy source is not a regular file")
    shutil.copyfile(source_path, destination, follow_symlinks=False)
    return destination


def _atomic_write(destination: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        _best_effort_directory_fsync(destination.parent)
    except OSError as exc:
        raise EvidenceWriteError("publication journal cannot be written") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_publication_root(artifact_root: Path, *, create: bool) -> Path:
    root = absolute_safe_root(artifact_root)
    publications = root / PUBLICATION_ROOT_NAME
    if create:
        try:
            publications.mkdir(exist_ok=True)
        except OSError as exc:
            raise EvidenceWriteError(
                "publication private root cannot be created"
            ) from exc
    if not publications.exists():
        return publications
    safe = absolute_safe_root(publications)
    if (
        safe != publications
        or is_symlink_or_reparse(publications)
        or not publications.is_dir()
    ):
        raise EvidencePathError("publication private root is unsafe")
    return publications


def _require_publication(publication: CampaignPublication) -> None:
    if not isinstance(publication, CampaignPublication):
        raise TypeError("publication must be CampaignPublication")
    expected_slot = publication_slot(
        publication.artifact_root,
        workspace_id=publication.workspace_id,
        campaign_id=publication.campaign_id,
    )
    expected_paths = (
        expected_slot,
        expected_slot / "stage",
        expected_slot / "stage" / publication.campaign_id,
        expected_slot / "backup" / publication.campaign_id,
        publication.artifact_root / publication.campaign_id,
    )
    actual_paths = (
        publication.slot,
        publication.stage_root,
        publication.stage_campaign,
        publication.backup_campaign,
        publication.final_campaign,
    )
    if (
        actual_paths != expected_paths
        or is_symlink_or_reparse(publication.slot)
        or absolute_safe_root(publication.slot) != publication.slot
    ):
        raise EvidencePathError("publication slot identity changed")
    validate_path_component(publication.authority_id, label="authority_id")
    if publication.had_final != (publication.previous_tree_sha256 is not None):
        raise PublicationError("publication previous generation contract changed")
    if (
        publication.previous_tree_sha256 is not None
        and _SHA256.fullmatch(publication.previous_tree_sha256) is None
    ):
        raise PublicationError("publication previous generation hash is invalid")


def _require_sealed(publication: CampaignPublication) -> None:
    _require_publication(publication)
    _require_active_lease(publication)
    if (
        publication.expected_tree_sha256 is None
        or _SHA256.fullmatch(publication.expected_tree_sha256) is None
        or not publication.journal_path.is_file()
        or is_symlink_or_reparse(publication.journal_path)
    ):
        raise PublicationError("publication is not durably sealed")


def _require_active_lease(publication: CampaignPublication) -> None:
    if publication.lease_token is None:
        raise PublicationError("publication does not hold an active lease")
    key = _publication_lease_key(publication.slot)
    with _LEASE_GUARD:
        lease = _ACTIVE_LEASES.get(key)
        if lease is None or lease.token != publication.lease_token:
            raise PublicationError("publication does not hold an active lease")


def _publication_lease_key(slot: Path) -> Path:
    return Path(os.path.abspath(os.fspath(slot)))


def _publication_lock_path(slot: Path) -> Path:
    return slot.with_name(f"{slot.name}.lock")


def _acquire_publication_lease(slot: Path, *, artifact_root: Path) -> str:
    """Acquire a process-lifetime OS lock for one final campaign namespace."""

    root = absolute_safe_root(artifact_root)
    publications = _safe_publication_root(root, create=True)
    key = _publication_lease_key(slot)
    lock_path = _publication_lock_path(key)
    if key.parent != publications or lock_path.parent != publications:
        raise EvidencePathError("publication lease escapes its private root")
    token = stable_id("oclease", os.urandom(32).hex())
    with _LEASE_GUARD:
        if key in _ACTIVE_LEASES:
            raise PublicationError("campaign has an active pending evidence publication")
        if lock_path.exists() and (
            is_symlink_or_reparse(lock_path) or not lock_path.is_file()
        ):
            raise EvidencePathError("publication lease file is unsafe")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        stream: BinaryIO | None = None
        locked = False
        try:
            descriptor = os.open(lock_path, flags, 0o666)
            opened_stat = os.fstat(descriptor)
            path_stat = lock_path.lstat()
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or not stat.S_ISREG(path_stat.st_mode)
                or not os.path.samestat(opened_stat, path_stat)
                or is_symlink_or_reparse(lock_path)
            ):
                raise EvidencePathError("publication lease file is unsafe")
            stream = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = None
            if opened_stat.st_size == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            try:
                _lock_publication_stream(stream)
            except OSError as exc:
                raise PublicationError(
                    "campaign has an active pending evidence publication"
                ) from exc
            locked = True
            # Recheck the directory entry after taking the lock so replacing
            # the path cannot make us protect an orphaned inode.
            if (
                is_symlink_or_reparse(lock_path)
                or not os.path.samestat(os.fstat(stream.fileno()), lock_path.lstat())
            ):
                raise EvidencePathError("publication lease file identity changed")
            _ACTIVE_LEASES[key] = _PublicationLease(
                path=lock_path,
                stream=stream,
                token=token,
            )
            return token
        except PublicationError:
            raise
        except EvidenceError:
            raise
        except OSError as exc:
            raise EvidenceWriteError("campaign publication lease failed") from exc
        finally:
            if key not in _ACTIVE_LEASES:
                if locked and stream is not None:
                    _unlock_publication_stream(stream)
                if stream is not None:
                    stream.close()
                elif descriptor is not None:
                    os.close(descriptor)


def _release_publication_lease(slot: Path, token: str) -> None:
    key = _publication_lease_key(slot)
    with _LEASE_GUARD:
        lease = _ACTIVE_LEASES.get(key)
        if lease is None:
            return
        if lease.token != token:
            raise PublicationError("publication lease belongs to a different owner")
        del _ACTIVE_LEASES[key]
        try:
            _unlock_publication_stream(lease.stream)
        finally:
            lease.stream.close()


def _lock_publication_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_publication_stream(stream: BinaryIO) -> None:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _remove_slot(slot: Path, *, artifact_root: Path) -> None:
    root = absolute_safe_root(artifact_root)
    publications = _safe_publication_root(root, create=False)
    if not publications.exists():
        return
    resolved_slot = absolute_safe_root(slot)
    try:
        resolved_slot.relative_to(publications)
    except ValueError as exc:
        raise EvidencePathError("publication cleanup target escapes its private root") from exc
    if (
        resolved_slot == publications
        or Path(os.path.abspath(os.fspath(slot))) != resolved_slot
        or is_symlink_or_reparse(resolved_slot)
    ):
        raise EvidencePathError("publication cleanup target is unsafe")
    try:
        shutil.rmtree(resolved_slot)
        if publications.exists() and not any(publications.iterdir()):
            publications.rmdir()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PublicationError("publication recovery files cannot be removed") from exc


def _best_effort_remove_slot(slot: Path, *, artifact_root: Path) -> None:
    try:
        _remove_slot(slot, artifact_root=artifact_root)
    except EvidenceError:
        pass


def _best_effort_directory_fsync(directory: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


__all__ = [
    "CampaignPublication",
    "PublicationError",
    "begin_publication",
    "campaign_tree_sha256",
    "cleanup_publication_recovery_files",
    "discard_unsealed_publication",
    "finish_committed_publication",
    "load_pending_publication",
    "publication_slot",
    "release_publication_claim",
    "rollback_publication",
    "seal_publication",
    "stage_reference_campaign",
    "swap_publication_to_final",
]
