from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import unquote

from .service import normalize_remote_path


MODELSCOPE_ORIGIN = "https://www.modelscope.cn"
DELETE_BATCH_SIZE = 100


def _request(web_session: "ModelScopeWebSession", method: str, url: str, **kwargs):
    """Send a web-session request with cookies scoped to ModelScope domains."""
    import requests

    kwargs.pop("cookies", None)
    cookies = requests.cookies.RequestsCookieJar()
    for name, value in web_session.cookies().items():
        cookies.set(name, value, domain=".modelscope.cn", path="/")
    request = getattr(requests, method.lower())
    return request(url, cookies=cookies, **kwargs)


@dataclass(frozen=True)
class ModelScopeWebSession:
    m_session_id: str
    csrf_session: str
    csrf_token: str

    def __post_init__(self) -> None:
        if not all((self.m_session_id, self.csrf_session, self.csrf_token)):
            raise ValueError("ModelScope web session is incomplete")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "ModelScopeWebSession":
        return cls(
            str(value.get("m_session_id", "")),
            str(value.get("csrf_session", "")),
            str(value.get("csrf_token", "")),
        )

    def cookies(self) -> dict[str, str]:
        return {
            "m_session_id": self.m_session_id,
            "csrf_session": self.csrf_session,
            "csrf_token": self.csrf_token,
        }

    def headers(self, referer: str = MODELSCOPE_ORIGIN) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Origin": MODELSCOPE_ORIGIN,
            "Referer": referer,
            "X-CSRF-TOKEN": unquote(self.csrf_token),
            "x-modelscope-accept-language": "zh_CN",
        }


def fetch_web_user_info(session: ModelScopeWebSession, timeout: int = 20) -> dict:
    response = _request(session, "GET",
        f"{MODELSCOPE_ORIGIN}/api/v1/users/info",
        headers=session.headers(f"{MODELSCOPE_ORIGIN}/my/overview"),
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("Code") not in (None, 200) or body.get("Success") is False:
        raise RuntimeError(str(body.get("Message") or "ModelScope web login is invalid"))
    return body.get("Data") or body


def web_session_username(user_info: dict) -> str:
    for key in ("user_name", "Username", "UserName", "username", "user_nickname"):
        value = str(user_info.get(key, "") or "").strip()
        if value:
            return value
    return ""


def delete_repository_file(
    session: ModelScopeWebSession,
    repo_id: str,
    repo_type: str,
    file_path: str,
    timeout: int = 30,
) -> dict:
    repo_id = repo_id.strip().strip("/")
    repo_type = repo_type.strip().lower()
    file_path = normalize_remote_path(file_path)
    if not repo_id or not file_path or repo_type not in {"dataset", "model"}:
        raise ValueError("repo_id, repo_type and file_path are required")
    kind = "datasets" if repo_type == "dataset" else "models"
    endpoint = "repo" if repo_type == "dataset" else "file"
    params = {"FilePath": file_path}
    if repo_type == "model":
        params["Revision"] = "master"
    referer = f"{MODELSCOPE_ORIGIN}/{kind}/{repo_id}/tree/master/{file_path.rsplit('/', 1)[0]}"
    response = _request(session, "DELETE",
        f"{MODELSCOPE_ORIGIN}/api/v1/{kind}/{repo_id}/{endpoint}",
        params=params,
        headers=session.headers(referer),
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("Code") != 200:
        raise RuntimeError(str(body.get("Message") or "ModelScope file deletion failed"))
    return body


def delete_repository_files(
    session: ModelScopeWebSession,
    repo_id: str,
    repo_type: str,
    file_paths: Iterable[str],
    timeout: int = 30,
) -> dict:
    repo_id = repo_id.strip().strip("/")
    repo_type = repo_type.strip().lower()
    paths = list(dict.fromkeys(normalize_remote_path(path) for path in file_paths))
    if not repo_id or not paths or repo_type not in {"dataset", "model"}:
        raise ValueError("repo_id, repo_type and file_paths are required")
    kind = "datasets" if repo_type == "dataset" else "models"
    actions = [
        {
            "action": "delete", "path": path, "type": "normal", "size": 0,
            "sha256": "", "content": "", "encoding": "",
        }
        for path in paths
    ]
    response = _request(session, "POST",
        f"{MODELSCOPE_ORIGIN}/api/v1/repos/{kind}/{repo_id}/commit/master",
        json={"commit_message": "Delete files via ModelScope Manager", "actions": actions},
        headers=session.headers(f"{MODELSCOPE_ORIGIN}/{kind}/{repo_id}/files"),
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("Code") not in (None, 200) or body.get("Success") is False:
        raise RuntimeError(str(body.get("Message") or "ModelScope batch deletion failed"))
    return body


def list_repository_file_paths(
    session: ModelScopeWebSession,
    repo_id: str,
    repo_type: str,
    root_path: str = "",
    timeout: int = 20,
) -> list[str]:
    repo_id = repo_id.strip().strip("/")
    repo_type = repo_type.strip().lower()
    root_path = normalize_remote_path(root_path) if root_path else ""
    if not repo_id or repo_type not in {"dataset", "model"}:
        raise ValueError("repo_id and repo_type are required")
    kind = "datasets" if repo_type == "dataset" else "models"
    suffix = "repo/tree" if repo_type == "dataset" else "repo/files"
    paths: list[str] = []
    page = 1
    while True:
        params = {"Revision": "master", "Recursive": "True", "PageNumber": page, "PageSize": 100}
        if root_path:
            params["Root"] = root_path
        response = _request(session, "GET",
            f"{MODELSCOPE_ORIGIN}/api/v1/{kind}/{repo_id}/{suffix}",
            params=params,
            headers=session.headers(f"{MODELSCOPE_ORIGIN}/{kind}/{repo_id}/files"),
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("Code") not in (None, 200) or body.get("Success") is False:
            raise RuntimeError(str(body.get("Message") or "ModelScope directory listing failed"))
        data = body.get("Data", body)
        files = data if isinstance(data, list) else (data.get("Files") or data.get("files") or [])
        for item in files:
            path = item.get("Path") or item.get("path") or item.get("Name") or item.get("name")
            if path:
                normalized = str(path).replace("\\", "/").strip("/")
                if root_path and not (normalized == root_path or normalized.startswith(root_path + "/")):
                    normalized = str(PurePosixPath(root_path) / normalized)
                paths.append(normalized)
        if len(files) < 100:
            break
        page += 1
    return paths


def delete_dataset_file(
    session: ModelScopeWebSession,
    repo_id: str,
    file_path: str,
    timeout: int = 30,
) -> dict:
    return delete_repository_file(session, repo_id, "dataset", file_path, timeout)
