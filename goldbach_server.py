#!/usr/bin/env python3
"""
Goldbach Community Verification Server

Lightweight coordination server for distributed Goldbach verification.
Assigns range chunks to clients, tracks completion, maintains waterline.

Usage:
    python3 goldbach_server.py [--port 8080] [--chunk-size 100000000000]

Requires: Python 3.6+, psycopg2 (pip install psycopg2-binary)
"""

import json
import time
import os
import sys
import uuid
import threading
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

# Configuration
RECORD_START = 4_000_000_000_000_000_000  # Current world record
DEFAULT_CHUNK_SIZE = 1_000_000_000        # 10^9 even numbers = ~30s at 4×10^18 scale
DEADLINE_SECONDS = 86400                   # 24 hours to complete a chunk
MAX_OUTSTANDING = 2                        # Max assigned chunks per client
MAX_IMPORT_CHUNKS = 10000                  # Max chunks per import request
MAX_CERT_BATCH = 5000                      # Max certs per import-certs request
MAX_REQUEST_SIZE = 10 * 1024 * 1024        # 10 MB max request body
BASE_CORES = 8
DB_DSN = os.environ.get("DATABASE_URL", "dbname=goldbach")

# ============================================================================
# DATABASE
# ============================================================================

def get_db():
    """Get a PostgreSQL connection."""
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    return conn

def dict_row(cursor):
    """Convert cursor row to dict."""
    if cursor.description is None:
        return None
    cols = [d[0] for d in cursor.description]
    def make_dict(row):
        if row is None:
            return None
        return dict(zip(cols, row))
    return make_dict

def fetchone_dict(cur):
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))

def fetchall_dict(cur):
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]

def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id SERIAL PRIMARY KEY,
            range_start TEXT NOT NULL,
            range_end TEXT NOT NULL,
            status TEXT DEFAULT 'unclaimed',
            import_id INTEGER,
            client_a TEXT,
            hash_a TEXT,
            submitted_a_at DOUBLE PRECISION,
            client_b TEXT,
            hash_b TEXT,
            submitted_b_at DOUBLE PRECISION,
            assigned_to TEXT,
            assigned_at DOUBLE PRECISION,
            deadline DOUBLE PRECISION,
            verified_at DOUBLE PRECISION
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            registered_at DOUBLE PRECISION,
            chunks_completed INTEGER DEFAULT 0,
            chunks_failed INTEGER DEFAULT 0,
            last_seen DOUBLE PRECISION
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS waterline (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            value TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS imports (
            id SERIAL PRIMARY KEY,
            client_id TEXT NOT NULL,
            submitted_at DOUBLE PRECISION,
            chunk_count INTEGER DEFAULT 0,
            verified_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS certificates (
            id SERIAL PRIMARY KEY,
            n TEXT NOT NULL UNIQUE,
            p TEXT NOT NULL,
            q TEXT NOT NULL,
            mode TEXT,
            client_id TEXT,
            submitted_at DOUBLE PRECISION,
            verified INTEGER DEFAULT 0
        )
    """)
    # Create indexes for common queries
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_range ON chunks(range_start, range_end)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_import ON chunks(import_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_assigned ON chunks(assigned_to)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_imports_status ON imports(status)")

    # Initialize waterline if not exists
    cur.execute("SELECT COUNT(*) FROM waterline")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO waterline (id, value) VALUES (1, %s)", (str(RECORD_START),))
    conn.commit()
    cur.close()
    return conn


def ensure_chunks(conn, count=1000):
    """Ensure at least `count` unclaimed chunks exist ahead of the waterline."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(CAST(range_end AS BIGINT)) FROM chunks")
    row = cur.fetchone()
    last = row[0] if row[0] is not None else RECORD_START

    cur.execute("SELECT COUNT(*) FROM chunks WHERE status = 'unclaimed'")
    existing_unclaimed = cur.fetchone()[0]

    if existing_unclaimed < count:
        to_add = count - existing_unclaimed
        cursor = int(last)
        for _ in range(to_add):
            start = cursor
            end = cursor + DEFAULT_CHUNK_SIZE
            cur.execute(
                "INSERT INTO chunks (range_start, range_end) VALUES (%s, %s)",
                (str(start), str(end))
            )
            cursor = end
        conn.commit()
    cur.close()


def expire_stale(conn):
    """Return stale assigned chunks to unclaimed."""
    now = time.time()
    cur = conn.cursor()
    cur.execute("""
        UPDATE chunks SET status = 'unclaimed', assigned_to = NULL, deadline = NULL
        WHERE status = 'assigned' AND deadline < %s
    """, (now,))
    count = cur.rowcount
    if count > 0:
        conn.commit()
    cur.close()
    return count


def update_waterline(conn):
    """Advance waterline to highest contiguous verified chunk."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM waterline WHERE id = 1")
    waterline = int(cur.fetchone()[0])

    cur.execute("""
        SELECT range_start, range_end FROM chunks
        WHERE status = 'verified'
        AND CAST(range_start AS BIGINT) >= %s
        ORDER BY CAST(range_start AS BIGINT) ASC
    """, (waterline,))

    for row in cur.fetchall():
        chunk_start = int(row[0])
        chunk_end = int(row[1])
        if chunk_start <= waterline:
            waterline = max(waterline, chunk_end)
        else:
            break

    cur.execute("UPDATE waterline SET value = %s WHERE id = 1", (str(waterline),))
    conn.commit()
    cur.close()
    return waterline


# ============================================================================
# HTTP SERVER
# ============================================================================

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread."""
    daemon_threads = True


# ============================================================================
# BACKGROUND VERIFICATION — spot-checks imported manifests
# ============================================================================

SPOT_CHECK_COUNT = 100

def run_background_verification():
    """Background thread that spot-checks pending imports, one batch at a time."""
    while True:
        time.sleep(10)
        conn = None
        try:
            conn = get_db()
            cur = conn.cursor()

            # Find the oldest pending import batch
            cur.execute("""
                SELECT * FROM imports WHERE status = 'pending'
                ORDER BY submitted_at ASC LIMIT 1
            """)
            batch = fetchone_dict(cur)

            if not batch:
                cur.close()
                conn.close()
                continue

            import_id = batch['id']
            client_id = batch['client_id']

            cur.execute("""
                SELECT COUNT(*) FROM chunks
                WHERE import_id = %s AND status = 'needs_doublecheck'
            """, (import_id,))
            pending_chunks = cur.fetchone()[0]

            if pending_chunks == 0:
                cur.execute("UPDATE imports SET status = 'complete' WHERE id = %s", (import_id,))
                conn.commit()
                cur.close()
                conn.close()
                continue

            to_check = min(pending_chunks, SPOT_CHECK_COUNT)
            cur.execute("""
                SELECT id, range_start, range_end, hash_a FROM chunks
                WHERE import_id = %s AND status = 'needs_doublecheck'
                ORDER BY RANDOM() LIMIT %s
            """, (import_id, to_check))
            chunks = fetchall_dict(cur)

            cur.execute("UPDATE imports SET status = 'verifying' WHERE id = %s", (import_id,))
            conn.commit()

            print(f"\n[VERIFIER] Import #{import_id} from {client_id}: "
                  f"spot-checking {len(chunks)}/{pending_chunks} chunks...")

            passed = 0
            failed = 0

            for chunk in chunks:
                start = chunk['range_start']
                end = chunk['range_end']
                expected = chunk['hash_a']
                chunk_id = chunk['id']

                cmd = f"./goldbach --range {start} {end} --fast 2>/dev/null"
                try:
                    result = subprocess.run(cmd, shell=True, capture_output=True,
                                          text=True, timeout=600)
                    actual_hash = ""
                    for line in result.stdout.split('\n'):
                        if 'sha256=' in line:
                            actual_hash = line.split('sha256=')[-1].strip().split()[0]

                    if actual_hash == expected:
                        cur.execute("""
                            UPDATE chunks SET hash_b = %s, client_b = 'server_verifier',
                            submitted_b_at = %s, status = 'verified', verified_at = %s
                            WHERE id = %s
                        """, (actual_hash, time.time(), time.time(), chunk_id))
                        conn.commit()
                        passed += 1
                        print(f"  [PASS] Chunk {chunk_id}: {start}-{end}")
                    else:
                        cur.execute("""
                            UPDATE chunks SET hash_b = %s, client_b = 'server_verifier',
                            submitted_b_at = %s, status = 'disputed'
                            WHERE id = %s
                        """, (actual_hash, time.time(), chunk_id))
                        conn.commit()
                        failed += 1
                        print(f"  [FAIL] Chunk {chunk_id}: {start}-{end}")
                        print(f"    Expected: {expected}")
                        print(f"    Got:      {actual_hash}")
                except subprocess.TimeoutExpired:
                    print(f"  [TIMEOUT] Chunk {chunk_id}: {start}-{end}")
                    failed += 1

            if failed == 0 and passed > 0:
                cur.execute("""
                    UPDATE chunks SET status = 'verified', verified_at = %s,
                    client_b = 'server_verifier_bulk', hash_b = hash_a
                    WHERE import_id = %s AND status = 'needs_doublecheck'
                """, (time.time(), import_id))
                bulk_count = cur.rowcount
                cur.execute("""
                    UPDATE imports SET status = 'verified',
                    verified_count = %s, failed_count = 0
                    WHERE id = %s
                """, (passed + bulk_count, import_id))
                conn.commit()
                waterline = update_waterline(conn)
                print(f"  [VERIFIER] Import #{import_id}: ALL {passed} spot-checks passed. "
                      f"Bulk-verified {bulk_count} remaining. Waterline: {waterline}")
            else:
                cur.execute("""
                    UPDATE imports SET status = 'rejected',
                    verified_count = %s, failed_count = %s
                    WHERE id = %s
                """, (passed, failed, import_id))
                conn.commit()
                print(f"  [VERIFIER] Import #{import_id}: REJECTED — "
                      f"{failed} failures, {passed} passed.")

            cur.close()
            conn.close()

        except Exception as e:
            print(f"[VERIFIER] Error: {e}")
            import traceback
            traceback.print_exc()
            if conn:
                try: conn.close()
                except: pass


class GoldbachHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
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
        conn = get_db()
        cur = conn.cursor()

        try:
            if parsed.path == '/api/claim':
                client_id = params.get('client', [None])[0]
                if not client_id:
                    self.send_json({"error": "client parameter required"}, 400)
                    return

                # Register client if new
                cur.execute("SELECT client_id FROM clients WHERE client_id = %s", (client_id,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO clients (client_id, registered_at, last_seen) VALUES (%s, %s, %s)",
                               (client_id, time.time(), time.time()))
                else:
                    cur.execute("UPDATE clients SET last_seen = %s WHERE client_id = %s",
                               (time.time(), client_id))

                expire_stale(conn)

                # Cap outstanding chunks per client
                cur.execute(
                    "SELECT COUNT(*) FROM chunks WHERE assigned_to = %s AND status = 'assigned'",
                    (client_id,))
                outstanding = cur.fetchone()[0]
                if outstanding >= MAX_OUTSTANDING:
                    self.send_json({"error": "max outstanding chunks reached", "outstanding": outstanding}, 429)
                    return

                # Priority 1: chunks needing double-check
                cur.execute("""
                    SELECT id, range_start, range_end FROM chunks
                    WHERE status = 'needs_doublecheck' AND client_a != %s
                    ORDER BY CAST(range_start AS BIGINT) ASC LIMIT 1
                """, (client_id,))
                chunk = fetchone_dict(cur)

                if not chunk:
                    ensure_chunks(conn)
                    cur.execute("""
                        SELECT id, range_start, range_end FROM chunks
                        WHERE status = 'unclaimed'
                        ORDER BY CAST(range_start AS BIGINT) ASC LIMIT 1
                    """)
                    chunk = fetchone_dict(cur)

                if not chunk:
                    self.send_json({"error": "no chunks available"})
                    return

                # Assign atomically with FOR UPDATE
                deadline = time.time() + DEADLINE_SECONDS
                cur.execute("""
                    UPDATE chunks SET status = 'assigned', assigned_to = %s,
                    assigned_at = %s, deadline = %s
                    WHERE id = %s AND status IN ('unclaimed', 'needs_doublecheck')
                """, (client_id, time.time(), deadline, chunk['id']))
                conn.commit()

                if cur.rowcount == 0:
                    self.send_json({"error": "chunk was claimed by another client, retry"}, 409)
                    return

                self.send_json({
                    "chunk_id": chunk['id'],
                    "start": chunk['range_start'],
                    "end": chunk['range_end'],
                    "deadline": int(deadline)
                })

            elif parsed.path == '/api/status':
                waterline = update_waterline(conn)
                cur.execute("SELECT COUNT(*) FROM chunks WHERE status = 'verified'")
                total_verified = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks")
                total_chunks = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM clients WHERE last_seen > %s",
                           (time.time() - 3600,))
                active_clients = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks WHERE status IN ('assigned', 'needs_doublecheck')")
                pending = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks WHERE status = 'unclaimed'")
                unclaimed = cur.fetchone()[0]

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

            elif parsed.path == '/api/config':
                self.send_json({
                    "chunk_size": str(DEFAULT_CHUNK_SIZE),
                    "record_start": str(RECORD_START),
                })

            elif parsed.path == '/api/import-status':
                client_id = params.get('client', [None])[0]
                if not client_id:
                    cur.execute("SELECT * FROM imports ORDER BY submitted_at DESC LIMIT 50")
                else:
                    cur.execute("SELECT * FROM imports WHERE client_id = %s ORDER BY submitted_at DESC LIMIT 50",
                               (client_id,))
                imports = fetchall_dict(cur)

                cur.execute("SELECT COUNT(*) FROM imports WHERE status = 'pending'")
                queue_depth = cur.fetchone()[0]

                self.send_json({
                    "queue_depth": queue_depth,
                    "imports": [{
                        "id": imp['id'],
                        "client_id": imp['client_id'],
                        "chunk_count": imp['chunk_count'],
                        "verified_count": imp['verified_count'],
                        "failed_count": imp['failed_count'],
                        "status": imp['status'],
                        "submitted_at": imp['submitted_at']
                    } for imp in imports]
                })

            elif parsed.path == '/api/spot-check':
                count = int(params.get('count', ['10'])[0])
                if count > 500:
                    count = 500
                cur.execute("""
                    SELECT range_start, range_end, hash_a, hash_b
                    FROM chunks WHERE status = 'verified'
                    ORDER BY RANDOM() LIMIT %s
                """, (count,))
                chunks = fetchall_dict(cur)
                self.send_json({
                    "count": len(chunks),
                    "chunks": [{"start": c['range_start'], "end": c['range_end'],
                                "hash_a": c['hash_a'], "hash_b": c['hash_b']}
                               for c in chunks]
                })

            elif parsed.path == '/api/health':
                cur.execute("SELECT COUNT(*) FROM chunks")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM imports WHERE status = 'pending'")
                queue = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM clients WHERE last_seen > %s",
                           (time.time() - 3600,))
                active = cur.fetchone()[0]
                self.send_json({
                    "status": "ok",
                    "total_chunks": total,
                    "pending_imports": queue,
                    "active_clients": active
                })

            else:
                self.send_json({"error": "not found"}, 404)

        finally:
            cur.close()
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > MAX_REQUEST_SIZE:
            self.send_json({"error": f"request too large (max {MAX_REQUEST_SIZE} bytes)"}, 413)
            return

        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid JSON"}, 400)
            return

        conn = get_db()
        cur = conn.cursor()

        try:
            if parsed.path == '/api/submit':
                chunk_id = data.get('chunk_id')
                sha256 = data.get('sha256')
                client_id = data.get('client_id')

                if not all([chunk_id, sha256, client_id]):
                    self.send_json({"error": "chunk_id, sha256, and client_id required"}, 400)
                    return

                cur.execute("SELECT * FROM chunks WHERE id = %s", (chunk_id,))
                chunk = fetchone_dict(cur)
                if not chunk:
                    self.send_json({"error": "chunk not found"}, 404)
                    return

                if not chunk['hash_a']:
                    cur.execute("""
                        UPDATE chunks SET hash_a = %s, client_a = %s, submitted_a_at = %s,
                        status = 'needs_doublecheck', assigned_to = NULL
                        WHERE id = %s
                    """, (sha256, client_id, time.time(), chunk_id))
                    conn.commit()
                    self.send_json({"status": "needs_doublecheck", "message": "first submission recorded"})

                elif chunk['client_a'] != client_id:
                    cur.execute("""
                        UPDATE chunks SET hash_b = %s, client_b = %s, submitted_b_at = %s
                        WHERE id = %s
                    """, (sha256, client_id, time.time(), chunk_id))

                    if chunk['hash_a'] == sha256:
                        cur.execute("UPDATE chunks SET status = 'verified', verified_at = %s WHERE id = %s",
                                   (time.time(), chunk_id))
                        cur.execute("UPDATE clients SET chunks_completed = chunks_completed + 1 WHERE client_id = %s",
                                   (chunk['client_a'],))
                        cur.execute("UPDATE clients SET chunks_completed = chunks_completed + 1 WHERE client_id = %s",
                                   (client_id,))
                        conn.commit()
                        waterline = update_waterline(conn)
                        self.send_json({
                            "status": "verified",
                            "message": "hashes match — chunk verified!",
                            "waterline": str(waterline)
                        })
                    else:
                        cur.execute("UPDATE chunks SET status = 'disputed' WHERE id = %s", (chunk_id,))
                        conn.commit()
                        self.send_json({
                            "status": "disputed",
                            "message": "hash mismatch — assigned to third party for resolution"
                        })
                else:
                    self.send_json({"error": "you already submitted for this chunk"}, 400)

            elif parsed.path == '/api/check-chunks':
                chunks_data = data.get('chunks', [])
                already_verified = 0
                needs_doublecheck = 0
                new_chunks = 0
                needed = []

                for chunk in chunks_data:
                    start = chunk.get('start', '')
                    end = chunk.get('end', '')
                    if not start or not end:
                        continue

                    cur.execute(
                        "SELECT status FROM chunks WHERE range_start = %s AND range_end = %s",
                        (start, end))
                    existing = cur.fetchone()

                    if existing:
                        if existing[0] == 'verified':
                            already_verified += 1
                        elif existing[0] == 'needs_doublecheck':
                            needs_doublecheck += 1
                        else:
                            needed.append({"start": start, "end": end})
                            new_chunks += 1
                    else:
                        needed.append({"start": start, "end": end})
                        new_chunks += 1

                self.send_json({
                    "total": len(chunks_data),
                    "already_verified": already_verified,
                    "needs_doublecheck": needs_doublecheck,
                    "new": new_chunks,
                    "needed": needed,
                    "message": f"{already_verified} already verified, "
                               f"{needs_doublecheck} awaiting double-check, "
                               f"{new_chunks} new chunks to upload"
                })

            elif parsed.path == '/api/import':
                client_id = data.get('client_id', 'import')
                chunks_data = data.get('chunks', [])

                if not chunks_data:
                    self.send_json({"error": "chunks array required"}, 400)
                    return

                if len(chunks_data) > MAX_IMPORT_CHUNKS:
                    self.send_json({"error": f"too many chunks (max {MAX_IMPORT_CHUNKS})"}, 400)
                    return

                # Create import batch
                cur.execute("""
                    INSERT INTO imports (client_id, submitted_at, chunk_count, status)
                    VALUES (%s, %s, %s, 'pending') RETURNING id
                """, (client_id, time.time(), len(chunks_data)))
                import_id = cur.fetchone()[0]

                imported = 0
                skipped = 0
                for chunk in chunks_data:
                    start = chunk.get('start', '')
                    end = chunk.get('end', '')
                    sha256 = chunk.get('sha256', '')
                    if not all([start, end, sha256]):
                        continue

                    if len(sha256) != 64 or not all(c in '0123456789abcdef' for c in sha256):
                        continue

                    try:
                        s, e = int(start), int(end)
                        if s < RECORD_START or e <= s or (e - s) > DEFAULT_CHUNK_SIZE * 2:
                            continue
                    except ValueError:
                        continue

                    cur.execute(
                        "SELECT id, hash_a FROM chunks WHERE range_start = %s AND range_end = %s",
                        (start, end))
                    existing = cur.fetchone()

                    if existing:
                        if existing[1]:  # hash_a already set
                            skipped += 1
                            continue
                        cur.execute("""
                            UPDATE chunks SET hash_a = %s, client_a = %s, submitted_a_at = %s,
                            status = 'needs_doublecheck', assigned_to = NULL, import_id = %s
                            WHERE id = %s
                        """, (sha256, client_id, time.time(), import_id, existing[0]))
                    else:
                        cur.execute("""
                            INSERT INTO chunks (range_start, range_end, hash_a, client_a,
                            submitted_a_at, status, import_id)
                            VALUES (%s, %s, %s, %s, %s, 'needs_doublecheck', %s)
                        """, (start, end, sha256, client_id, time.time(), import_id))
                    imported += 1

                cur.execute("UPDATE imports SET chunk_count = %s WHERE id = %s", (imported, import_id))
                conn.commit()
                self.send_json({
                    "status": "ok",
                    "imported": imported,
                    "skipped": skipped,
                    "message": f"{imported} chunks imported, {skipped} already had submissions"
                })

            elif parsed.path == '/api/import-certs':
                client_id = data.get('client_id', 'import')
                certs = data.get('certs', [])
                mode = data.get('mode', 'unknown')

                if not certs:
                    self.send_json({"error": "certs array required"}, 400)
                    return

                if len(certs) > MAX_CERT_BATCH:
                    self.send_json({"error": f"too many certs (max {MAX_CERT_BATCH})"}, 400)
                    return

                for cert in certs:
                    n = cert.get('n', '')
                    p = cert.get('p', '')
                    q = cert.get('q', '')
                    if n and p and q:
                        cur.execute("""
                            INSERT INTO certificates (n, p, q, mode, client_id, submitted_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (n) DO NOTHING
                        """, (n, p, q, mode, client_id, time.time()))

                conn.commit()
                self.send_json({
                    "status": "ok",
                    "imported": len(certs),
                    "message": f"{len(certs)} certificates imported"
                })

            else:
                self.send_json({"error": "not found"}, 404)

        finally:
            cur.close()
            conn.close()


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
    print(f"  Database:     PostgreSQL ({DB_DSN})")
    print(f"  Port:         {port}")
    print()

    conn = init_db()
    ensure_chunks(conn)
    waterline = update_waterline(conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chunks")
    total = cur.fetchone()[0]
    cur.close()
    print(f"  Waterline:    {waterline:,}")
    print(f"  Chunks ready: {total}")
    conn.close()

    print(f"\n  Server running on http://localhost:{port}")
    print(f"  Endpoints:")
    print(f"    GET  /api/claim?client=ID    — claim a chunk")
    print(f"    POST /api/submit             — submit result")
    print(f"    GET  /api/status             — view progress")
    print(f"    GET  /api/config             — get chunk size and config")
    print(f"    POST /api/import             — import offline manifest chunks")
    print(f"    POST /api/import-certs       — import beyond/suspect certificates")
    print(f"    POST /api/check-chunks       — pre-flight check for duplicates")
    print(f"    GET  /api/import-status      — check import queue status")
    print(f"    GET  /api/spot-check?count=N — get random verified chunks for audit")
    print(f"    GET  /api/health             — server health check")
    print()

    # Start background verification thread
    verifier = threading.Thread(target=run_background_verification, daemon=True)
    verifier.start()
    print(f"  Background verifier: running (spot-checks {SPOT_CHECK_COUNT} chunks per import)")

    server = ThreadedHTTPServer(('', port), GoldbachHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == '__main__':
    main()
