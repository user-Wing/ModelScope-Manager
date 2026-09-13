from __future__ import annotations

import base64
import email.utils
import html
import hmac
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request

from .folder_index import FolderSizeIndex
from .http_security import modelscope_token_headers, safe_urlopen
from .service import (
    ModelScopeService,
    RemoteEntry,
    Repository,
    normalize_remote_path,
    repository_directories,
)

WEBDAV_MAX_CONCURRENT_UPLOADS = 2
WEBDAV_MIN_FREE_BYTES = 512 * 1024**2


@dataclass(frozen=True)
class DavNode:
    path: str
    name: str
    is_dir: bool
    size: int = 0
    repo: Repository | None = None
    remote_path: str = ""
    public: bool = False


class ModelScopeWebDAV:
    """Small WebDAV gateway intended for AList's generic WebDAV driver."""

    def __init__(
        self,
        service_getter: Callable[[], ModelScopeService | None],
        host: str,
        port: int,
        username: str,
        password: str,
        public_repositories_getter: Callable[[], list[Repository]] | None = None,
        folder_index: FolderSizeIndex | None = None,
    ):
        self.service_getter = service_getter
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.public_repositories_getter = public_repositories_getter or (lambda: [])
        self.folder_index = folder_index
        self.public_service = ModelScopeService("", require_token=False)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._repos_cache: dict[bool, tuple[float, list[Repository]]] = {}
        self._entries_cache: dict[tuple[bool, str, str], tuple[float, list[RemoteEntry]]] = {}
        self._virtual_dirs: set[tuple[str, str, str]] = set()
        self._upload_slots = threading.BoundedSemaphore(WEBDAV_MAX_CONCURRENT_UPLOADS)
        self._upload_guard = threading.Lock()
        self._reserved_upload_bytes = 0

    def reserve_upload(self, length: int) -> str | None:
        """Reserve bounded local staging capacity for one authenticated PUT."""
        if not self._upload_slots.acquire(blocking=False):
            return "Too many concurrent uploads"
        try:
            with self._upload_guard:
                free = shutil.disk_usage(tempfile.gettempdir()).free
                available = max(0, free - WEBDAV_MIN_FREE_BYTES - self._reserved_upload_bytes)
                if length > available:
                    return "Insufficient temporary disk space"
                self._reserved_upload_bytes += length
            return None
        except Exception:
            self._upload_slots.release()
            raise
        finally:
            # A rejected reservation never owns a slot.
            if 'available' in locals() and length > available:
                self._upload_slots.release()

    def release_upload(self, length: int) -> None:
        with self._upload_guard:
            self._reserved_upload_bytes = max(0, self._reserved_upload_bytes - max(0, length))
        self._upload_slots.release()
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        gateway = self

        class Handler(_WebDAVHandler):
            manager = gateway

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="ModelScope-WebDAV", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)

    def refresh_public_pools(self) -> None:
        with self._lock:
            self._repos_cache.pop(True, None)

    def repositories(self, public: bool = False) -> list[Repository]:
        with self._lock:
            timestamp, cached = self._repos_cache.get(public, (0.0, []))
            if cached and time.monotonic() - timestamp < 30:
                return cached
            if public:
                repos = self.public_repositories_getter()
            else:
                service = self._service(required=False)
                repos = service.list_repositories() if service else []
            self._repos_cache[public] = (time.monotonic(), repos)
            return repos

    def entries(self, repo: Repository, public: bool = False) -> list[RemoteEntry]:
        key = (public, repo.repo_type, repo.repo_id)
        with self._lock:
            timestamp, cached = self._entries_cache.get(key, (0.0, []))
            if cached and time.monotonic() - timestamp < 15:
                return cached
            entries = self._service(public=public).list_entries(repo)
            if self.folder_index:
                self.folder_index.update_repository(repo, entries, public)
            self._entries_cache[key] = (time.monotonic(), entries)
            return entries

    def invalidate(self, repo: Repository, public: bool = False) -> None:
        with self._lock:
            self._entries_cache.pop((public, repo.repo_type, repo.repo_id), None)

    def _repository_virtual_dirs(self, repo: Repository, public: bool = False) -> set[str]:
        if public:
            return set()
        with self._lock:
            return {
                remote_path for repo_type_key, repo_id_key, remote_path in self._virtual_dirs
                if repo_type_key == repo.repo_type and repo_id_key == repo.repo_id
            }

    def _service(self, public: bool = False, required: bool = True) -> ModelScopeService | None:
        service = self.public_service if public else self.service_getter()
        if service is None and required:
            raise RuntimeError("ModelScope account is not connected")
        return service

    @staticmethod
    def clean_path(raw: str) -> str:
        path = unquote(urlparse(raw).path).replace("\\", "/")
        parts = [part for part in path.split("/") if part not in ("", ".")]
        if ".." in parts:
            raise ValueError("invalid path")
        return "/".join(parts)

    @staticmethod
    def public_mount_name(repo: Repository) -> str:
        """Return a stable, readable directory name for a saved public pool."""
        return f"{repo.repo_id.replace('/', '@', 1)} [{repo.repo_type}]"

    def _folder_size(self, repo: Repository, remote_path: str = "", public: bool = False) -> int:
        return self.folder_index.folder_size(repo, remote_path, public) if self.folder_index else 0

    def _repositories_size(self, repos: list[Repository], public: bool = False) -> int:
        return self.folder_index.repositories_size(repos, public) if self.folder_index else 0

    def resolve(self, raw_path: str) -> DavNode | None:
        path = self.clean_path(raw_path)
        parts = path.split("/") if path else []
        if not parts:
            private = self.repositories(False)
            public_repos = self.repositories(True)
            size = self._repositories_size(private) + self._repositories_size(public_repos, True)
            return DavNode("", "ModelScope", True, size)
        public = parts[0] == "public"
        if public and len(parts) == 1:
            repos = self.repositories(True)
            return DavNode(path, "public", True, self._repositories_size(repos, True), public=True)
        if public:
            repo = next(
                (candidate for candidate in self.repositories(True)
                 if self.public_mount_name(candidate) == parts[1]),
                None,
            )
            if repo is None:
                return None
            if len(parts) == 2:
                return DavNode(path, parts[1], True, self._folder_size(repo, public=True), repo=repo, public=True)
            remote = normalize_remote_path(*parts[2:])
            entries = self.entries(repo, True)
            by_path = {entry.path: entry for entry in entries}
            directories = repository_directories(by_path)
            entry = by_path.get(remote)
            is_dir = remote in directories or bool(entry and entry.is_dir)
            if entry is None and not is_dir:
                return None
            size = self._folder_size(repo, remote, True) if is_dir else entry.size
            return DavNode(path, parts[-1], is_dir, size, repo, remote, True)
        if parts[0] not in {"models", "datasets"}:
            return None
        category = parts[0]
        repo_type = "model" if category == "models" else "dataset"
        level = len(parts)
        if level == 1:
            repos = [repo for repo in self.repositories(False) if repo.repo_type == repo_type]
            return DavNode(path, category, True, self._repositories_size(repos))
        repos = [repo for repo in self.repositories(False) if repo.repo_type == repo_type]
        owners = {repo.repo_id.split("/", 1)[0] for repo in repos}
        owner = parts[1]
        if owner not in owners:
            return None
        if level == 2:
            owner_repos = [repo for repo in repos if repo.repo_id.startswith(owner + "/")]
            return DavNode(path, owner, True, self._repositories_size(owner_repos))
        name = parts[2]
        repo_id = f"{owner}/{name}"
        repo = next((candidate for candidate in repos if candidate.repo_id == repo_id), None)
        if repo is None:
            return None
        if level == 3:
            return DavNode(path, name, True, self._folder_size(repo), repo=repo)
        remote = normalize_remote_path(*parts[3:])
        entries = self.entries(repo, False)
        by_path = {entry.path: entry for entry in entries}
        directories = repository_directories(by_path)
        directories.update(self._repository_virtual_dirs(repo))
        entry = by_path.get(remote)
        is_dir = remote in directories or bool(entry and entry.is_dir)
        if entry is None and not is_dir:
            return None
        size = self._folder_size(repo, remote) if is_dir else entry.size
        return DavNode(path, parts[-1], is_dir, size, repo, remote)

    def children(self, node: DavNode) -> list[DavNode]:
        parts = node.path.split("/") if node.path else []
        if not parts:
            private = self.repositories(False)
            models = [repo for repo in private if repo.repo_type == "model"]
            datasets = [repo for repo in private if repo.repo_type == "dataset"]
            public_repos = self.repositories(True)
            return [
                DavNode("models", "models", True, self._repositories_size(models)),
                DavNode("datasets", "datasets", True, self._repositories_size(datasets)),
                DavNode("public", "public", True, self._repositories_size(public_repos, True), public=True),
            ]
        public = parts[0] == "public"
        if public and len(parts) == 1:
            return [
                DavNode(
                    f"public/{self.public_mount_name(repo)}",
                    self.public_mount_name(repo),
                    True,
                    self._folder_size(repo, public=True),
                    repo=repo,
                    public=True,
                )
                for repo in sorted(self.repositories(True), key=lambda item: self.public_mount_name(item).lower())
            ]
        if public:
            repo_node = node if node.repo else self.resolve("/" + "/".join(parts[:2]))
            if repo_node is None or repo_node.repo is None:
                return []
            return self._repository_children(node, repo_node.repo, True)
        category = parts[0]
        repo_type = "model" if category == "models" else "dataset"
        repos = [repo for repo in self.repositories(False) if repo.repo_type == repo_type]
        level = len(parts)
        if level == 1:
            owners = sorted({repo.repo_id.split("/", 1)[0] for repo in repos}, key=str.lower)
            return [
                DavNode(
                    f"{node.path}/{owner}",
                    owner,
                    True,
                    self._repositories_size([repo for repo in repos if repo.repo_id.startswith(owner + "/")]),
                )
                for owner in owners
            ]
        if level == 2:
            owner = parts[1]
            names = sorted(
                [repo.repo_id.split("/", 1)[1] for repo in repos if repo.repo_id.startswith(owner + "/")],
                key=str.lower,
            )
            return [
                DavNode(
                    f"{node.path}/{name}",
                    name,
                    True,
                    self._folder_size(next(repo for repo in repos if repo.repo_id == f"{owner}/{name}")),
                    repo=next(repo for repo in repos if repo.repo_id == f"{owner}/{name}"),
                )
                for name in names
            ]
        repo_node = node if node.repo else self.resolve("/" + "/".join(parts[:3]))
        if repo_node is None or repo_node.repo is None:
            return []
        return self._repository_children(node, repo_node.repo, False)

    def _repository_children(self, node: DavNode, repo: Repository, public: bool) -> list[DavNode]:
        prefix = node.remote_path.strip("/")
        entries = self.entries(repo, public)
        directories = repository_directories(entry.path for entry in entries)
        directories.update(self._repository_virtual_dirs(repo, public))
        by_path = {entry.path: entry for entry in entries}
        candidates = set(directories) | set(by_path)
        output: list[DavNode] = []
        for candidate in sorted(candidates, key=str.lower):
            if prefix:
                if not candidate.startswith(prefix + "/"):
                    continue
                relative = candidate[len(prefix) + 1 :]
            else:
                relative = candidate
            if not relative or "/" in relative:
                continue
            entry = by_path.get(candidate)
            is_dir = candidate in directories or bool(entry and entry.is_dir)
            size = self._folder_size(repo, candidate, public) if is_dir else (0 if entry is None else entry.size)
            output.append(DavNode(
                f"{node.path}/{relative}",
                relative,
                is_dir,
                size,
                repo,
                candidate,
                public,
            ))
        return output

    def make_collection(self, path: str) -> None:
        clean = self.clean_path(path)
        parts = clean.split("/")
        if not parts or parts[0] == "public":
            raise PermissionError("Public pools are read-only")
        if len(parts) < 4:
            raise ValueError("Folders must be created inside a repository")
        repo_node = self.resolve("/" + "/".join(parts[:3]))
        if repo_node is None or repo_node.repo is None:
            raise FileNotFoundError("Repository not found")
        remote_path = normalize_remote_path(*parts[3:])
        with self._lock:
            self._virtual_dirs.add((repo_node.repo.repo_type, repo_node.repo.repo_id, remote_path))

    def upload(self, path: str, stream, length: int) -> bool:
        clean = self.clean_path(path)
        parts = clean.split("/")
        if not parts or parts[0] == "public":
            raise PermissionError("Public pools are read-only")
        if len(parts) < 4:
            raise ValueError("Files must be uploaded inside a repository")
        repo = self.resolve("/" + "/".join(parts[:3]))
        if repo is None or repo.repo is None:
            raise FileNotFoundError("Repository not found")
        remote_path = normalize_remote_path(*parts[3:])
        existed = self.resolve("/" + clean) is not None
        suffix = Path(remote_path).suffix
        handle = tempfile.NamedTemporaryFile(prefix="modelscope-webdav-", suffix=suffix, delete=False)
        temporary = Path(handle.name)
        try:
            remaining = length
            with handle:
                while remaining:
                    chunk = stream.read(min(4 * 1024 * 1024, remaining))
                    if not chunk:
                        raise ConnectionError("Upload ended before Content-Length")
                    handle.write(chunk)
                    remaining -= len(chunk)
            self._service().upload_file_as(repo.repo, temporary, remote_path)
            self.invalidate(repo.repo)
            return existed
        finally:
            temporary.unlink(missing_ok=True)


class _WebDAVHandler(BaseHTTPRequestHandler):
    manager: ModelScopeWebDAV
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _authorized(self) -> bool:
        expected = base64.b64encode(f"{self.manager.username}:{self.manager.password}".encode()).decode()
        if hmac.compare_digest(self.headers.get("Authorization", ""), "Basic " + expected):
            return True
        # A PROPFIND request commonly has an XML body.  Do not leave that body
        # unread on a persistent connection: WebDAV clients may reuse the
        # connection for the authenticated retry and corrupt the HTTP stream.
        self.close_connection = True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ModelScope Manager"')
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        return False

    def _resource_path(self) -> str:
        """Map the conventional /dav endpoint to the virtual DAV root."""
        path = urlparse(self.path).path
        if path == "/dav" or path == "/dav/":
            return "/"
        if path.startswith("/dav/"):
            return path[4:]
        return path

    def _href_prefix(self) -> str:
        path = urlparse(self.path).path
        return "dav" if path == "/dav" or path.startswith("/dav/") else ""

    def _error(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("DAV", "1")
        self.send_header("Allow", "OPTIONS, PROPFIND, HEAD, GET, PUT, MKCOL")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):
        if not self._authorized():
            return
        try:
            body_length = int(self.headers.get("Content-Length", "0") or 0)
            if body_length:
                self.rfile.read(body_length)
            node = self.manager.resolve(self._resource_path())
            if node is None:
                self._error(404, "Not found")
                return
            nodes = [node]
            if self.headers.get("Depth", "1") != "0" and node.is_dir:
                nodes.extend(self.manager.children(node))
            prefix = self._href_prefix()
            responses = "".join(self._xml_node(item, prefix) for item in nodes)
            body = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<d:multistatus xmlns:d="DAV:">' + responses + '</d:multistatus>').encode("utf-8")
        except Exception as exc:
            self._error(503, str(exc))
            return
        self.send_response(207)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _xml_node(node: DavNode, prefix: str = "") -> str:
        path = "/".join(part for part in (prefix.strip("/"), node.path.strip("/")) if part)
        href = "/" + quote(path, safe="/") + ("/" if node.is_dir and path else "")
        resource = "<d:collection/>" if node.is_dir else ""
        modified = email.utils.formatdate(time.time(), usegmt=True)
        return (
            "<d:response><d:href>" + html.escape(href) + "</d:href><d:propstat><d:prop>"
            "<d:displayname>" + html.escape(node.name) + "</d:displayname>"
            f"<d:resourcetype>{resource}</d:resourcetype>"
            f"<d:getcontentlength>{node.size}</d:getcontentlength>"
            f"<d:getlastmodified>{modified}</d:getlastmodified>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        )

    def do_HEAD(self):
        self._serve_file(head_only=True)

    def do_GET(self):
        self._serve_file(head_only=False)

    def _serve_file(self, head_only: bool) -> None:
        if not self._authorized():
            return
        try:
            node = self.manager.resolve(self._resource_path())
            if node is None or node.is_dir or node.repo is None:
                self._error(404, "File not found")
                return
            service = self.manager._service(public=node.public)
            download_url = service.get_download_url(node.repo, node.remote_path)
            headers = modelscope_token_headers(download_url, service.token, include_session_cookie=True)
            if self.headers.get("Range"):
                headers["Range"] = self.headers["Range"]
            request = Request(download_url, headers=headers)
            response = safe_urlopen(request, timeout=30)
            status = getattr(response, "status", 200)
            self.send_response(status)
            for name in ("Content-Length", "Content-Type", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
                value = response.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            response.close()
        except HTTPError as exc:
            self._error(exc.code, str(exc))
        except Exception as exc:
            self._error(502, str(exc))

    def do_PUT(self):
        if not self._authorized():
            return
        length = -1
        reserved = False
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0:
                self._error(411, "Content-Length is required")
                return
            reservation_error = self.manager.reserve_upload(length)
            if reservation_error:
                status = 507 if "disk" in reservation_error.lower() else 503
                self._error(status, reservation_error)
                return
            reserved = True
            existed = self.manager.upload(self._resource_path(), self.rfile, length)
        except PermissionError as exc:
            self._error(403, str(exc))
            return
        except OverflowError as exc:
            self._error(413, str(exc))
            return
        except Exception as exc:
            self._error(502, str(exc))
            return
        finally:
            if reserved:
                self.manager.release_upload(length)
        self.send_response(204 if existed else 201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MKCOL(self):
        if not self._authorized():
            return
        try:
            self.manager.make_collection(self._resource_path())
        except PermissionError as exc:
            self._error(403, str(exc))
            return
        except Exception as exc:
            self._error(409, str(exc))
            return
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        self._unsupported()

    def do_MOVE(self):
        self._unsupported()

    def do_COPY(self):
        self._unsupported()

    def _unsupported(self):
        if not self._authorized():
            return
        self._error(405, "ModelScope official API does not support delete, rename, move, or copy operations")
