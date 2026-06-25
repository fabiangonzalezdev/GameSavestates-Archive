#!/usr/bin/env python3
"""
Sync Nintendo Switch JKSV backups into this archive and Eden.

Default mode is a preview. Pass --apply to write files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


TITLE_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
BACKUP_TIME_RE = re.compile(
    r"(?P<year>20\d{2})[.\-](?P<month>\d{1,2})[.\-](?P<day>\d{1,2})"
    r"\s*@\s*"
    r"(?P<hour>\d{1,2})[.:](?P<minute>\d{2})(?:[.:](?P<second>\d{2}))?"
)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class BackupCandidate:
    path: Path
    kind: str
    parsed_at: datetime | None
    mtime: datetime

    @property
    def selected_at(self) -> datetime:
        return self.parsed_at or self.mtime


@dataclass(frozen=True)
class GameInfo:
    folder_name: str
    title_id: str | None
    archive_name: str
    aliases: tuple[str, ...]


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from None


def resolve_path(repo_root: Path, value: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def safe_folder_name(value: str) -> str:
    value = INVALID_FILENAME_RE.sub("-", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return value or "Unknown Game"


def parse_backup_time(name: str) -> datetime | None:
    match = BACKUP_TIME_RE.search(name)
    if not match:
        return None

    parts = {key: int(value) for key, value in match.groupdict(default="0").items()}
    try:
        return datetime(
            parts["year"],
            parts["month"],
            parts["day"],
            parts["hour"],
            parts["minute"],
            parts["second"],
        )
    except ValueError:
        return None


def has_files(path: Path) -> bool:
    return any(child.is_file() for child in path.rglob("*"))


def list_backup_candidates(game_dir: Path) -> list[BackupCandidate]:
    candidates: list[BackupCandidate] = []
    for child in game_dir.iterdir():
        if child.name.startswith("."):
            continue

        kind: str | None = None
        if child.is_file() and child.suffix.lower() == ".zip":
            kind = "zip"
        elif child.is_dir() and has_files(child):
            kind = "dir"

        if not kind:
            continue

        stat = child.stat()
        candidates.append(
            BackupCandidate(
                path=child,
                kind=kind,
                parsed_at=parse_backup_time(child.name),
                mtime=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    return candidates


def select_latest_backup(candidates: list[BackupCandidate]) -> BackupCandidate | None:
    if not candidates:
        return None

    trusted = [candidate for candidate in candidates if candidate.parsed_at]
    pool = trusted or candidates
    return max(
        pool,
        key=lambda candidate: (
            candidate.selected_at,
            1 if candidate.kind == "zip" else 0,
            candidate.mtime,
            candidate.path.name.lower(),
        ),
    )


def load_games(config: dict[str, Any]) -> tuple[dict[str, GameInfo], dict[str, str]]:
    games_by_key: dict[str, GameInfo] = {}
    names_by_title_id: dict[str, str] = {}

    for folder_name, raw in config.get("games", {}).items():
        if isinstance(raw, str):
            raw = {"title_id": raw}
        if not isinstance(raw, dict):
            continue

        title_id = raw.get("title_id")
        if isinstance(title_id, str):
            title_id = title_id.upper()
        else:
            title_id = None

        archive_name = str(raw.get("archive_name") or raw.get("name") or folder_name)
        aliases = tuple(str(item) for item in raw.get("aliases", []))
        info = GameInfo(folder_name, title_id, archive_name, aliases)

        for key in (folder_name, archive_name, *aliases):
            games_by_key[normalize_name(key)] = info

        if title_id:
            games_by_key[title_id.lower()] = info
            names_by_title_id[title_id] = archive_name

    return games_by_key, names_by_title_id


def resolve_game_info(
    folder_name: str,
    games_by_key: dict[str, GameInfo],
    names_by_title_id: dict[str, str],
) -> GameInfo:
    if TITLE_ID_RE.match(folder_name):
        title_id = folder_name.upper()
        return GameInfo(
            folder_name=folder_name,
            title_id=title_id,
            archive_name=names_by_title_id.get(title_id, title_id),
            aliases=(),
        )

    info = games_by_key.get(normalize_name(folder_name))
    if info:
        return info

    return GameInfo(folder_name=folder_name, title_id=None, archive_name=folder_name, aliases=())


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for child in path.rglob("*") if child.is_file())


def safe_extract_zip(zip_path: Path, target: Path) -> None:
    target_resolved = target.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_target = (target / member.filename).resolve()
            if not member_target.is_relative_to(target_resolved):
                raise ValueError(f"Unsafe zip member path: {member.filename}")
        archive.extractall(target)


def payload_root_for_extracted(root: Path, backup_name: str) -> Path:
    entries = list(root.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        only = entries[0]
        if normalize_name(only.name) in normalize_name(backup_name):
            return only
    return root


def file_needs_copy(source: Path, target: Path) -> bool:
    if not target.exists():
        return True
    source_stat = source.stat()
    target_stat = target.stat()
    if source_stat.st_size != target_stat.st_size:
        return True
    return int(source_stat.st_mtime) != int(target_stat.st_mtime)


def copy_tree(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
    replace_target: bool = False,
    delete_extra: bool = False,
) -> dict[str, int]:
    copied = 0
    skipped = 0
    deleted = 0
    created_dirs = 0

    if replace_target and target_root.exists():
        deleted += count_files(target_root)
        if not dry_run:
            shutil.rmtree(target_root)

    if not dry_run:
        target_root.mkdir(parents=True, exist_ok=True)
    elif not target_root.exists():
        created_dirs += 1

    source_paths = {Path(".")}
    for source in source_root.rglob("*"):
        relative = source.relative_to(source_root)
        source_paths.add(relative)
        target = target_root / relative

        if source.is_dir():
            if not target.exists():
                created_dirs += 1
                if not dry_run:
                    target.mkdir(parents=True, exist_ok=True)
            continue

        if file_needs_copy(source, target):
            copied += 1
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        else:
            skipped += 1

    if delete_extra and target_root.exists() and not replace_target:
        for target in sorted(target_root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            relative = target.relative_to(target_root)
            if relative in source_paths:
                continue
            deleted += 1 if target.is_file() else 0
            if not dry_run:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()

    return {
        "copied": copied,
        "skipped": skipped,
        "deleted": deleted,
        "created_dirs": created_dirs,
    }


def choose_eden_user(eden_root: Path, configured_user: str | None) -> Path | None:
    if configured_user:
        return eden_root / configured_user

    users = [path for path in eden_root.iterdir() if path.is_dir()] if eden_root.exists() else []
    if not users:
        return None

    return max(users, key=lambda path: sum(1 for child in path.iterdir() if child.is_dir()))


def write_metadata(path: Path, metadata: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def backup_eden_destination(
    eden_dest: Path,
    backup_root: Path,
    title_id: str,
    archive_name: str,
    *,
    dry_run: bool,
) -> Path | None:
    if not eden_dest.exists():
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"{stamp}_{title_id}_{safe_folder_name(archive_name)}"
    target = backup_root / backup_name

    if dry_run:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(eden_dest, target)
    return target


def print_copy_result(prefix: str, target: Path, result: dict[str, int], dry_run: bool) -> None:
    mode = "preview" if dry_run else "done"
    print(
        f"  {prefix} [{mode}]: {target} "
        f"(copy {result['copied']}, skip {result['skipped']}, delete {result['deleted']})"
    )


def should_include_game(game_dir: Path, filters: list[str]) -> bool:
    if not filters:
        return True
    haystack = normalize_name(game_dir.name)
    title_haystack = game_dir.name.lower()
    return any(item in haystack or item.lower() in title_haystack for item in filters)


def run_once(args: argparse.Namespace) -> int:
    repo_root = repo_root_from_script()
    config_path = resolve_path(repo_root, args.config)
    config = read_json(config_path)
    dry_run = not args.apply

    jksv_dir = resolve_path(repo_root, config.get("jksv_dir", "JKSV"))
    archive_dir = resolve_path(repo_root, config.get("archive_dir", "SAVES/Nintendo/Switch"))
    archive_emulator = safe_folder_name(config.get("archive_emulator", "Eden"))
    archive_latest_folder = safe_folder_name(config.get("archive_latest_folder", "latest"))
    replace_archive_latest = bool(config.get("replace_archive_latest", True))
    delete_extra_in_archive = bool(config.get("delete_extra_in_archive", True))

    eden_root = resolve_path(
        repo_root,
        config.get("eden_save_root", r"%APPDATA%/eden/nand/user/save/0000000000000000"),
    )
    eden_user_dir = choose_eden_user(eden_root, config.get("eden_user_id"))
    eden_backup_root = resolve_path(repo_root, config.get("eden_backup_dir", "SAVES/Tools/sync_backups/eden"))
    delete_extra_in_eden = bool(args.delete_extra_eden or config.get("delete_extra_in_eden", False))

    ignore_games = {normalize_name(name) for name in config.get("ignore_games", [])}
    games_by_key, names_by_title_id = load_games(config)
    filters = [normalize_name(item) for item in args.game]

    if not jksv_dir.exists():
        print(f"JKSV folder not found: {jksv_dir}", file=sys.stderr)
        return 2

    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("JKSV:", jksv_dir)
    print("Archive:", archive_dir / archive_emulator)
    print("Eden:", eden_user_dir if eden_user_dir else "(not found)")

    processed = 0
    skipped = 0
    eden_synced = 0

    for game_dir in sorted((path for path in jksv_dir.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
        if normalize_name(game_dir.name) in ignore_games:
            continue
        if not should_include_game(game_dir, filters):
            continue

        latest = select_latest_backup(list_backup_candidates(game_dir))
        if not latest:
            skipped += 1
            print(f"- {game_dir.name}: no backup candidates")
            continue

        info = resolve_game_info(game_dir.name, games_by_key, names_by_title_id)
        title_suffix = f" [{info.title_id}]" if info.title_id else ""
        archive_game_dir = archive_dir / archive_emulator / safe_folder_name(f"{info.archive_name}{title_suffix}")
        archive_latest_dir = archive_game_dir / archive_latest_folder

        selected_label = latest.selected_at.strftime("%Y-%m-%d %H:%M:%S")
        id_label = info.title_id or "no title id"
        print(f"- {game_dir.name} -> {info.archive_name} ({id_label})")
        print(f"  latest: {latest.path.name} ({latest.kind}, {selected_label})")

        try:
            with tempfile.TemporaryDirectory(prefix="jksv-sync-") as tmp:
                if latest.kind == "zip":
                    extract_root = Path(tmp)
                    safe_extract_zip(latest.path, extract_root)
                    payload_root = payload_root_for_extracted(extract_root, latest.path.stem)
                else:
                    payload_root = latest.path

                if not args.no_archive:
                    result = copy_tree(
                        payload_root,
                        archive_latest_dir,
                        dry_run=dry_run,
                        replace_target=replace_archive_latest,
                        delete_extra=delete_extra_in_archive,
                    )
                    print_copy_result("archive", archive_latest_dir, result, dry_run)

                    metadata = {
                        "game_folder": game_dir.name,
                        "archive_name": info.archive_name,
                        "title_id": info.title_id,
                        "source_backup": str(latest.path),
                        "source_kind": latest.kind,
                        "selected_at": latest.selected_at.isoformat(),
                        "synced_at": datetime.now().isoformat(timespec="seconds"),
                        "eden_user_id": eden_user_dir.name if eden_user_dir else None,
                    }
                    write_metadata(archive_game_dir / "sync-metadata.json", metadata, dry_run=dry_run)

                if not args.no_eden:
                    if not info.title_id:
                        print("  eden: skipped, missing title id mapping")
                    elif not eden_user_dir:
                        print("  eden: skipped, Eden user folder not found")
                    else:
                        eden_dest = eden_user_dir / info.title_id
                        if not eden_dest.exists() and not args.create_eden_game_dir:
                            print(f"  eden: skipped, game folder does not exist: {eden_dest}")
                        else:
                            eden_plan = copy_tree(
                                payload_root,
                                eden_dest,
                                dry_run=True,
                                delete_extra=delete_extra_in_eden,
                            )
                            needs_eden_write = eden_plan["copied"] > 0 or eden_plan["deleted"] > 0
                            backup_path = None
                            if needs_eden_write and not dry_run and config.get("backup_eden_before_write", True):
                                backup_path = backup_eden_destination(
                                    eden_dest,
                                    eden_backup_root,
                                    info.title_id,
                                    info.archive_name,
                                    dry_run=False,
                                )
                            elif needs_eden_write and dry_run and config.get("backup_eden_before_write", True):
                                backup_path = backup_eden_destination(
                                    eden_dest,
                                    eden_backup_root,
                                    info.title_id,
                                    info.archive_name,
                                    dry_run=True,
                                )

                            if backup_path:
                                print(f"  eden backup: {backup_path}")

                            result = eden_plan
                            if needs_eden_write and not dry_run:
                                result = copy_tree(
                                    payload_root,
                                    eden_dest,
                                    dry_run=False,
                                    delete_extra=delete_extra_in_eden,
                                )
                            print_copy_result("eden", eden_dest, result, dry_run)
                            eden_synced += 1

        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            skipped += 1
            print(f"  error: {exc}", file=sys.stderr)
            continue

        processed += 1

    print(f"Summary: processed {processed}, Eden synced {eden_synced}, skipped {skipped}")
    if dry_run:
        print("No files were written. Re-run with --apply to copy.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Switch JKSV saves to archive and Eden.")
    parser.add_argument(
        "--config",
        default="SAVES/Tools/switch_sync_config.json",
        help="Path to switch sync config JSON.",
    )
    parser.add_argument("--apply", action="store_true", help="Write files. Without this, only preview.")
    parser.add_argument("--game", action="append", default=[], help="Only sync games matching this text.")
    parser.add_argument("--watch", type=int, default=0, help="Repeat every N seconds.")
    parser.add_argument("--no-archive", action="store_true", help="Skip archive copy.")
    parser.add_argument("--no-eden", action="store_true", help="Skip Eden copy.")
    parser.add_argument("--delete-extra-eden", action="store_true", help="Delete Eden files not present in JKSV backup.")
    parser.add_argument("--create-eden-game-dir", action="store_true", help="Create missing Eden title folders.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.watch and args.watch < 5:
        parser.error("--watch must be 5 seconds or more")

    while True:
        exit_code = run_once(args)
        if not args.watch:
            return exit_code
        print(f"Waiting {args.watch} seconds...")
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
