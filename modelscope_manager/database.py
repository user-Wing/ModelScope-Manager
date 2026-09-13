from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .security import protect, unprotect
from .web_session import ModelScopeWebSession


@dataclass
class AccountRecord:
    account_id: str
    label: str
    username: str = ""
    token: str = field(default="", repr=False)
    remember: bool = True
    enabled: bool = True
    status: str = "waiting"


@dataclass
class WebAccountRecord:
    account_id: str
    label: str
    username: str = ""
    status: str = "waiting"


@dataclass(frozen=True)
class IndexedEntry:
    account_id: str
    repo_type: str
    repo_id: str
    path: str
    name: str
    extension: str
    file_type: str
    size: int
    sha256: str
    is_dir: bool


FILE_TYPE_EXTENSIONS = {
    "video": {
        ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
        ".mpeg", ".mpg", ".mts", ".ogv", ".rm", ".rmvb", ".ts", ".webm", ".wmv",
    },
    "image": {
        ".avif", ".bmp", ".gif", ".heic", ".heif", ".ico", ".jpeg", ".jpg",
        ".png", ".svg", ".tif", ".tiff", ".webp",
    },
    "document": {
        ".csv", ".doc", ".docx", ".epub", ".html", ".json", ".md", ".odf",
        ".ods", ".odt", ".pdf", ".ppt", ".pptx", ".rtf", ".tex", ".toml",
        ".tsv", ".txt", ".xls", ".xlsx", ".xml", ".yaml", ".yml",
    },
    "archive": {
        ".7z", ".bz2", ".gz", ".iso", ".lz", ".lz4", ".rar", ".tar", ".tbz2",
        ".tgz", ".txz", ".xz", ".zip", ".zst",
    },
}


def classify_file(path: str, is_dir: bool = False) -> tuple[str, str]:
    if is_dir:
        return "", "folder"
    lowered = path.lower()
    compound = next((suffix for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst") if lowered.endswith(suffix)), "")
    extension = compound or PurePosixPath(path).suffix.lower()
    for file_type, extensions in FILE_TYPE_EXTENSIONS.items():
        if extension in extensions or (compound and f".{compound.rsplit('.', 1)[-1]}" in extensions):
            return extension, file_type
    return extension, "other"


def everything_search_match(path: str, is_dir: bool, query: str) -> bool:
    """Match every space-separated term against a path, with optional field prefixes."""
    normalized_path = path.replace("\\", "/").casefold()
    name = normalized_path.rsplit("/", 1)[-1]
    extension, file_type = classify_file(normalized_path, is_dir)
    fields = {
        "path": normalized_path,
        "name": name,
        "ext": extension.lstrip("."),
        "type": file_type,
    }
    terms = [left or right for left, right in re.findall(r'"([^"]+)"|(\S+)', query.casefold())]
    for term in terms:
        field, separator, pattern = term.partition(":")
        scoped = bool(separator and field in fields)
        target = fields[field] if scoped else normalized_path
        pattern = pattern if scoped else term
        if not pattern:
            continue
        if "*" in pattern or "?" in pattern:
            if not fnmatch.fnmatch(target, f"*{pattern}*"):
                return False
        elif pattern not in target:
            return False
    return True


def initialize_database(path: Path, legacy_folder_index: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() and legacy_folder_index and legacy_folder_index.exists():
        shutil.copy2(legacy_folder_index, path)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                token_cipher TEXT NOT NULL DEFAULT '',
                token_device_id TEXT NOT NULL DEFAULT '',
                remember INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_web_sessions (
                account_id TEXT PRIMARY KEY,
                session_cipher TEXT NOT NULL,
                session_device_id TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_accounts (
                account_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS repositories (
                account_id TEXT NOT NULL,
                repo_type TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                indexed_at INTEGER NOT NULL,
                PRIMARY KEY (account_id, repo_type, repo_id)
            );
            CREATE TABLE IF NOT EXISTS entries (
                account_id TEXT NOT NULL,
                repo_type TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                extension TEXT NOT NULL DEFAULT '',
                file_type TEXT NOT NULL DEFAULT 'other',
                size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                is_dir INTEGER NOT NULL DEFAULT 0,
                indexed_at INTEGER NOT NULL,
                PRIMARY KEY (account_id, repo_type, repo_id, path)
            );
            CREATE INDEX IF NOT EXISTS entries_name ON entries(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS entries_type ON entries(file_type);
            CREATE INDEX IF NOT EXISTS entries_repo ON entries(account_id, repo_type, repo_id);
            CREATE TABLE IF NOT EXISTS folder_sizes (
                public INTEGER NOT NULL,
                repo_type TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                total_size INTEGER NOT NULL,
                indexed_at INTEGER NOT NULL,
                PRIMARY KEY (public, repo_type, repo_id, folder_path)
            );
            CREATE TABLE IF NOT EXISTS backup_jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                account_id TEXT NOT NULL,
                local_path TEXT NOT NULL,
                repo_type TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                dest_dir TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'incremental',
                interval_value REAL NOT NULL DEFAULT 30,
                interval_unit TEXT NOT NULL DEFAULT 'minute',
                download_limit_mb REAL NOT NULL DEFAULT 10,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_scan INTEGER NOT NULL DEFAULT 0,
                last_sync INTEGER NOT NULL DEFAULT 0,
                last_attempt INTEGER NOT NULL DEFAULT 0,
                last_success INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS backup_files (
                job_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                uploaded_at INTEGER NOT NULL,
                remote_path TEXT NOT NULL,
                PRIMARY KEY (job_id, relative_path)
            );
            CREATE INDEX IF NOT EXISTS backup_jobs_due ON backup_jobs(enabled, last_scan);
            CREATE TABLE IF NOT EXISTS image_records (
                image_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                repo_type TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                direct_url TEXT NOT NULL,
                cache_path TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS image_records_created ON image_records(created_at DESC);
            CREATE TABLE IF NOT EXISTS tags (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entry_tags (
                account_id TEXT NOT NULL,
                repo_type TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                path TEXT NOT NULL,
                tag_name TEXT NOT NULL COLLATE NOCASE,
                PRIMARY KEY (account_id, repo_type, repo_id, path, tag_name)
            );
            CREATE INDEX IF NOT EXISTS entry_tags_tag ON entry_tags(tag_name);
            """
        )
        backup_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(backup_jobs)")}
        if "last_attempt" not in backup_columns:
            connection.execute("ALTER TABLE backup_jobs ADD COLUMN last_attempt INTEGER NOT NULL DEFAULT 0")
        if "last_success" not in backup_columns:
            connection.execute("ALTER TABLE backup_jobs ADD COLUMN last_success INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            "UPDATE backup_jobs SET last_attempt=last_scan WHERE last_attempt=0 AND last_scan<>0"
        )
        connection.execute(
            "UPDATE backup_jobs SET last_success=last_scan WHERE last_success=0 AND last_scan<>0"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS backup_jobs_attempt ON backup_jobs(enabled, last_attempt, last_success)"
        )
        connection.execute(
            """INSERT OR IGNORE INTO web_accounts
               (account_id, label, username, sort_order, updated_at)
               SELECT sessions.account_id,
                      COALESCE(NULLIF(accounts.label, ''), NULLIF(accounts.username, ''), 'ModelScope 账户'),
                      COALESCE(accounts.username, ''),
                      COALESCE(accounts.sort_order, 0),
                      sessions.updated_at
               FROM account_web_sessions AS sessions
               LEFT JOIN accounts ON accounts.account_id=sessions.account_id"""
        )
        if legacy_folder_index and legacy_folder_index.exists() and legacy_folder_index.resolve() != path.resolve():
            legacy_connection = sqlite3.connect(legacy_folder_index)
            try:
                has_table = legacy_connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='folder_sizes'"
                ).fetchone()
                legacy_rows = legacy_connection.execute("SELECT * FROM folder_sizes").fetchall() if has_table else []
            finally:
                legacy_connection.close()
            if legacy_rows:
                connection.executemany(
                    "INSERT OR REPLACE INTO folder_sizes VALUES (?, ?, ?, ?, ?, ?)", legacy_rows
                )
        connection.commit()
    finally:
        connection.close()


class AccountStore:
    def __init__(
        self,
        path: Path,
        device_id: str,
        identity_replaced: bool = False,
        *,
        plaintext_storage: bool = False,
        destroy_on_device_change: bool = True,
    ):
        self.path = path
        self.device_id = device_id
        self.plaintext_storage = plaintext_storage
        self.destroy_on_device_change = destroy_on_device_change
        self.tokens_destroyed = False
        if identity_replaced and destroy_on_device_change:
            self.tokens_destroyed = self._saved_token_count() > 0 or self._saved_web_session_count() > 0
            self.destroy_all_tokens()
            self.destroy_all_web_sessions()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _encode_secret(self, plaintext: str) -> str:
        return "p:" + plaintext if self.plaintext_storage else protect(plaintext)

    @staticmethod
    def _decode_secret(cipher: str) -> str:
        return cipher[2:] if cipher.startswith("p:") else unprotect(cipher)

    def set_plaintext_storage(self, enabled: bool) -> int:
        """Migrate every readable saved credential to the selected storage mode."""
        previous = self.plaintext_storage
        self.plaintext_storage = enabled
        migrated = 0
        connection = self._connect()
        try:
            for row in connection.execute(
                "SELECT account_id, token_cipher FROM accounts WHERE token_cipher <> ''"
            ).fetchall():
                cipher = str(row["token_cipher"])
                try:
                    plaintext = self._decode_secret(cipher)
                except Exception:
                    continue
                replacement = self._encode_secret(plaintext)
                if replacement != cipher:
                    connection.execute(
                        "UPDATE accounts SET token_cipher=?, token_device_id=? WHERE account_id=?",
                        (replacement, self.device_id, str(row["account_id"])),
                    )
                    migrated += 1
            for row in connection.execute(
                "SELECT account_id, session_cipher FROM account_web_sessions"
            ).fetchall():
                cipher = str(row["session_cipher"])
                try:
                    plaintext = self._decode_secret(cipher)
                except Exception:
                    continue
                replacement = self._encode_secret(plaintext)
                if replacement != cipher:
                    connection.execute(
                        "UPDATE account_web_sessions SET session_cipher=?, session_device_id=? WHERE account_id=?",
                        (replacement, self.device_id, str(row["account_id"])),
                    )
                    migrated += 1
            connection.commit()
        except Exception:
            self.plaintext_storage = previous
            raise
        finally:
            connection.close()
        return migrated

    def _upgrade_account_cipher(self, account_id: str, token: str) -> None:
        """Rewrite legacy machine-scope account credentials with user DPAPI."""
        cipher = protect(token)
        connection = self._connect()
        try:
            connection.execute("UPDATE accounts SET token_cipher=? WHERE account_id=?", (cipher, account_id))
            connection.commit()
        finally:
            connection.close()

    def _upgrade_web_session_cipher(self, account_id: str, plaintext: str) -> None:
        cipher = protect(plaintext)
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE account_web_sessions SET session_cipher=? WHERE account_id=?", (cipher, account_id)
            )
            connection.commit()
        finally:
            connection.close()
    def _saved_token_count(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute(
                "SELECT COUNT(*) FROM accounts WHERE token_cipher <> ''"
            ).fetchone()[0])
        finally:
            connection.close()

    def _saved_web_session_count(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute("SELECT COUNT(*) FROM account_web_sessions").fetchone()[0])
        finally:
            connection.close()

    def list_accounts(self) -> list[AccountRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM accounts ORDER BY sort_order, updated_at, account_id"
            ).fetchall()
        finally:
            connection.close()
        output: list[AccountRecord] = []
        invalid_ids: list[str] = []
        for row in rows:
            token = ""
            cipher = str(row["token_cipher"] or "")
            bound_id = str(row["token_device_id"] or "")
            if cipher:
                if bound_id != self.device_id and self.destroy_on_device_change:
                    invalid_ids.append(str(row["account_id"]))
                else:
                    try:
                        token = self._decode_secret(cipher)
                    except Exception:
                        if self.destroy_on_device_change:
                            invalid_ids.append(str(row["account_id"]))
                    else:
                        if cipher.startswith("m:") and not self.plaintext_storage:
                            try:
                                self._upgrade_account_cipher(str(row["account_id"]), token)
                            except Exception:
                                # The legacy machine-scope value is still usable; do not destroy it
                                # merely because current-user DPAPI cannot rewrite it right now.
                                pass
            output.append(AccountRecord(
                str(row["account_id"]),
                str(row["label"]),
                str(row["username"]),
                token,
                bool(row["remember"]),
                bool(row["enabled"]),
                "waiting" if token else "token_required",
            ))
        if invalid_ids:
            self.destroy_tokens(invalid_ids)
            for account in output:
                if account.account_id in invalid_ids:
                    account.token = ""
                    account.status = "token_required"
        return output

    def list_web_accounts(self) -> list[WebAccountRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM web_accounts ORDER BY sort_order, updated_at, account_id"
            ).fetchall()
        finally:
            connection.close()
        output: list[WebAccountRecord] = []
        for row in rows:
            account_id = str(row["account_id"])
            output.append(WebAccountRecord(
                account_id,
                str(row["label"]),
                str(row["username"]),
                "connected" if self.load_web_session(account_id) else "login_required",
            ))
        return output

    def save_web_account(self, account: WebAccountRecord) -> WebAccountRecord:
        account_id = account.account_id or str(uuid.uuid4())
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT sort_order FROM web_accounts WHERE account_id=?", (account_id,)
            ).fetchone()
            order = int(existing[0]) if existing else int(connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM web_accounts"
            ).fetchone()[0])
            connection.execute(
                """INSERT OR REPLACE INTO web_accounts
                   (account_id, label, username, sort_order, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    account_id,
                    account.label.strip() or account.username or "ModelScope 账户",
                    account.username.strip(),
                    order,
                    int(time.time()),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        account.account_id = account_id
        return account

    def remove_web_account(self, account_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM web_accounts WHERE account_id=?", (account_id,))
            connection.execute("DELETE FROM account_web_sessions WHERE account_id=?", (account_id,))
            connection.execute("DELETE FROM repositories WHERE account_id=?", (f"web:{account_id}",))
            connection.execute("DELETE FROM entries WHERE account_id=?", (f"web:{account_id}",))
            connection.commit()
        finally:
            connection.close()

    def save(self, account: AccountRecord) -> AccountRecord:
        account_id = account.account_id or str(uuid.uuid4())
        cipher = self._encode_secret(account.token) if account.remember and account.token else ""
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT sort_order FROM accounts WHERE account_id=?", (account_id,)
            ).fetchone()
            if existing:
                order = int(existing[0])
            else:
                order = int(connection.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM accounts").fetchone()[0])
            connection.execute(
                """INSERT OR REPLACE INTO accounts
                   (account_id, label, username, token_cipher, token_device_id,
                    remember, enabled, sort_order, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    account.label.strip() or account.username or "ModelScope 账户",
                    account.username,
                    cipher,
                    self.device_id if cipher else "",
                    int(account.remember),
                    int(account.enabled),
                    order,
                    int(time.time()),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        account.account_id = account_id
        return account

    def remove(self, account_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
            connection.execute("DELETE FROM repositories WHERE account_id=?", (account_id,))
            connection.execute("DELETE FROM entries WHERE account_id=?", (account_id,))
            connection.commit()
        finally:
            connection.close()

    def destroy_tokens(self, account_ids: list[str]) -> None:
        if not account_ids:
            return
        connection = self._connect()
        try:
            connection.executemany(
                "UPDATE accounts SET token_cipher='', token_device_id='' WHERE account_id=?",
                [(account_id,) for account_id in account_ids],
            )
            connection.commit()
        finally:
            connection.close()

    def destroy_all_tokens(self) -> None:
        connection = self._connect()
        try:
            connection.execute("UPDATE accounts SET token_cipher='', token_device_id=''")
            connection.commit()
        finally:
            connection.close()

    def save_web_session(self, account_id: str, session: ModelScopeWebSession) -> None:
        cipher = self._encode_secret(json.dumps(session.to_dict(), ensure_ascii=False, separators=(",", ":")))
        connection = self._connect()
        try:
            connection.execute(
                """INSERT OR REPLACE INTO account_web_sessions
                   (account_id, session_cipher, session_device_id, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (account_id, cipher, self.device_id, int(time.time())),
            )
            connection.commit()
        finally:
            connection.close()

    def load_web_session(self, account_id: str) -> ModelScopeWebSession | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT session_cipher, session_device_id FROM account_web_sessions WHERE account_id=?",
                (account_id,),
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return None
        if str(row["session_device_id"]) != self.device_id and self.destroy_on_device_change:
            self.destroy_web_session(account_id)
            return None
        try:
            cipher = str(row["session_cipher"])
            plaintext = self._decode_secret(cipher)
            session = ModelScopeWebSession.from_dict(json.loads(plaintext))
        except Exception:
            if self.destroy_on_device_change:
                self.destroy_web_session(account_id)
            return None
        if cipher.startswith("m:") and not self.plaintext_storage:
            try:
                self._upgrade_web_session_cipher(account_id, plaintext)
            except Exception:
                pass
        return session

    def destroy_web_session(self, account_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM account_web_sessions WHERE account_id=?", (account_id,))
            connection.commit()
        finally:
            connection.close()

    def destroy_all_web_sessions(self) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM account_web_sessions")
            connection.commit()
        finally:
            connection.close()

    def cache_repositories(self, account_id: str, repositories) -> None:
        connection = self._connect()
        now = int(time.time())
        try:
            connection.execute("DELETE FROM repositories WHERE account_id=?", (account_id,))
            connection.executemany(
                """INSERT INTO repositories
                   (account_id, repo_type, repo_id, visibility, updated_at, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (account_id, repo.repo_type, repo.repo_id, repo.visibility, repo.updated_at, now)
                    for repo in repositories
                ],
            )
            connection.execute(
                """DELETE FROM entries
                   WHERE account_id=? AND NOT EXISTS (
                       SELECT 1 FROM repositories
                       WHERE repositories.account_id=entries.account_id
                         AND repositories.repo_type=entries.repo_type
                         AND repositories.repo_id=entries.repo_id
                   )""",
                (account_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def cache_entries(self, account_id: str, repo, entries) -> None:
        now = int(time.time())
        rows = []
        for entry in entries:
            normalized = str(entry.path).replace("\\", "/").strip("/")
            if not normalized:
                continue
            extension, file_type = classify_file(normalized, bool(entry.is_dir))
            rows.append((
                account_id,
                repo.repo_type,
                repo.repo_id,
                normalized,
                PurePosixPath(normalized).name,
                extension,
                file_type,
                max(0, int(entry.size)),
                str(entry.sha256 or ""),
                int(bool(entry.is_dir)),
                now,
            ))
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM entries WHERE account_id=? AND repo_type=? AND repo_id=?",
                (account_id, repo.repo_type, repo.repo_id),
            )
            connection.executemany(
                """INSERT INTO entries
                   (account_id, repo_type, repo_id, path, name, extension, file_type,
                    size, sha256, is_dir, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            connection.execute(
                """DELETE FROM entry_tags WHERE account_id=? AND repo_type=? AND repo_id=?
                   AND NOT EXISTS (
                       SELECT 1 FROM entries WHERE entries.account_id=entry_tags.account_id
                         AND entries.repo_type=entry_tags.repo_type AND entries.repo_id=entry_tags.repo_id
                         AND entries.path=entry_tags.path
                   )""",
                (account_id, repo.repo_type, repo.repo_id),
            )
            connection.execute(
                "DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM entry_tags WHERE entry_tags.tag_name=tags.name)"
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _indexed_entry(row: sqlite3.Row) -> IndexedEntry:
        return IndexedEntry(
            str(row["account_id"]), str(row["repo_type"]), str(row["repo_id"]),
            str(row["path"]), str(row["name"]), str(row["extension"]),
            str(row["file_type"]), int(row["size"]), str(row["sha256"]),
            bool(row["is_dir"]),
        )

    def search_entries(
        self,
        query: str = "",
        file_type: str = "all",
        account_id: str | None = None,
        repo_type: str | None = None,
        repo_id: str | None = None,
        path_prefix: str | None = None,
        tag_name: str | None = None,
        limit: int | None = None,
    ) -> list[IndexedEntry]:
        clauses = ["is_dir=0"]
        values: list[object] = []
        if file_type != "all":
            clauses.append("file_type=?")
            values.append(file_type)
        if account_id:
            clauses.append("account_id=?")
            values.append(account_id)
        if repo_type:
            clauses.append("repo_type=?")
            values.append(repo_type)
        if repo_id:
            clauses.append("repo_id=?")
            values.append(repo_id)
        if path_prefix:
            escaped_path = path_prefix.strip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("path LIKE ? ESCAPE '\\'")
            values.append(f"{escaped_path}/%")
        if tag_name:
            clauses.append(
                "EXISTS (SELECT 1 FROM entry_tags WHERE entry_tags.account_id=entries.account_id "
                "AND entry_tags.repo_type=entries.repo_type AND entry_tags.repo_id=entries.repo_id "
                "AND entry_tags.path=entries.path AND entry_tags.tag_name=?)"
            )
            values.append(tag_name)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM entries WHERE {' AND '.join(clauses)} "
                "ORDER BY name COLLATE NOCASE, repo_id COLLATE NOCASE, path COLLATE NOCASE",
                values,
            ).fetchall()
        finally:
            connection.close()
        entries = [self._indexed_entry(row) for row in rows]
        matched = [entry for entry in entries if everything_search_match(entry.path, entry.is_dir, query)]
        return matched if limit is None else matched[:max(1, int(limit))]

    @staticmethod
    def _tag_name(value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("标签名称不能为空")
        return name if name.startswith("#") else f"#{name}"

    def all_tags(self) -> list[str]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
        finally:
            connection.close()
        return [str(row[0]) for row in rows]

    def tags_for_entry(self, account_id: str, repo_type: str, repo_id: str, path: str) -> list[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT tag_name FROM entry_tags WHERE account_id=? AND repo_type=? AND repo_id=? AND path=?
                   ORDER BY tag_name COLLATE NOCASE""",
                (account_id, repo_type, repo_id, path.strip("/")),
            ).fetchall()
        finally:
            connection.close()
        return [str(row[0]) for row in rows]

    def set_entry_tags(
        self, account_id: str, repo_type: str, repo_id: str, path: str, tags: list[str],
    ) -> list[str]:
        normalized = path.strip("/")
        names = sorted({self._tag_name(tag) for tag in tags}, key=str.lower)
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM entry_tags WHERE account_id=? AND repo_type=? AND repo_id=? AND path=?",
                (account_id, repo_type, repo_id, normalized),
            )
            now = int(time.time())
            connection.executemany("INSERT OR IGNORE INTO tags VALUES (?, ?)", [(name, now) for name in names])
            connection.executemany(
                "INSERT INTO entry_tags VALUES (?, ?, ?, ?, ?)",
                [(account_id, repo_type, repo_id, normalized, name) for name in names],
            )
            connection.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM entry_tags WHERE entry_tags.tag_name=tags.name)")
            connection.commit()
        finally:
            connection.close()
        return names

    def remove_repository_entries(self, account_id: str, repo_type: str, repo_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM entries WHERE account_id=? AND repo_type=? AND repo_id=?",
                (account_id, repo_type, repo_id),
            )
            connection.execute(
                "DELETE FROM entry_tags WHERE account_id=? AND repo_type=? AND repo_id=?",
                (account_id, repo_type, repo_id),
            )
            connection.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM entry_tags WHERE entry_tags.tag_name=tags.name)")
            connection.commit()
        finally:
            connection.close()

    def remove_entry_prefixes(self, account_id: str, repo_type: str, repo_id: str, prefixes) -> None:
        normalized = list(dict.fromkeys(str(path).replace("\\", "/").strip("/") for path in prefixes if path))
        if not normalized:
            return
        connection = self._connect()
        try:
            for prefix in normalized:
                escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                parameters = (account_id, repo_type, repo_id, prefix, escaped + "/%")
                connection.execute(
                    """DELETE FROM entries WHERE account_id=? AND repo_type=? AND repo_id=?
                       AND (path=? OR path LIKE ? ESCAPE '\\')""",
                    parameters,
                )
                connection.execute(
                    """DELETE FROM entry_tags WHERE account_id=? AND repo_type=? AND repo_id=?
                       AND (path=? OR path LIKE ? ESCAPE '\\')""",
                    parameters,
                )
            connection.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM entry_tags WHERE entry_tags.tag_name=tags.name)")
            connection.commit()
        finally:
            connection.close()

    def repository_entries(self, account_id: str, repo_type: str, repo_id: str) -> list[IndexedEntry]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM entries
                   WHERE account_id=? AND repo_type=? AND repo_id=?
                   ORDER BY path COLLATE NOCASE""",
                (account_id, repo_type, repo_id),
            ).fetchall()
        finally:
            connection.close()
        return [self._indexed_entry(row) for row in rows]
