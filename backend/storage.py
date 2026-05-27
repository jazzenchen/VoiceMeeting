from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import CHUNKS_DIR, DB_PATH, ensure_runtime_dirs
from .transcript import build_utterances


DEFAULT_SUMMARY: Dict[str, Any] = {
    "summary": "",
    "topics": [],
    "decisions": [],
    "action_items": [],
    "open_questions": [],
    "risks": [],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeetingStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        ensure_runtime_dirs()
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
	                CREATE TABLE IF NOT EXISTS meetings (
	                    id TEXT PRIMARY KEY,
	                    title TEXT NOT NULL,
	                    description TEXT NOT NULL DEFAULT '',
	                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    final_markdown TEXT NOT NULL DEFAULT '',
                    summary_source_hash TEXT NOT NULL DEFAULT '',
                    summary_source_version_id TEXT NOT NULL DEFAULT '',
                    summary_segment_count INTEGER NOT NULL DEFAULT 0,
                    final_source_hash TEXT NOT NULL DEFAULT '',
                    final_source_version_id TEXT NOT NULL DEFAULT '',
                    active_version_id TEXT NOT NULL DEFAULT 'auto'
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    client_chunk_id TEXT NOT NULL DEFAULT '',
                    started_at_ms INTEGER,
                    ended_at_ms INTEGER,
                    cut_reason TEXT NOT NULL DEFAULT '',
                    audio_path TEXT NOT NULL,
                    wav_path TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    duration_ms INTEGER,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id)
                );

                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    version_id TEXT NOT NULL DEFAULT 'auto',
                    chunk_id TEXT NOT NULL,
                    start_ms INTEGER NOT NULL DEFAULT 0,
                    end_ms INTEGER NOT NULL DEFAULT 0,
                    speaker TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id),
                    FOREIGN KEY (chunk_id) REFERENCES chunks(id)
                );

                CREATE TABLE IF NOT EXISTS transcript_versions (
                    id TEXT NOT NULL,
                    meeting_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    parent_version_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (meeting_id, id),
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id)
                );

                CREATE TABLE IF NOT EXISTS final_notes (
                    meeting_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (meeting_id, version_id, source_hash),
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id)
                );

                CREATE TABLE IF NOT EXISTS speakers (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id),
                    UNIQUE(meeting_id, label)
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_meeting_seq
                    ON chunks(meeting_id, seq);
                CREATE INDEX IF NOT EXISTS idx_segments_meeting_created
                    ON segments(meeting_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_versions_meeting_updated
                    ON transcript_versions(meeting_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_final_notes_meeting_version
                    ON final_notes(meeting_id, version_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_speakers_meeting_label
                    ON speakers(meeting_id, label);
                """
            )
            self._ensure_schema_columns(conn)

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        table: str,
        migrations: Dict[str, str],
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row["name"] for row in rows}
        for column, statement in migrations.items():
            if column not in existing:
                conn.execute(statement)

    def _ensure_schema_columns(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "chunks",
            {
                "client_chunk_id": "ALTER TABLE chunks ADD COLUMN client_chunk_id TEXT NOT NULL DEFAULT ''",
                "started_at_ms": "ALTER TABLE chunks ADD COLUMN started_at_ms INTEGER",
                "ended_at_ms": "ALTER TABLE chunks ADD COLUMN ended_at_ms INTEGER",
                "cut_reason": "ALTER TABLE chunks ADD COLUMN cut_reason TEXT NOT NULL DEFAULT ''",
            },
        )
        self._ensure_columns(
            conn,
            "meetings",
	            {
	                "description": "ALTER TABLE meetings ADD COLUMN description TEXT NOT NULL DEFAULT ''",
	                "active_version_id": "ALTER TABLE meetings ADD COLUMN active_version_id TEXT NOT NULL DEFAULT 'auto'",
                "summary_source_hash": "ALTER TABLE meetings ADD COLUMN summary_source_hash TEXT NOT NULL DEFAULT ''",
                "summary_source_version_id": "ALTER TABLE meetings ADD COLUMN summary_source_version_id TEXT NOT NULL DEFAULT ''",
                "summary_segment_count": "ALTER TABLE meetings ADD COLUMN summary_segment_count INTEGER NOT NULL DEFAULT 0",
                "final_source_hash": "ALTER TABLE meetings ADD COLUMN final_source_hash TEXT NOT NULL DEFAULT ''",
                "final_source_version_id": "ALTER TABLE meetings ADD COLUMN final_source_version_id TEXT NOT NULL DEFAULT ''",
            },
        )
        self._ensure_columns(
            conn,
            "segments",
            {
                "version_id": "ALTER TABLE segments ADD COLUMN version_id TEXT NOT NULL DEFAULT 'auto'",
            },
        )
        conn.execute("UPDATE meetings SET active_version_id = 'auto' WHERE active_version_id = ''")
        conn.execute("UPDATE segments SET version_id = 'auto' WHERE version_id = ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_segments_meeting_version_created
                ON segments(meeting_id, version_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_final_notes_meeting_version
                ON final_notes(meeting_id, version_id, updated_at)
            """
        )
        meetings = conn.execute("SELECT id, created_at, updated_at FROM meetings").fetchall()
        for meeting in meetings:
            conn.execute(
                """
                INSERT OR IGNORE INTO transcript_versions
                (id, meeting_id, label, kind, parent_version_id, status, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "auto",
                    meeting["id"],
                    "auto",
                    "initial",
                    "",
                    "ready",
                    "{}",
                    meeting["created_at"],
                    meeting["updated_at"],
                ),
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO final_notes
            (meeting_id, version_id, source_hash, markdown, created_at, updated_at)
            SELECT
                id,
                COALESCE(NULLIF(final_source_version_id, ''), NULLIF(active_version_id, ''), 'auto'),
                final_source_hash,
                final_markdown,
                updated_at,
                updated_at
            FROM meetings
            WHERE final_markdown != '' AND final_source_hash != ''
            """
        )

    def _transcript_source_for_segments(
        self,
        version_id: str,
        segments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = [
            {
                "speaker": str(segment.get("speaker") or ""),
                "text": str(segment.get("text") or "").strip(),
                "start_ms": int(segment.get("start_ms") or 0),
                "end_ms": int(segment.get("end_ms") or 0),
                "chunk_id": str(segment.get("chunk_id") or ""),
            }
            for segment in segments
            if str(segment.get("text") or "").strip()
        ]
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "version_id": str(version_id or "auto"),
            "hash": digest,
            "segment_count": len(payload),
        }

    def _active_version_id(self, conn: sqlite3.Connection, meeting_id: str) -> str:
        row = conn.execute("SELECT active_version_id FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            raise KeyError(meeting_id)
        return str(row["active_version_id"] or "auto")

    def _list_transcript_versions(self, conn: sqlite3.Connection, meeting_id: str) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM transcript_versions
            WHERE meeting_id = ?
            ORDER BY created_at ASC
            """,
            (meeting_id,),
        ).fetchall()
        versions = []
        for row in rows:
            item = dict(row)
            try:
                item["settings"] = json.loads(item.pop("settings_json") or "{}")
            except Exception:
                item["settings"] = {}
            versions.append(item)
        return versions

    def _transcript_version(
        self,
        conn: sqlite3.Connection,
        meeting_id: str,
        version_id: str,
    ) -> Dict[str, Any]:
        row = conn.execute(
            """
            SELECT *
            FROM transcript_versions
            WHERE meeting_id = ? AND id = ?
            """,
            (meeting_id, version_id),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        item = dict(row)
        try:
            item["settings"] = json.loads(item.pop("settings_json") or "{}")
        except Exception:
            item["settings"] = {}
        return item

    def create_meeting(self, title: Optional[str], description: Optional[str] = None) -> Dict[str, Any]:
        meeting_id = uuid.uuid4().hex
        stamp = now_iso()
        clean_title = title.strip() if title else "今天的会议"
        clean_description = (description or "").strip()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meetings
                (id, title, description, status, created_at, updated_at, summary_json, active_version_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    clean_title,
                    clean_description,
                    "recording",
                    stamp,
                    stamp,
                    json.dumps(DEFAULT_SUMMARY),
                    "auto",
                ),
            )
            conn.execute(
                """
                INSERT INTO transcript_versions
                (id, meeting_id, label, kind, parent_version_id, status, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("auto", meeting_id, "auto", "initial", "", "ready", "{}", stamp, stamp),
            )
        return self.get_meeting(meeting_id)

    def update_meeting_status(self, meeting_id: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE meetings SET status = ?, updated_at = ? WHERE id = ?",
                (status, now_iso(), meeting_id),
            )

    def update_meeting_title(
        self,
        meeting_id: str,
        title: Optional[str],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_title = title.strip() if title else "今天的会议"
        with self._lock, self._connect() as conn:
            if description is None:
                result = conn.execute(
                    "UPDATE meetings SET title = ?, updated_at = ? WHERE id = ?",
                    (clean_title, now_iso(), meeting_id),
                )
            else:
                clean_description = description.strip()
                result = conn.execute(
                    "UPDATE meetings SET title = ?, description = ?, updated_at = ? WHERE id = ?",
                    (clean_title, clean_description, now_iso(), meeting_id),
                )
            if result.rowcount == 0:
                raise KeyError(meeting_id)
        return self.get_meeting(meeting_id)

    def get_meeting(self, meeting_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if meeting is None:
                raise KeyError(meeting_id)
            active_version_id = str(meeting["active_version_id"] or "auto")
            segments = conn.execute(
                """
                SELECT * FROM segments
                WHERE meeting_id = ? AND version_id = ?
                ORDER BY created_at ASC
                """,
                (meeting_id, active_version_id),
            ).fetchall()
            chunks = conn.execute(
                """
                SELECT * FROM chunks
                WHERE meeting_id = ?
                ORDER BY seq ASC
                """,
                (meeting_id,),
            ).fetchall()
            speakers = conn.execute(
                """
                SELECT id, meeting_id, label, sample_count, created_at, updated_at
                FROM speakers
                WHERE meeting_id = ?
                ORDER BY created_at ASC
                """,
                (meeting_id,),
            ).fetchall()
            versions = self._list_transcript_versions(conn, meeting_id)
            segment_rows = [dict(row) for row in segments]
            chunk_rows = [dict(row) for row in chunks]
            speaker_rows = [dict(row) for row in speakers]
            final_markdown = ""
            final_source_hash = ""
            final_source_version_id = ""
            current_source = self._transcript_source_for_segments(active_version_id, segment_rows)
            final_note = conn.execute(
                """
                SELECT markdown, source_hash, version_id
                FROM final_notes
                WHERE meeting_id = ? AND version_id = ? AND source_hash = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (meeting_id, current_source["version_id"], current_source["hash"]),
            ).fetchone()
            if final_note is not None:
                final_markdown = str(final_note["markdown"] or "")
                final_source_hash = str(final_note["source_hash"] or "")
                final_source_version_id = str(final_note["version_id"] or "")
            else:
                meeting_final_hash = str(meeting["final_source_hash"] or "")
                meeting_final_version_id = str(meeting["final_source_version_id"] or "")
                version_matches = meeting_final_version_id == current_source["version_id"] or (
                    meeting_final_version_id == "" and current_source["version_id"] == "auto"
                )
                if (
                    str(meeting["final_markdown"] or "").strip()
                    and meeting_final_hash == current_source["hash"]
                    and version_matches
                ):
                    final_markdown = str(meeting["final_markdown"] or "")
                    final_source_hash = meeting_final_hash
                    final_source_version_id = current_source["version_id"]
        return {
            "id": meeting["id"],
            "title": meeting["title"],
            "description": meeting["description"],
            "status": meeting["status"],
            "created_at": meeting["created_at"],
            "updated_at": meeting["updated_at"],
            "active_version_id": active_version_id,
            "transcript_versions": versions,
            "summary": json.loads(meeting["summary_json"]),
            "summary_source_hash": meeting["summary_source_hash"],
            "summary_source_version_id": meeting["summary_source_version_id"],
            "summary_segment_count": meeting["summary_segment_count"],
            "final_source_hash": final_source_hash,
            "final_source_version_id": final_source_version_id,
            "final_markdown": final_markdown,
            "segments": segment_rows,
            "utterances": build_utterances(segment_rows, chunk_rows),
            "chunks": chunk_rows,
            "speakers": speaker_rows,
        }

    def list_meetings(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, status, created_at, updated_at
                FROM meetings
                ORDER BY created_at DESC
                LIMIT 50
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_transcript_versions(self, meeting_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            meeting = conn.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if meeting is None:
                raise KeyError(meeting_id)
            return self._list_transcript_versions(conn, meeting_id)

    def get_transcript_version(self, meeting_id: str, version_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            meeting = conn.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if meeting is None:
                raise KeyError(meeting_id)
            return self._transcript_version(conn, meeting_id, version_id)

    def get_active_transcript_version(self, meeting_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            version_id = self._active_version_id(conn, meeting_id)
            return self._transcript_version(conn, meeting_id, version_id)

    def set_active_transcript_version(self, meeting_id: str, version_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            version = conn.execute(
                """
                SELECT id FROM transcript_versions
                WHERE meeting_id = ? AND id = ?
                """,
                (meeting_id, version_id),
            ).fetchone()
            if version is None:
                raise KeyError(version_id)
            conn.execute(
                """
                UPDATE meetings
                SET active_version_id = ?, final_markdown = '', final_source_hash = '',
                    final_source_version_id = '', updated_at = ?
                WHERE id = ?
                """,
                (version_id, now_iso(), meeting_id),
            )
        return self.get_meeting(meeting_id)

    def update_transcript_version_status(
        self,
        meeting_id: str,
        version_id: str,
        status: str,
        settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: List[Any] = [status, now_iso()]
        if settings is not None:
            fields.append("settings_json = ?")
            values.append(json.dumps(settings, ensure_ascii=False))
        values.extend([meeting_id, version_id])
        with self._lock, self._connect() as conn:
            result = conn.execute(
                f"""
                UPDATE transcript_versions
                SET {', '.join(fields)}
                WHERE meeting_id = ? AND id = ?
                """,
                values,
            )
            if result.rowcount == 0:
                raise KeyError(version_id)

    def delete_segments_for_version(self, meeting_id: str, version_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM segments WHERE meeting_id = ? AND version_id = ?",
                (meeting_id, version_id),
            )

    def create_transcript_version(
        self,
        meeting_id: str,
        version_id: str,
        label: str,
        kind: str,
        settings: Optional[Dict[str, Any]] = None,
        parent_version_id: Optional[str] = None,
        make_current: bool = False,
    ) -> Dict[str, Any]:
        stamp = now_iso()
        with self._lock, self._connect() as conn:
            parent = parent_version_id or self._active_version_id(conn, meeting_id)
            conn.execute(
                """
                INSERT INTO transcript_versions
                (id, meeting_id, label, kind, parent_version_id, status, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    meeting_id,
                    label,
                    kind,
                    parent,
                    "ready",
                    json.dumps(settings or {}, ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            if make_current:
                conn.execute(
                    """
                    UPDATE meetings
                    SET active_version_id = ?, final_markdown = '', final_source_hash = '',
                        final_source_version_id = '', updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, stamp, meeting_id),
                )
        return self.get_meeting(meeting_id)

    def copy_segments_to_version(
        self,
        meeting_id: str,
        source_version_id: str,
        target_version_id: str,
    ) -> int:
        with self._lock, self._connect() as conn:
            self._transcript_version(conn, meeting_id, source_version_id)
            self._transcript_version(conn, meeting_id, target_version_id)
            rows = conn.execute(
                """
                SELECT *
                FROM segments
                WHERE meeting_id = ? AND version_id = ?
                ORDER BY created_at ASC
                """,
                (meeting_id, source_version_id),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO segments
                    (
                        id, meeting_id, version_id, chunk_id, start_ms, end_ms,
                        speaker, text, confidence, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        row["meeting_id"],
                        target_version_id,
                        row["chunk_id"],
                        row["start_ms"],
                        row["end_ms"],
                        row["speaker"],
                        row["text"],
                        row["confidence"],
                        row["created_at"],
                    ),
                )
        return len(rows)

    def get_segments_for_version(self, meeting_id: str, version_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            self._transcript_version(conn, meeting_id, version_id)
            rows = conn.execute(
                """
                SELECT segments.*
                FROM segments
                LEFT JOIN chunks ON chunks.id = segments.chunk_id
                WHERE segments.meeting_id = ? AND segments.version_id = ?
                ORDER BY
                    COALESCE(chunks.started_at_ms, chunks.seq, 0) ASC,
                    segments.start_ms ASC,
                    segments.created_at ASC
                """,
                (meeting_id, version_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_editable_version(
        self,
        meeting_id: str,
        source_version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        stamp = now_iso()
        compact_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        version_id = f"manual-{compact_stamp}-{uuid.uuid4().hex[:6]}"
        label = f"手动编辑 {compact_stamp}"
        with self._lock, self._connect() as conn:
            meeting = conn.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if meeting is None:
                raise KeyError(meeting_id)
            source = source_version_id or self._active_version_id(conn, meeting_id)
            self._transcript_version(conn, meeting_id, source)
            conn.execute(
                """
                INSERT INTO transcript_versions
                (id, meeting_id, label, kind, parent_version_id, status, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    meeting_id,
                    label,
                    "manual-edit",
                    source,
                    "ready",
                    json.dumps({"source_version_id": source}, ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            rows = conn.execute(
                """
                SELECT *
                FROM segments
                WHERE meeting_id = ? AND version_id = ?
                ORDER BY created_at ASC
                """,
                (meeting_id, source),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO segments
                    (
                        id, meeting_id, version_id, chunk_id, start_ms, end_ms,
                        speaker, text, confidence, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        row["meeting_id"],
                        version_id,
                        row["chunk_id"],
                        row["start_ms"],
                        row["end_ms"],
                        row["speaker"],
                        row["text"],
                        row["confidence"],
                        row["created_at"],
                    ),
                )
            conn.execute(
                """
                UPDATE transcript_versions
                SET settings_json = ?, updated_at = ?
                WHERE meeting_id = ? AND id = ?
                """,
                (
                    json.dumps(
                        {"source_version_id": source, "copied_segments": len(rows)},
                        ensure_ascii=False,
                    ),
                    stamp,
                    meeting_id,
                    version_id,
                ),
            )
            conn.execute(
                """
                UPDATE meetings
                SET active_version_id = ?, final_markdown = '', final_source_hash = '',
                    final_source_version_id = '', updated_at = ?
                WHERE id = ?
                """,
                (version_id, stamp, meeting_id),
            )
        return self.get_meeting(meeting_id)

    def update_segment(
        self,
        meeting_id: str,
        version_id: str,
        segment_id: str,
        text: Optional[str] = None,
        speaker: Optional[str] = None,
    ) -> Dict[str, Any]:
        assignments = []
        values: List[Any] = []
        if text is not None:
            assignments.append("text = ?")
            values.append(text.strip())
        if speaker is not None:
            assignments.append("speaker = ?")
            values.append(speaker.strip())
        if not assignments:
            return self.get_meeting(meeting_id)

        values.extend([meeting_id, version_id, segment_id])
        with self._lock, self._connect() as conn:
            self._transcript_version(conn, meeting_id, version_id)
            result = conn.execute(
                f"""
                UPDATE segments
                SET {', '.join(assignments)}
                WHERE meeting_id = ? AND version_id = ? AND id = ?
                """,
                values,
            )
            if result.rowcount == 0:
                raise KeyError(segment_id)
            stamp = now_iso()
            conn.execute(
                """
                UPDATE transcript_versions
                SET updated_at = ?
                WHERE meeting_id = ? AND id = ?
                """,
                (stamp, meeting_id, version_id),
            )
            conn.execute(
                """
                UPDATE meetings
                SET final_markdown = '', final_source_hash = '', final_source_version_id = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (stamp, meeting_id),
            )
        return self.get_meeting(meeting_id)

    def rename_speaker_in_version(
        self,
        meeting_id: str,
        version_id: str,
        old_label: str,
        new_label: str,
    ) -> Dict[str, Any]:
        old_clean = old_label.strip()
        new_clean = new_label.strip()
        if not old_clean or not new_clean:
            raise ValueError("Speaker labels cannot be empty")
        stamp = now_iso()
        with self._lock, self._connect() as conn:
            self._transcript_version(conn, meeting_id, version_id)
            result = conn.execute(
                """
                UPDATE segments
                SET speaker = ?
                WHERE meeting_id = ? AND version_id = ? AND speaker = ?
                """,
                (new_clean, meeting_id, version_id, old_clean),
            )
            if result.rowcount == 0:
                raise KeyError(old_clean)
            conn.execute(
                """
                UPDATE transcript_versions
                SET updated_at = ?
                WHERE meeting_id = ? AND id = ?
                """,
                (stamp, meeting_id, version_id),
            )
            conn.execute(
                """
                UPDATE meetings
                SET final_markdown = '', final_source_hash = '', final_source_version_id = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (stamp, meeting_id),
            )
        return self.get_meeting(meeting_id)

    def delete_meeting(self, meeting_id: str) -> None:
        with self._lock, self._connect() as conn:
            meeting = conn.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if meeting is None:
                raise KeyError(meeting_id)
            conn.execute("DELETE FROM final_notes WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM speakers WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM transcript_versions WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM chunks WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

        shutil.rmtree(CHUNKS_DIR / meeting_id, ignore_errors=True)

    def next_chunk_seq(self, meeting_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM chunks WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return int(row["next_seq"])

    def create_chunk(
        self,
        meeting_id: str,
        audio_bytes: bytes,
        filename: str,
        mime_type: str,
        duration_ms: Optional[int],
        client_chunk_id: str = "",
        started_at_ms: Optional[int] = None,
        ended_at_ms: Optional[int] = None,
        cut_reason: str = "",
    ) -> Dict[str, Any]:
        seq = self.next_chunk_seq(meeting_id)
        chunk_id = uuid.uuid4().hex
        meeting_dir = CHUNKS_DIR / meeting_id
        meeting_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(filename).suffix or ".webm"
        audio_path = meeting_dir / f"{seq:05d}_{chunk_id}{suffix}"
        audio_path.write_bytes(audio_bytes)

        stamp = now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chunks
                (
                    id, meeting_id, seq, client_chunk_id, started_at_ms, ended_at_ms,
                    cut_reason, audio_path, mime_type, created_at, duration_ms, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    meeting_id,
                    seq,
                    client_chunk_id,
                    started_at_ms,
                    ended_at_ms,
                    cut_reason,
                    str(audio_path),
                    mime_type,
                    stamp,
                    duration_ms,
                    "saved",
                ),
            )
        return self.get_chunk(chunk_id)

    def get_chunk(self, chunk_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row is None:
                raise KeyError(chunk_id)
        return dict(row)

    def update_chunk(self, chunk_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"wav_path", "status", "error", "duration_ms"}
        assignments = []
        values = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(chunk_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE chunks SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def add_segments(
        self,
        meeting_id: str,
        chunk_id: str,
        segments: List[Dict[str, Any]],
        version_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        inserted = []
        with self._lock, self._connect() as conn:
            resolved_version_id = version_id or self._active_version_id(conn, meeting_id)
            for segment in segments:
                segment_id = uuid.uuid4().hex
                stamp = now_iso()
                row = {
                    "id": segment_id,
                    "meeting_id": meeting_id,
                    "version_id": resolved_version_id,
                    "chunk_id": chunk_id,
                    "start_ms": int(segment.get("start_ms") or 0),
                    "end_ms": int(segment.get("end_ms") or 0),
                    "speaker": str(segment.get("speaker") or ""),
                    "text": str(segment.get("text") or "").strip(),
                    "confidence": segment.get("confidence"),
                    "created_at": stamp,
                }
                if not row["text"]:
                    continue
                conn.execute(
                    """
                    INSERT INTO segments
                    (
                        id, meeting_id, version_id, chunk_id, start_ms, end_ms,
                        speaker, text, confidence, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["meeting_id"],
                        row["version_id"],
                        row["chunk_id"],
                        row["start_ms"],
                        row["end_ms"],
                        row["speaker"],
                        row["text"],
                        row["confidence"],
                        row["created_at"],
                    ),
                )
                inserted.append(row)
        return inserted

    def list_speakers(self, meeting_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM speakers
                WHERE meeting_id = ?
                ORDER BY created_at ASC
                """,
                (meeting_id,),
            ).fetchall()
        speakers = []
        for row in rows:
            item = dict(row)
            try:
                item["embedding"] = json.loads(item.pop("embedding_json") or "[]")
            except Exception:
                item["embedding"] = []
            speakers.append(item)
        return speakers

    def delete_speakers(self, meeting_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM speakers WHERE meeting_id = ?", (meeting_id,))
            conn.execute(
                "UPDATE meetings SET updated_at = ? WHERE id = ?",
                (now_iso(), meeting_id),
            )

    def create_speaker(self, meeting_id: str, embedding: List[float]) -> Dict[str, Any]:
        existing = self.list_speakers(meeting_id)
        label = f"Speaker {len(existing) + 1}"
        speaker_id = uuid.uuid4().hex
        stamp = now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO speakers
                (id, meeting_id, label, embedding_json, sample_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    speaker_id,
                    meeting_id,
                    label,
                    json.dumps(embedding),
                    1,
                    stamp,
                    stamp,
                ),
            )
        return {
            "id": speaker_id,
            "meeting_id": meeting_id,
            "label": label,
            "embedding": embedding,
            "sample_count": 1,
            "created_at": stamp,
            "updated_at": stamp,
        }

    def update_speaker_embedding(
        self,
        speaker_id: str,
        embedding: List[float],
        sample_count: int,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE speakers
                SET embedding_json = ?, sample_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(embedding), int(sample_count), now_iso(), speaker_id),
            )

    def clear_final_markdown(self, meeting_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE meetings
                SET final_markdown = '', final_source_hash = '', final_source_version_id = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso(), meeting_id),
            )

    def set_summary(
        self,
        meeting_id: str,
        summary: Dict[str, Any],
        source: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged = dict(DEFAULT_SUMMARY)
        merged.update(summary or {})
        source_hash = str((source or {}).get("hash") or "")
        source_version_id = str((source or {}).get("version_id") or "")
        source_segment_count = int((source or {}).get("segment_count") or 0)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE meetings
                SET summary_json = ?, summary_source_hash = ?, summary_source_version_id = ?,
                    summary_segment_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(merged, ensure_ascii=False),
                    source_hash,
                    source_version_id,
                    source_segment_count,
                    now_iso(),
                    meeting_id,
                ),
            )

    def set_final_markdown(
        self,
        meeting_id: str,
        markdown: str,
        source: Optional[Dict[str, Any]] = None,
    ) -> None:
        source_hash = str((source or {}).get("hash") or "")
        source_version_id = str((source or {}).get("version_id") or "")
        stamp = now_iso()
        with self._lock, self._connect() as conn:
            if source_hash and source_version_id:
                conn.execute(
                    """
                    INSERT INTO final_notes
                    (meeting_id, version_id, source_hash, markdown, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(meeting_id, version_id, source_hash)
                    DO UPDATE SET markdown = excluded.markdown, updated_at = excluded.updated_at
                    """,
                    (meeting_id, source_version_id, source_hash, markdown, stamp, stamp),
                )
            conn.execute(
                """
                UPDATE meetings
                SET final_markdown = ?, final_source_hash = ?, final_source_version_id = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                (markdown, source_hash, source_version_id, "completed", stamp, meeting_id),
            )
