"""Read-only Codex desktop event bridge. Never executes Codex or reads auth files."""
from __future__ import annotations

import json
import math
import re
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

VERSION = "1.1.0"
MAX_DISCOVERY_ROWS = 512
MAX_CURRENT_THREADS = 30
DISCOVERY_TAIL = 512 * 1024
DISCOVERY_BUDGET = 64 * 1024 * 1024
ACTIVE_FRESH_SECONDS = 300
MAX_TAIL = 8 * 1024 * 1024
MAX_LINE = 2 * 1024 * 1024
TITLES = {"working":"处理中", "completed":"已完成", "waiting":"需要确认", "idle":"已中断", "error":"处理失败", "unknown":"等待状态事件", "offline":"会话不可用"}


def normalized_path(value: str | Path) -> str:
    text = str(value)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(str(Path(text).resolve()))


class EventState:
    """Only event discriminants, IDs and timestamps survive parsing."""
    def __init__(self):
        self.state = "unknown"
        self.updated = None
        self.turn = None
        self.pending_input = None
        self.activity = None

    def consume(self, record: dict) -> None:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        kind, event = record.get("type"), payload.get("type")
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            return
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return
        except ValueError:
            return
        if kind == "event_msg":
            turn = payload.get("turn_id") or payload.get("root_turn_id")
            if event != "task_started" and turn and self.turn and turn != self.turn:
                return
            self.activity = timestamp
            if event == "task_started":
                self.state, self.turn, self.pending_input = "working", turn, None
            elif event in {"task_complete", "turn_complete", "turn_aborted", "task_failed", "error"}:
                if turn and self.turn and turn != self.turn:
                    return
                self.state = {"task_complete":"completed", "turn_complete":"completed", "turn_aborted":"idle", "task_failed":"error", "error":"error"}[event]
                self.pending_input = None
            elif event in {"exec_approval_request", "apply_patch_approval_request", "request_user_input", "approval_requested"}:
                if self.state not in {"working", "waiting"} or not turn or turn != self.turn:
                    return
                self.state = "waiting"
            elif event in {"approval_resolved", "user_input_received"}:
                if self.state == "waiting" and turn and turn == self.turn:
                    self.state = "working"
                else:
                    return
            else:
                return
        elif kind == "response_item":
            # Blocking user input has an explicit matching call/result. Async
            # questions can coexist with work, so they never imply a paused turn.
            if event == "function_call" and self.state in {"working", "waiting"} and str(payload.get("name", "")).split(".")[-1] == "request_user_input":
                self.pending_input = payload.get("call_id")
                self.state = "waiting"
            elif event == "function_call_output" and self.pending_input and payload.get("call_id") == self.pending_input:
                self.pending_input, self.state = None, "working"
            else:
                return
        else:
            return
        self.updated = timestamp


def _effective_state(reader: EventState, caught_up: bool = True) -> str:
    if not caught_up:
        return "unknown"
    state = reader.state
    if state in {"working", "waiting"}:
        stamp = reader.activity or reader.updated
        if not stamp:
            return "unknown"
        try:
            age = (datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
            if not -30 <= age <= ACTIVE_FRESH_SECONDS:
                return "unknown"
        except (ValueError, TypeError):
            return "unknown"
    return state


def _public_title(value: object, project_label: str, identity: str) -> str:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        if cleaned and len(cleaned) <= 512 and not any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
            return cleaned
    return f"{project_label} · {identity[:8]}"


def _event_metadata(data: bytes) -> dict | None:
    """Select lifecycle scalar fields without JSON-decoding message/tool bodies."""
    allowed = {"type", "turn_id", "root_turn_id", "call_id", "name"}
    size = len(data)
    def space(i):
        while i < size and data[i] in b" \t\r\n": i += 1
        return i
    def string_end(i):
        if i >= size or data[i] != 34: raise ValueError()
        i += 1
        while i < size:
            quote = data.find(b'"', i)
            if quote < 0: raise ValueError()
            back = quote-1
            while back >= i and data[back] == 92: back -= 1
            if (quote-1-back) % 2 == 0: return quote+1
            i = quote+1
        raise ValueError()
    def skip(i, depth):
        i=space(i)
        if depth > 64 or i >= size: raise ValueError()
        if data[i] == 34: return string_end(i)
        if data[i] == 123: return obj(i,set(),depth+1)[1]
        if data[i] == 91:
            i=space(i+1)
            if i < size and data[i] == 93:return i+1
            while True:
                i=space(skip(i,depth+1))
                if i < size and data[i] == 93:return i+1
                if i >= size or data[i] != 44:raise ValueError()
                i=space(i+1)
        match=re.match(rb'(?:null|true|false|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)',data[i:])
        if not match:raise ValueError()
        return i+match.end()
    def obj(i,wanted,depth):
        result={};i=space(i+1)
        if i < size and data[i] == 125:return result,i+1
        while True:
            end=string_end(i)
            key=json.loads(data[i:end]) if wanted and end-i <= 128 else None
            i=space(end)
            if i >= size or data[i] != 58:raise ValueError()
            i=space(i+1)
            if key == "payload" and key in wanted and data[i:i+1] == b"{":
                result[key],i=obj(i,allowed,depth+1)
            else:
                end=skip(i,depth+1)
                if key in wanted and end-i <= 2048 and (data[i:i+1] == b'"' or data[i:end] == b"null"):
                    result[key]=json.loads(data[i:end])
                i=end
            i=space(i)
            if i < size and data[i] == 125:return result,i+1
            if i >= size or data[i] != 44:raise ValueError()
            i=space(i+1)
    try:
        start=space(0)
        if data[start:start+1] != b"{":return None
        record,end=obj(start,{"type","timestamp","payload"},0)
        return record if space(end) == size else None
    except (ValueError,IndexError,UnicodeDecodeError,RecursionError):
        return None


class _EventCursor:
    """Bounded tail, then only appended bytes. Message bodies never survive parsing."""
    def __init__(self):
        self.reader = EventState()
        self.offset = 0
        self.partial = b""
        self.identity = None
        self.window = 0
        self.caught_up = False

    def update(self, path: Path, allowance: int) -> int:
        info = path.stat()
        identity = (info.st_dev, info.st_ino)
        fresh = self.identity != identity or info.st_size < self.offset
        if self.reader.state == "unknown" and self.window < allowance and info.st_size > self.window:
            fresh = True
        allowance = max(0, min(MAX_TAIL, allowance))
        if not allowance:
            return 0
        start = max(0, info.st_size-allowance) if fresh else self.offset
        if fresh:
            self.reader, self.partial = EventState(), b""
            self.window = allowance
        with path.open("rb") as stream:
            stream.seek(start)
            skipped = len(stream.readline(allowance)) if fresh and start else 0
            data = stream.read(max(0, allowance-skipped))
            self.offset = stream.tell()
        self.identity = identity
        self.caught_up = self.offset >= info.st_size
        lines = (self.partial+data).split(b"\n")
        self.partial = lines.pop()
        if len(self.partial) > MAX_LINE:
            self.partial = b""
        for line in lines:
            if len(line) > MAX_LINE:
                continue
            # Session metadata, prompts and normal response/message records are
            # not decoded. Only lifecycle/control records feed EventState.
            kind = re.search(rb'"type"\s*:\s*"(event_msg|response_item)"', line[:256])
            if not kind:
                continue
            if kind.group(1) == b"response_item" and not re.search(rb'"type"\s*:\s*"function_call(?:_output)?"', line[ max(0,kind.end()):2048]):
                continue
            record = _event_metadata(line)
            if record is not None:
                self.reader.consume(record)
        return skipped+len(data)


class CodexBridge:
    def __init__(self, project: Path, home: Path | None = None, config_path: Path | None = None):
        self.project = project.resolve()
        self.home = (home or Path(os.environ.get("CODEX_HOME", Path.home()/".codex"))).resolve()
        if config_path is None:
            from host.config_store import _user_config_path
            config_path = _user_config_path().with_name("codex-connection.json")
        self.config_path = config_path
        self.lock = threading.RLock()
        self.config = {}
        self.error = ""
        self._threads = []
        self._discovered_at = 0.0
        self._cursors: dict[str, _EventCursor] = {}
        self.discovery_info = {}
        self._pending_ids = []
        self._reader = EventState()
        self._path = None
        self._offset = 0
        self._partial = b""
        self._last_read = 0.0
        self._load()

    def _load(self):
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            if value.get("version") in {"1.0.0", VERSION} and normalized_path(value.get("project", "")) == normalized_path(self.project):
                self.config = value
        except (OSError, ValueError, AttributeError):
            pass

    def _safe_rollout(self, value: str) -> Path | None:
        try:
            path = Path(normalized_path(value)).resolve()
            path.relative_to((self.home/"sessions").resolve())
            return path if path.suffix == ".jsonl" and path.is_file() else None
        except (OSError, ValueError):
            return None

    def _cursor(self, path: Path, allowance: int) -> tuple[_EventCursor, int]:
        cursor = self._cursors.setdefault(str(path), _EventCursor())
        return cursor, cursor.update(path, allowance)

    def _index_titles(self) -> dict[str, str]:
        """Official local sidebar title index; bounded metadata-only tail."""
        result = {}
        try:
            with (self.home / "session_index.jsonl").open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - MAX_TAIL))
                if size > MAX_TAIL:
                    stream.readline(MAX_LINE)
                for line in stream:
                    if len(line) > MAX_LINE:
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    identity, title = record.get("id"), record.get("thread_name")
                    if isinstance(identity, str) and isinstance(title, str):
                        uuid.UUID(identity)
                        if _public_title(title, "", identity) == " ".join(title.split()):
                            result[identity] = title
        except (OSError, ValueError, TypeError):
            pass
        return result

    def discover(self, force=False) -> list[dict]:
        with self.lock:
            if not force and time.monotonic()-self._discovered_at < 10:
                return [dict(item) for item in self._threads]
            rows, truncated = [], False
            index_titles = self._index_titles()
            databases = sorted(self.home.glob("state_*.sqlite"), key=lambda p:p.stat().st_mtime, reverse=True)
            for database in databases[:2]:
                try:
                    with closing(sqlite3.connect(database.as_uri()+"?mode=ro", uri=True, timeout=1)) as con:
                        columns = {row[1] for row in con.execute("PRAGMA table_info(threads)")}
                        if not {"id", "rollout_path", "cwd", "source", "updated_at", "archived"} <= columns:
                            continue
                        # Only explicitly authorized thread metadata; never
                        # preview, prompt, message, auth, or other SQLite tables.
                        fields = "id,rollout_path,cwd,source,updated_at,"+("title" if "title" in columns else "NULL")
                        allowed = "archived=0 AND lower(coalesce(source,'')) NOT LIKE '%subagent%'"
                        recent = con.execute(f"SELECT {fields} FROM threads WHERE {allowed} ORDER BY updated_at DESC LIMIT ?", (MAX_DISCOVERY_ROWS+1,)).fetchall()
                        truncated = len(recent) > MAX_DISCOVERY_ROWS
                        rows.extend(recent[:MAX_DISCOVERY_ROWS])
                        aliases = {str(self.project), normalized_path(self.project), "\\\\?\\"+str(self.project)}
                        placeholders = ",".join("?" for _ in aliases)
                        rows.extend(con.execute(f"SELECT {fields} FROM threads WHERE {allowed} AND cwd COLLATE NOCASE IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?", (*aliases, MAX_CURRENT_THREADS)).fetchall())
                        if self.config.get("threadId"):
                            rows.extend(con.execute(f"SELECT {fields} FROM threads WHERE {allowed} AND id=? LIMIT 1", (self.config["threadId"],)).fetchall())
                    break
                except (OSError, sqlite3.Error):
                    continue
            candidates = {}
            for identity, rollout, cwd, source, updated, title in rows:
                if not isinstance(cwd, str) or not cwd or "subagent" in str(source).lower():
                    continue
                try:
                    uuid.UUID(identity)
                    current = normalized_path(cwd) == normalized_path(self.project)
                    label = Path(cwd).name or "未命名项目"
                    path = self._safe_rollout(rollout)
                except (ValueError, TypeError, OSError):
                    continue
                if path is None or identity in candidates:
                    continue
                try:
                    updated = float(updated)
                    if not math.isfinite(updated): updated = 0
                except (ValueError,TypeError):
                    updated = 0
                candidates[identity] = {"id":identity,"path":str(path),"updatedAt":updated,
                    "title":_public_title(index_titles.get(identity, title),label,identity),"isCurrentProject":current,"projectLabel":label,"state":"unknown"}
            current_rows = sorted((item for item in candidates.values() if item["isCurrentProject"]), key=lambda item:item["updatedAt"], reverse=True)
            current_ids = {item["id"] for item in current_rows[:MAX_CURRENT_THREADS]}
            bound_id = self.config.get("threadId")
            remaining = DISCOVERY_BUDGET
            retained, pending, limited = [], [], []
            order = {identity:index for index,identity in enumerate(self._pending_ids)}
            ordered = sorted(candidates.values(),key=lambda item:order.get(item["id"],len(order)))
            for item in ordered:
                path = Path(item["path"])
                try:
                    protected = item["id"] in current_ids or item["id"] == bound_id
                    allowance = min(remaining, DISCOVERY_TAIL)
                    existing = self._cursors.get(str(path))
                    if not allowance and (existing is None or existing.offset != path.stat().st_size):
                        pending.append(item["id"])
                        if protected:retained.append(item)
                        continue
                    cursor, used = self._cursor(path, allowance)
                    remaining -= used
                    if cursor.reader.state == "unknown" and path.stat().st_size > cursor.window and remaining:
                        cursor, used = self._cursor(path, min(MAX_TAIL, remaining))
                        remaining -= used
                    item["state"] = _effective_state(cursor.reader, cursor.caught_up)
                    if not cursor.caught_up or (cursor.reader.state == "unknown" and path.stat().st_size > cursor.window and cursor.window < MAX_TAIL):
                        pending.append(item["id"])
                    elif cursor.reader.state == "unknown" and path.stat().st_size > MAX_TAIL:
                        limited.append(item["id"])
                except OSError:
                    item["state"] = "offline"
                if protected or item["state"] in {"working","waiting"}:
                    retained.append(item)
            retained.sort(key=lambda item:(item["state"] not in {"working","waiting"},not item["isCurrentProject"],-float(item["updatedAt"])))
            self._threads = retained
            self._pending_ids = pending
            safe_paths = {item["path"] for item in candidates.values()}
            self._cursors = {path:cursor for path,cursor in self._cursors.items() if path in safe_paths}
            self.discovery_info = {"candidateLimit":MAX_DISCOVERY_ROWS,"currentProjectLimit":MAX_CURRENT_THREADS,
                                  "tailLimitBytes":MAX_TAIL,"readBudgetBytes":DISCOVERY_BUDGET,"cacheSeconds":10,
                                  "examined":len(candidates),"truncated":truncated or bool(pending),
                                  "scanTruncated":truncated or bool(pending),"stateScanPending":len(pending),
                                  "stateScanLimited":len(limited),"freshSeconds":ACTIVE_FRESH_SECONDS}
            self._discovered_at = time.monotonic()
            return [dict(item) for item in self._threads]

    def install(self, thread_id: str | None = None) -> dict:
        with self.lock:
            threads = self.discover(force=True)
            current = sorted((row for row in threads if row["isCurrentProject"]), key=lambda row:row["updatedAt"], reverse=True)
            selected = next((row for row in threads if row["id"] == thread_id), None) if thread_id else (current[0] if current else next((row for row in threads if row["id"] == self.config.get("threadId")),None))
            if selected is None:
                raise ValueError("未找到可绑定会话；自动选择仅使用当前项目，请打开当前项目或手动选择正在执行的根对话")
            candidate = {"version":VERSION, "enabled":True, "project":str(self.project), "threadId":selected["id"]}
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".codex-connection-", dir=self.config_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(candidate, out, ensure_ascii=False)
                os.replace(temporary, self.config_path)
                self.config = candidate
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self._path, self._offset, self._partial = None, 0, b""
            self._last_read = 0
            return self.component()

    def _refresh(self):
        if time.monotonic()-self._last_read < .5:
            if self.error:
                raise OSError("session unavailable")
            return
        self._last_read = time.monotonic()
        selected = next((row for row in self.discover() if row["id"] == self.config.get("threadId")), None)
        path = self._safe_rollout(selected["path"]) if selected else None
        if not path:
            raise OSError("selected session unavailable")
        cursor, _ = self._cursor(path, MAX_TAIL)
        self._reader = cursor.reader
        self._path, self._offset, self._partial = path, cursor.offset, cursor.partial
        self._caught_up = cursor.caught_up

    def status(self) -> dict:
        with self.lock:
            if not self.config.get("enabled"):
                return {"state":"unknown", "title":"组件未安装", "detail":"请在电脑设置页安装 Codex 连接组件", "updatedAt":None, "source":"none", "connected":False}
            try:
                self._refresh()
                self.error = ""
                state, updated = _effective_state(self._reader, getattr(self,"_caught_up",True)), self._reader.updated
                detail = {"working":"Codex 正在处理当前任务", "completed":"当前 Codex 任务已完成", "waiting":"Codex 正在等待你的确认", "idle":"当前 Codex 任务已中断", "error":"当前 Codex 任务发生错误", "unknown":"组件已连接，等待新的明确状态事件"}[state]
                return {"state":state, "title":TITLES[state], "detail":detail, "updatedAt":updated, "source":"codex-local-events", "connected":True}
            except (OSError, ValueError):
                self.error = "无法读取绑定会话，请确认 Codex 会话仍存在，或重新安装连接组件选择会话"
                return {"state":"offline", "title":"会话不可用", "detail":self.error, "updatedAt":None, "source":"codex-local-events", "connected":False}

    def component(self, force=False) -> dict:
        with self.lock:
            threads = self.discover(force)
            status = self.status() if self.config.get("enabled") else {"connected":False}
            bound=next((row for row in threads if row['id']==self.config.get('threadId')),None)
            if bound:
                bound["state"] = status.get("state",bound["state"])
            bound_label = bound["projectLabel"] if bound else self.project.name
            return {"installed":bool(self.config.get("enabled")), "version":VERSION,
                    "connected":status["connected"], "threadId":self.config.get("threadId"),
                    "sourceLabel":"Codex 本地只读事件", "projectLabel":self.project.name,
                    "message":self.error or (f"已绑定 {bound_label} 会话" if self.config.get("enabled") else "自动绑定当前项目最近会话；也可选择本机其他项目正在执行的根对话"),
                    "threads":[{key:row[key] for key in ("id","title","state","isCurrentProject","projectLabel","updatedAt")} for row in threads],
                    "discovery":dict(self.discovery_info)}
