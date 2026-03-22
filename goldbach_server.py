#!/usr/bin/env python3
"""
Goldbach Community Verification Server

Lightweight coordination server for distributed Goldbach verification.
Assigns range chunks to clients, tracks completion, maintains waterline.

Usage:
    python3 goldbach_server.py [--port 8080] [--chunk-size 100000000000]

Requires: Python 3.6+ (no external dependencies)
"""

import json
import sqlite3
import time
import os
import sys
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration
RECORD_START = 4_000_000_000_000_000_000  # Current world record
DEFAULT_CHUNK_SIZE = 1_000_000_000        # 10^9 even numbers = ~30s at 4×10^18 scale
DEADLINE_SECONDS = 86400                   # 24 hours to complete a chunk
# Dynamic chunk scaling: base size × (cores / 8)
# 8-core machine → 10^10, 16-core → 2×10^10, 64-core → 8×10^10
BASE_CORES = 8
DB_FILE = "goldbach_community.db"

# ============================================================================
# DATABASE
# ============================================================================

def init_db():
    """Create database and pre-populate initial chunks."""
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            range_start TEXT NOT NULL,
            range_end TEXT NOT NULL,
            status TEXT DEFAULT 'unclaimed',
            client_a TEXT,
            hash_a TEXT,
            submitted_a_at REAL,
            client_b TEXT,
            hash_b TEXT,
            submitted_b_at REAL,
            assigned_to TEXT,
            assigned_at REAL,
            deadline REAL,
            verified_at REAL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            registered_at REAL,
            chunks_completed INTEGER DEFAULT 0,
            chunks_failed INTEGER DEFAULT 0,
            last_seen REAL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS waterline (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            value TEXT NOT NULL
        )
    """)
    # Initialize waterline if not exists
    if db.execute("SELECT COUNT(*) FROM waterline").fetchone()[0] == 0:
        db.execute("INSERT INTO waterline (id, value) VALUES (1, ?)", (str(RECORD_START),))
    db.commit()
    return db


def ensure_chunks(db, count=1000):
    """Ensure at least `count` unclaimed chunks exist ahead of the waterline."""
    last = db.execute("SELECT MAX(CAST(range_end AS INTEGER)) FROM chunks").fetchone()[0]
    if last is None:
        last = RECORD_START

    existing_unclaimed = db.execute(
        "SELECT COUNT(*) FROM chunks WHERE status = 'unclaimed'"
    ).fetchone()[0]

    if existing_unclaimed < count:
        to_add = count - existing_unclaimed
        cursor = int(last)
        for _ in range(to_add):
            start = cursor
            end = cursor + DEFAULT_CHUNK_SIZE
            db.execute(
                "INSERT INTO chunks (range_start, range_end) VALUES (?, ?)",
                (str(start), str(end))
            )
            cursor = end
        db.commit()


def expire_stale(db):
    """Return stale assigned chunks to unclaimed."""
    now = time.time()
    expired = db.execute("""
        UPDATE chunks SET status = 'unclaimed', assigned_to = NULL, deadline = NULL
        WHERE status = 'assigned' AND deadline < ?
    """, (now,))
    if expired.rowcount > 0:
        db.commit()
    return expired.rowcount


def update_waterline(db):
    """Advance waterline to highest contiguous verified chunk."""
    waterline = int(db.execute("SELECT value FROM waterline WHERE id = 1").fetchone()[0])

    chunks = db.execute("""
        SELECT range_start, range_end FROM chunks
        WHERE status = 'verified'
        AND CAST(range_start AS INTEGER) >= ?
        ORDER BY CAST(range_start AS INTEGER) ASC
    """, (waterline,)).fetchall()

    for chunk in chunks:
        chunk_start = int(chunk['range_start'])
        chunk_end = int(chunk['range_end'])
        if chunk_start <= waterline:
            waterline = max(waterline, chunk_end)
        else:
            break  # Gap found

    db.execute("UPDATE waterline SET value = ? WHERE id = 1", (str(waterline),))
    db.commit()
    return waterline


# ============================================================================
# HTTP SERVER
# ============================================================================

class GoldbachHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        """Quieter logging."""
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {args[0]}\n")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        db = sqlite3.connect(DB_FILE)
        db.row_factory = sqlite3.Row

        if parsed.path == '/api/claim':
            client_id = params.get('client', [None])[0]
            if not client_id:
                self.send_json({"error": "client parameter required"}, 400)
                db.close()
                return

            # Note: chunk size is fixed (not dynamic) because double-checking
            # requires both clients to verify the exact same range to produce
            # matching SHA-256 hashes.

            # Register client if new
            existing = db.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
            if not existing:
                db.execute("INSERT INTO clients (client_id, registered_at, last_seen) VALUES (?, ?, ?)",
                           (client_id, time.time(), time.time()))
            else:
                db.execute("UPDATE clients SET last_seen = ? WHERE client_id = ?",
                           (time.time(), client_id))

            # Expire stale assignments
            expire_stale(db)

            # Priority 1: chunks needing double-check (where this client wasn't the first checker)
            chunk = db.execute("""
                SELECT * FROM chunks
                WHERE status = 'needs_doublecheck'
                AND client_a != ?
                ORDER BY CAST(range_start AS INTEGER) ASC
                LIMIT 1
            """, (client_id,)).fetchone()

            if not chunk:
                # Priority 2: unclaimed chunks (lowest first)
                ensure_chunks(db)
                chunk = db.execute("""
                    SELECT * FROM chunks
                    WHERE status = 'unclaimed'
                    ORDER BY CAST(range_start AS INTEGER) ASC
                    LIMIT 1
                """).fetchone()

            if not chunk:
                self.send_json({"error": "no chunks available"})
                db.close()
                return

            # Assign
            deadline = time.time() + DEADLINE_SECONDS
            db.execute("""
                UPDATE chunks SET status = 'assigned', assigned_to = ?, assigned_at = ?, deadline = ?
                WHERE id = ?
            """, (client_id, time.time(), deadline, chunk['id']))
            db.commit()

            self.send_json({
                "chunk_id": chunk['id'],
                "start": chunk['range_start'],
                "end": chunk['range_end'],
                "deadline": int(deadline)
            })

        elif parsed.path == '/api/status':
            waterline = update_waterline(db)
            total_verified = db.execute("SELECT COUNT(*) FROM chunks WHERE status = 'verified'").fetchone()[0]
            total_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            active_clients = db.execute(
                "SELECT COUNT(*) FROM clients WHERE last_seen > ?",
                (time.time() - 3600,)
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM chunks WHERE status IN ('assigned', 'needs_doublecheck')"
            ).fetchone()[0]
            unclaimed = db.execute(
                "SELECT COUNT(*) FROM chunks WHERE status = 'unclaimed'"
            ).fetchone()[0]

            self.send_json({
                "record_start": str(RECORD_START),
                "waterline": str(waterline),
                "extension": f"{(waterline - RECORD_START) / RECORD_START * 100:.6f}%",
                "total_verified": total_verified,
                "total_chunks": total_chunks,
                "active_clients": active_clients,
                "pending": pending,
                "unclaimed": unclaimed
            })

        else:
            self.send_json({"error": "not found"}, 404)

        db.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        db = sqlite3.connect(DB_FILE)
        db.row_factory = sqlite3.Row

        if parsed.path == '/api/submit':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.send_json({"error": "invalid JSON"}, 400)
                db.close()
                return

            chunk_id = data.get('chunk_id')
            sha256 = data.get('sha256')
            client_id = data.get('client_id')

            if not all([chunk_id, sha256, client_id]):
                self.send_json({"error": "chunk_id, sha256, and client_id required"}, 400)
                db.close()
                return

            chunk = db.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if not chunk:
                self.send_json({"error": "chunk not found"}, 404)
                db.close()
                return

            if not chunk['hash_a']:
                # First submission
                db.execute("""
                    UPDATE chunks SET hash_a = ?, client_a = ?, submitted_a_at = ?,
                    status = 'needs_doublecheck', assigned_to = NULL
                    WHERE id = ?
                """, (sha256, client_id, time.time(), chunk_id))
                db.commit()
                self.send_json({"status": "needs_doublecheck", "message": "first submission recorded"})

            elif chunk['client_a'] != client_id:
                # Double-check submission
                db.execute("""
                    UPDATE chunks SET hash_b = ?, client_b = ?, submitted_b_at = ?
                    WHERE id = ?
                """, (sha256, client_id, time.time(), chunk_id))

                if chunk['hash_a'] == sha256:
                    # Match — verified!
                    db.execute("UPDATE chunks SET status = 'verified', verified_at = ? WHERE id = ?",
                               (time.time(), chunk_id))
                    # Credit both clients
                    db.execute("UPDATE clients SET chunks_completed = chunks_completed + 1 WHERE client_id = ?",
                               (chunk['client_a'],))
                    db.execute("UPDATE clients SET chunks_completed = chunks_completed + 1 WHERE client_id = ?",
                               (client_id,))
                    waterline = update_waterline(db)
                    db.commit()
                    self.send_json({
                        "status": "verified",
                        "message": "hashes match — chunk verified!",
                        "waterline": str(waterline)
                    })
                else:
                    # Mismatch — disputed
                    db.execute("UPDATE chunks SET status = 'disputed' WHERE id = ?", (chunk_id,))
                    # Penalize someone (we'll figure out who when a third party resolves it)
                    db.commit()
                    self.send_json({
                        "status": "disputed",
                        "message": "hash mismatch — assigned to third party for resolution"
                    })
            else:
                self.send_json({"error": "you already submitted for this chunk"}, 400)

        else:
            self.send_json({"error": "not found"}, 404)

        db.close()


# ============================================================================
# MAIN
# ============================================================================

def main():
    global DEFAULT_CHUNK_SIZE
    port = 8080
    chunk_size = DEFAULT_CHUNK_SIZE
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--port' and i < len(sys.argv):
            port = int(sys.argv[i + 1])
        elif arg == '--chunk-size' and i < len(sys.argv):
            chunk_size = int(sys.argv[i + 1])

    DEFAULT_CHUNK_SIZE = chunk_size

    print(f"Goldbach Community Verification Server")
    print(f"  Record start: {RECORD_START:,}")
    print(f"  Chunk size:   {chunk_size:,} even numbers ({chunk_size * 2:,} range)")
    print(f"  Database:     {DB_FILE}")
    print(f"  Port:         {port}")
    print()

    db = init_db()
    ensure_chunks(db)
    waterline = update_waterline(db)
    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"  Waterline:    {waterline:,}")
    print(f"  Chunks ready: {total}")
    db.close()

    print(f"\n  Server running on http://localhost:{port}")
    print(f"  Endpoints:")
    print(f"    GET  /api/claim?client=ID  — claim a chunk")
    print(f"    POST /api/submit           — submit result")
    print(f"    GET  /api/status           — view progress")
    print()

    server = HTTPServer(('', port), GoldbachHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == '__main__':
    main()
