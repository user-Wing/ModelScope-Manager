"""长耗时 I/O、上传下载和索引后台线程。"""

from __future__ import annotations

import hashlib
import posixpath
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.request import Request
from .app_helpers import THUMBNAIL_RENDER_SIZE, VIDEO_THUMBNAIL_SEEK_SECONDS, copy_name, repository_file_url, repository_is_public
from .avif_converter import AvifOptions, convert_to_avif
from .backup import BackupJob, BackupStore
from .database import AccountStore
from .download_service import Aria2DownloadRunner, DownloadSpec
from .folder_index import FolderSizeIndex
from .http_security import modelscope_token_headers, safe_urlopen
from .image_bed import IMAGE_EXTENSIONS, ImageStore
from .local_paths import iter_contained_files
from .player_installer import install_potplayer
from .service import ModelScopeService, RemoteEntry, Repository, normalize_remote_path
from .storage import POTPLAYER_DIR, SEVEN_ZIP_ZSTD_EXE, THUMBNAIL_CACHE_DIR
from .web_session import DELETE_BATCH_SIZE, delete_repository_file, delete_repository_files, list_repository_file_paths


class TaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, action: Callable[[], Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.action = action

    def run(self) -> None:
        try:
            result = self.action()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)

class ThumbnailThread(QThread):
    ready = Signal(dict)

    def __init__(
        self, service: ModelScopeService, repo: Repository, entries: list[RemoteEntry], maximum_size: int,
        workers: int = 32, parent=None,
    ):
        super().__init__(parent)
        self.service, self.repo, self.entries, self.maximum_size = service, repo, entries, maximum_size
        self.workers = max(1, workers)

    def run(self) -> None:
        THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        completed: dict[str, str] = {}
        candidates = [entry for entry in self.entries if self.is_eligible(entry, self.maximum_size)]
        executor = ThreadPoolExecutor(max_workers=min(self.workers, len(candidates) or 1), thread_name_prefix="thumbnail")
        futures = [executor.submit(self._create_thumbnail, entry, ffmpeg) for entry in candidates]
        try:
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    break
                result = future.result()
                if result:
                    path, thumbnail = result
                    completed[path] = thumbnail
        finally:
            executor.shutdown(wait=not self.isInterruptionRequested(), cancel_futures=True)
        self.ready.emit(completed)

    @staticmethod
    def is_eligible(entry: RemoteEntry, maximum_size: int) -> bool:
        suffix = Path(entry.path).suffix.lower()
        is_video = suffix in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"}
        return not entry.is_dir and (suffix in IMAGE_EXTENSIONS or is_video) and (is_video or entry.size <= maximum_size)

    def _create_thumbnail(self, entry: RemoteEntry, ffmpeg: str | None) -> tuple[str, str] | None:
        if self.isInterruptionRequested() or not ffmpeg:
            return None
        width, height = THUMBNAIL_RENDER_SIZE
        key = hashlib.sha256(
            f"16x9-v3-{width}x{height}-at{VIDEO_THUMBNAIL_SEEK_SECONDS}/"
            f"{self.repo.repo_type}/{self.repo.repo_id}/{entry.path}".encode()
        ).hexdigest()
        target = THUMBNAIL_CACHE_DIR / f"{key}.jpg"
        try:
            if not target.exists():
                url = self.service.get_download_url(self.repo, entry.path)
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
                if Path(entry.path).suffix.lower() in IMAGE_EXTENSIONS:
                    headers = modelscope_token_headers(url, self.service.token, include_session_cookie=True)
                    with safe_urlopen(Request(url, headers=headers), timeout=20) as response:
                        image_bytes = response.read()
                    subprocess.run(command + ["-f", "image2pipe", "-i", "pipe:0", "-frames:v", "1", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black", "-q:v", "3", str(target)], input=image_bytes, capture_output=True, timeout=45, check=True, creationflags=creationflags)
                else:
                    subprocess.run(command + ["-ss", str(VIDEO_THUMBNAIL_SEEK_SECONDS), "-probesize", "32k", "-analyzeduration", "0", "-i", url, "-map", "0:v:0", "-frames:v", "1", "-an", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black", "-q:v", "3", str(target)], capture_output=True, timeout=45, check=True, creationflags=creationflags)
            return (entry.path, str(target)) if target.exists() else None
        except Exception:
            return None

class CopyThread(QThread):
    completed = Signal(int, int)
    failed = Signal(str)

    def __init__(self, source_service, source_repo, source_entries, selected, destination_service, destination_repo, destination_folder, parent=None):
        super().__init__(parent)
        self.source_service, self.source_repo, self.source_entries, self.selected = source_service, source_repo, source_entries, selected
        self.destination_service, self.destination_repo, self.destination_folder = destination_service, destination_repo, destination_folder

    def run(self) -> None:
        temporary = Path(tempfile.mkdtemp(prefix="modelscope-copy-"))
        ok = failed = 0
        try:
            available_files = [entry for entry in self.source_entries if not entry.is_dir]
            selected_items = self.selected if isinstance(self.selected, list) else [self.selected]
            for selected in selected_items:
                if selected.is_dir:
                    prefix = selected.path.strip("/")
                    files = [entry for entry in available_files if entry.path.startswith(prefix + "/")]
                    base = Path(prefix).name
                else:
                    files = [selected]
                    base = ""
                source_parent = selected.path.strip("/").rpartition("/")[0]
                same_location = (
                    self.source_repo.repo_type == self.destination_repo.repo_type
                    and self.source_repo.repo_id == self.destination_repo.repo_id
                    and source_parent == normalize_remote_path(self.destination_folder)
                )
                if same_location and selected.is_dir:
                    base = copy_name(base, is_dir=True)
                for entry in files:
                    if self.isInterruptionRequested():
                        break
                    relative = entry.path[len(selected.path.strip("/")):].strip("/") if selected.is_dir else Path(entry.path).name
                    if same_location and not selected.is_dir:
                        relative = copy_name(relative)
                    local = temporary / (base if selected.is_dir else "") / relative
                    local.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if hasattr(self.source_service, "download_to_file"):
                            self.source_service.download_to_file(self.source_repo, entry.path, local)
                        else:
                            download_url = self.source_service.get_download_url(self.source_repo, entry.path)
                            headers = modelscope_token_headers(download_url, self.source_service.token, include_session_cookie=True)
                            request = Request(download_url, headers=headers)
                            with safe_urlopen(request, timeout=30) as response, local.open("wb") as output:
                                while chunk := response.read(1024 * 1024):
                                    output.write(chunk)
                        target = normalize_remote_path(self.destination_folder, base if selected.is_dir else "", relative)
                        self.destination_service.upload_file_as(self.destination_repo, local, target)
                    except Exception:
                        failed += 1
                    else:
                        ok += 1
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        self.completed.emit(ok, failed)

class DeleteThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, session, repo, paths, parent=None):
        super().__init__(parent)
        self.session, self.repo, self.paths = session, repo, list(paths)

    def run(self) -> None:
        deleted: list[str] = []
        failures: dict[str, str] = {}
        for offset in range(0, len(self.paths), DELETE_BATCH_SIZE):
            batch = self.paths[offset:offset + DELETE_BATCH_SIZE]
            try:
                delete_repository_files(self.session, self.repo.repo_id, self.repo.repo_type, batch)
            except Exception as exc:
                root = posixpath.commonpath(batch)
                if root in batch:
                    root = str(PurePosixPath(root).parent)
                try:
                    present = set(list_repository_file_paths(
                        self.session, self.repo.repo_id, self.repo.repo_type, "" if root == "." else root,
                    ))
                except Exception:
                    present = set(batch)
                missing = [path for path in batch if path not in present]
                deleted.extend(missing)
                for path in (path for path in batch if path in present):
                    try:
                        delete_repository_file(self.session, self.repo.repo_id, self.repo.repo_type, path)
                    except Exception as item_exc:
                        failures[path] = str(item_exc or exc)
                    else:
                        deleted.append(path)
            else:
                deleted.extend(batch)
        self.completed.emit({"deleted": deleted, "failures": failures})

class RelocateThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self, source_service, source_repo, destination_service, destination_repo,
        mappings: dict[str, str], delete_source: Callable[[str], None], parent=None,
    ):
        super().__init__(parent)
        self.source_service, self.source_repo = source_service, source_repo
        self.destination_service, self.destination_repo = destination_service, destination_repo
        self.mappings = dict(mappings)
        self.delete_source = delete_source
        self.result: dict[str, Any] = {}

    def run(self) -> None:
        temporary = Path(tempfile.mkdtemp(prefix="modelscope-relocate-"))
        downloaded: dict[str, Path] = {}
        upload_failed: list[str] = []
        deleted: list[str] = []
        delete_failed: dict[str, str] = {}
        try:
            for index, source in enumerate(self.mappings):
                local = temporary / str(index) / Path(source).name
                local.parent.mkdir(parents=True, exist_ok=True)
                self.source_service.download_to_file(self.source_repo, source, local)
                downloaded[source] = local
            for source, target in self.mappings.items():
                try:
                    self.destination_service.upload_file_as(self.destination_repo, downloaded[source], target)
                except Exception:
                    upload_failed.append(source)
            if not upload_failed:
                for source in sorted(self.mappings, reverse=True):
                    try:
                        self.delete_source(source)
                    except Exception as exc:
                        delete_failed[source] = str(exc)
                    else:
                        deleted.append(source)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        self.result = {
            "mappings": self.mappings,
            "upload_failed": upload_failed,
            "deleted": deleted,
            "delete_failed": delete_failed,
        }
        self.completed.emit(self.result)

@dataclass
class UploadQueueItem:
    path: Path
    target: str
    status: str = "waiting"
    completed_files: set[Path] = field(default_factory=set, repr=False)

class UploadCancelled(Exception):
    pass

class UploadThread(QThread):
    item_done = Signal(str, bool, str)
    progress_info = Signal(str, int, float, int)
    bytes_transferred = Signal(int)
    reconnect_ready = Signal(str)
    cancelled = Signal(str)

    def __init__(
        self,
        service: ModelScopeService,
        repo: Repository,
        item: UploadQueueItem,
        keep_name: bool,
        max_workers: int = 4,
        skipped_files: set[Path] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.service = service
        self.repo = repo
        self.item = item
        self.keep_name = keep_name
        self.max_workers = max(1, int(max_workers))
        self.skipped_files = {path.resolve() for path in (skipped_files or set())}
        self._resume = threading.Event()
        self._resume.set()
        self._cancel = threading.Event()
        self._reconnect = threading.Event()
        self.stopped_for_reconnect = False

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._resume.set()

    def request_reconnect(self) -> None:
        self._reconnect.set()

    def _wait_until_resumed(self) -> None:
        while not self._resume.wait(0.1):
            if self._cancel.is_set():
                raise UploadCancelled()
        if self._cancel.is_set():
            raise UploadCancelled()

    def run(self) -> None:
        path = self.item.path
        try:
            root = path.resolve(strict=True)
            if root.is_dir():
                files = sorted(
                    (
                        child for child in iter_contained_files(root)
                        if child.resolve() not in self.skipped_files
                    ),
                    key=lambda child: child.relative_to(root).as_posix().casefold(),
                )
                remote_root = normalize_remote_path(
                    self.item.target,
                    root.name if self.keep_name else "",
                )
                remote_paths = {
                    child: normalize_remote_path(remote_root, child.relative_to(root).as_posix())
                    for child in files
                }
            elif root.is_file():
                files = [] if root in self.skipped_files else [root]
                remote_paths = {
                    root: normalize_remote_path(self.item.target, root.name),
                }
            else:
                raise FileNotFoundError(str(path))
            file_sizes = {child: max(0, child.stat().st_size) for child in files}
        except Exception as exc:
            self.item_done.emit(str(path), False, str(exc))
            return
        item_size = max(1, sum(file_sizes.values()))
        completed = {child.resolve() for child in self.item.completed_files}
        self.item.completed_files.intersection_update(file_sizes)
        started = time.monotonic()
        last_speed_time = started
        last_speed_bytes = 0
        current_speed = 0.0
        transferred_size = 0
        file_progress = {
            child: file_sizes[child] if child.resolve() in completed else 0
            for child in files
        }
        current_size = sum(file_progress.values())
        progress_lock = threading.Lock()
        failures: list[tuple[Path, Exception]] = []

        def emit_progress(progress: int, speed: float) -> None:
            eta = int((item_size - progress) / speed) if speed > 0 else -1
            self.progress_info.emit(
                str(path), min(99, int(progress * 100 / item_size)), speed, eta,
            )

        def report_bytes(child: Path, amount: int) -> None:
            nonlocal current_size, transferred_size, last_speed_time, last_speed_bytes, current_speed
            self._wait_until_resumed()
            amount = max(0, amount)
            with progress_lock:
                transferred_size += amount
                credited = min(amount, max(0, file_sizes[child] - file_progress[child]))
                file_progress[child] += credited
                current_size += credited
                now = time.monotonic()
                interval = now - last_speed_time
                if interval >= 0.15:
                    instant = max(0, transferred_size - last_speed_bytes) / interval
                    current_speed = instant if current_speed <= 0 else current_speed * 0.55 + instant * 0.45
                    last_speed_time = now
                    last_speed_bytes = transferred_size
                progress = current_size
                speed = current_speed
            if amount:
                self.bytes_transferred.emit(amount)
            emit_progress(progress, speed)

        def mark_file_complete(child: Path) -> None:
            nonlocal current_size
            with progress_lock:
                current_size += file_sizes[child] - file_progress[child]
                file_progress[child] = file_sizes[child]
                self.item.completed_files.add(child.resolve())
                progress = current_size
                speed = current_speed
            emit_progress(progress, speed)

        def upload_one(child: Path) -> None:
            self._wait_until_resumed()
            with self.service.track_upload_progress(lambda amount: report_bytes(child, amount)):
                self.service.upload_file_as(self.repo, child, remote_paths[child])
            mark_file_complete(child)

        if current_size:
            emit_progress(current_size, 0.0)

        try:
            pending_files = [
                child for child in files
                if child.resolve() not in self.item.completed_files
            ]
            file_iterator = iter(pending_files)
            futures = {}
            with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(pending_files)))) as executor:
                def submit_next() -> None:
                    if self._cancel.is_set() or self._reconnect.is_set():
                        return
                    try:
                        child = next(file_iterator)
                    except StopIteration:
                        return
                    futures[executor.submit(upload_one, child)] = child

                for _ in range(min(self.max_workers, len(pending_files))):
                    submit_next()
                while futures:
                    future = next(as_completed(tuple(futures)))
                    child = futures.pop(future)
                    try:
                        future.result()
                    except UploadCancelled:
                        self._cancel.set()
                    except Exception as exc:
                        failures.append((child, exc))
                    submit_next()
        except UploadCancelled:
            self._cancel.set()
        except Exception as exc:
            self.item_done.emit(str(path), False, str(exc))
            return

        if self._cancel.is_set():
            self.cancelled.emit(str(path))
            return

        remaining = [
            child for child in files
            if child.resolve() not in self.item.completed_files
        ]
        if self._reconnect.is_set() and remaining:
            self.stopped_for_reconnect = True
            self.reconnect_ready.emit(str(path))
            return

        if failures:
            first_path, first_error = failures[0]
            self.item_done.emit(
                str(path),
                False,
                f"{len(self.item.completed_files)}/{len(files)} 个文件已分别提交；"
                f"{len(failures)} 个失败，首个错误：{first_path.name} · {first_error}",
            )
            return

        self.progress_info.emit(str(path), 100, current_speed, 0)
        if not files and self.skipped_files:
            message = "没有可上传的文件"
        else:
            message = f"上传完成：{len(files)} 个文件已分别提交"
        self.item_done.emit(str(path), True, message)

class DownloadThread(QThread):
    progress_info = Signal(int, int, float, int)
    item_update = Signal(str, str, int, int, str)
    completed = Signal(int, int)
    failed = Signal(str)

    def __init__(self, runner: Aria2DownloadRunner, specs: list[DownloadSpec], parent: QWidget | None = None):
        super().__init__(parent)
        self.runner = runner
        self.specs = specs

    def run(self) -> None:
        try:
            result = self.runner.run(
                self.specs,
                lambda completed, total, speed, eta: self.progress_info.emit(completed, total, speed, eta),
                lambda spec, status, completed, total, message: self.item_update.emit(
                    str(spec.local_path), status, completed, total, message
                ),
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(*result)

class PotPlayerInstallThread(QThread):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, archive: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.archive = archive

    def run(self) -> None:
        try:
            executable = install_potplayer(
                self.archive, SEVEN_ZIP_ZSTD_EXE, POTPLAYER_DIR,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(str(executable))

class BackupThread(QThread):
    item_done = Signal(str, bool, str)
    progress_info = Signal(int, int, float, int)
    completed = Signal(str, int, int, int)

    def __init__(
        self,
        store: BackupStore,
        job: BackupJob,
        service: ModelScopeService,
        repo: Repository,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.job = job
        self.service = service
        self.repo = repo

    def run(self) -> None:
        uploaded = failed = 0
        interrupted = False
        self.store.mark_attempt(self.job.job_id)
        try:
            changed, oversized = self.store.scan_changes(self.job)
        except Exception as exc:
            self.item_done.emit(self.job.local_path, False, str(exc))
            self.completed.emit(self.job.job_id, 0, 1, 0)
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = normalize_remote_path(
            self.job.dest_dir,
            timestamp if self.job.mode == "incremental" else "",
        )
        total = max(1, sum(item.size for item in changed))
        completed_bytes = 0
        started = time.monotonic()
        for item in changed:
            if self.isInterruptionRequested():
                interrupted = True
                break
            remote_path = normalize_remote_path(prefix, item.relative_path)
            try:
                before = item.path.stat()
                if (int(before.st_size), int(before.st_mtime_ns)) != (item.size, item.mtime_ns):
                    raise RuntimeError("文件在扫描后发生变化，将在下一轮重试")
                self.service.upload_file_as(self.repo, item.path, remote_path)
                after = item.path.stat()
                if (int(after.st_size), int(after.st_mtime_ns)) != (item.size, item.mtime_ns):
                    raise RuntimeError("文件在上传期间发生变化，将在下一轮重新上传")
                self.store.mark_uploaded(self.job.job_id, item, remote_path)
            except Exception as exc:
                failed += 1
                self.item_done.emit(item.relative_path, False, str(exc))
            else:
                uploaded += 1
                self.item_done.emit(item.relative_path, True, remote_path)
            completed_bytes += item.size
            elapsed = max(0.001, time.monotonic() - started)
            speed = completed_bytes / elapsed
            eta = int((total - completed_bytes) / speed) if speed > 0 else -1
            self.progress_info.emit(completed_bytes, total, speed, eta)
        if not failed and not interrupted:
            self.store.mark_scan(self.job.job_id)
        self.completed.emit(self.job.job_id, uploaded, failed, len(oversized))

class ImageUploadThread(QThread):
    uploaded = Signal(object)
    item_done = Signal(str, bool, str)
    completed = Signal(int, int)

    def __init__(
        self,
        store: ImageStore,
        account_id: str,
        service: ModelScopeService,
        repo: Repository,
        paths: list[Path],
        destination: str,
        temporary_paths: set[Path] | None = None,
        avif_options: AvifOptions | None = None,
        ffmpeg_path: Path | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.account_id = account_id
        self.service = service
        self.repo = repo
        self.paths = paths
        self.destination = destination
        self.temporary_paths = temporary_paths or set()
        self.avif_options = avif_options
        self.ffmpeg_path = ffmpeg_path

    def run(self) -> None:
        ok = failed = 0
        date_folder = datetime.now().strftime("%Y/%m")
        try:
            for path in self.paths:
                if self.isInterruptionRequested():
                    break
                upload_path = path
                converted_path: Path | None = None
                remote_file_name = path.name
                try:
                    if self.avif_options is not None and path.suffix.lower() != ".avif":
                        if self.ffmpeg_path is None:
                            raise RuntimeError("未找到支持 AVIF 的 FFmpeg")
                        converted_path = convert_to_avif(path, self.ffmpeg_path, self.avif_options)
                        upload_path = converted_path
                        remote_file_name = f"{path.stem}.avif"
                    remote_name = f"{uuid.uuid4().hex[:8]}_{remote_file_name}"
                    remote_path = normalize_remote_path(self.destination, date_folder, remote_name)
                    self.service.upload_file_as(self.repo, upload_path, remote_path)
                    direct_url = repository_file_url(
                        self.repo,
                        remote_path,
                        repository_is_public(self.repo, self.service.token),
                    )
                    record = self.store.add(
                        self.account_id, self.repo.repo_type, self.repo.repo_id,
                        remote_path, direct_url, upload_path,
                    )
                except Exception as exc:
                    failed += 1
                    self.item_done.emit(str(path), False, str(exc))
                else:
                    ok += 1
                    self.uploaded.emit(record)
                    self.item_done.emit(str(path), True, direct_url)
                finally:
                    if converted_path is not None:
                        converted_path.unlink(missing_ok=True)
        finally:
            for path in self.temporary_paths:
                path.unlink(missing_ok=True)
        self.completed.emit(ok, failed)

class FolderIndexThread(QThread):
    repository_indexed = Signal(str)
    completed = Signal(int, int)

    def __init__(
        self,
        index: FolderSizeIndex,
        entry_store: AccountStore,
        jobs: list[tuple[ModelScopeService, Repository, bool, str]],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.index = index
        self.entry_store = entry_store
        self.jobs = jobs

    def run(self) -> None:
        ok = failed = 0
        for service, repo, public, account_id in self.jobs:
            if self.isInterruptionRequested():
                break
            try:
                entries = service.list_entries(repo)
                self.index.update_repository(repo, entries, public)
                if account_id:
                    self.entry_store.cache_entries(account_id, repo, entries)
            except Exception:
                failed += 1
            else:
                ok += 1
                self.repository_indexed.emit(repo.repo_id)
        self.completed.emit(ok, failed)
