from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .service import Repository


@dataclass(frozen=True)
class PublicMount:
    url: str
    repo: Repository
    root_path: str = ""

    @property
    def mount_name(self) -> str:
        base = f"{self.repo.repo_id.replace('/', '@', 1)} [{self.repo.repo_type}]"
        return base if not self.root_path else f"{base} [{self.root_path.replace('/', '@')}]"


class PublicPoolStore:
    """Portable on-disk history of public repositories imported from Search."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> list[dict[str, str]]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                pools = payload.get("pools", [])
            except (OSError, ValueError, TypeError):
                return []
            output = []
            seen = set()
            for item in pools:
                if not isinstance(item, dict):
                    continue
                repo_id = str(item.get("repo_id", "")).strip()
                repo_type = str(item.get("repo_type", "")).strip()
                url = str(item.get("url", "")).strip()
                root_path = str(item.get("root_path", "")).strip("/")
                key = (repo_type, repo_id, root_path)
                if repo_type not in {"model", "dataset"} or "/" not in repo_id or key in seen:
                    continue
                seen.add(key)
                output.append({"url": url, "repo_id": repo_id, "repo_type": repo_type, "root_path": root_path})
            return output

    def add(self, url: str, repo: Repository, root_path: str = "") -> None:
        with self._lock:
            pools = self.load()
            pools = [
                item for item in pools
                if (item["repo_type"], item["repo_id"], item.get("root_path", "")) != (repo.repo_type, repo.repo_id, root_path)
            ]
            pools.insert(0, {"url": url, "repo_id": repo.repo_id, "repo_type": repo.repo_type, "root_path": root_path})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="public-pools-",
                suffix=".json.tmp",
                dir=self.path.parent,
                delete=False,
            )
            temporary = Path(handle.name)
            try:
                with handle:
                    json.dump({"version": 1, "pools": pools}, handle, ensure_ascii=False, indent=2)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def remove(self, repo: Repository, root_path: str | None = None) -> None:
        with self._lock:
            pools = [
                item for item in self.load()
                if not (
                    (item["repo_type"], item["repo_id"]) == (repo.repo_type, repo.repo_id)
                    and (root_path is None or item.get("root_path", "") == root_path)
                )
            ]
            self._write(pools)

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def _write(self, pools: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="public-pools-", suffix=".json.tmp",
            dir=self.path.parent, delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump({"version": 1, "pools": pools}, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def repositories(self) -> list[Repository]:
        output = {}
        for item in self.load():
            output.setdefault((item["repo_type"], item["repo_id"]), Repository(item["repo_id"], item["repo_type"], "public"))
        return list(output.values())

    def mounts(self) -> list[PublicMount]:
        return [
            PublicMount(item["url"], Repository(item["repo_id"], item["repo_type"], "public"), item.get("root_path", ""))
            for item in self.load()
        ]
