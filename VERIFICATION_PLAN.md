# Verification & Proof System — Design Plan

## Overview

A system for offline verification, proof storage, and third-party submission of Goldbach verification results. Supports all modes: exhaustive ranges, beyond-record sampling, suspect/adversarial testing, and custom ranges.

---

## Certificate Formats

### Exhaustive Range: Compact Binary Format

For exhaustive (sequential) verification, each cert is just the small prime `p` that completes the Goldbach pair. Everything else is derived:

- **N** = implied by position (line 0 = range_start, line 1 = range_start + 2, etc.)
- **q** = N - p (derived)
- **p** = typically < 200 (fits in 1 byte as index into small primes list)

**Binary format per chunk:**
```
Header:
  magic:        4 bytes  "GBCH"
  version:      1 byte
  range_start:  16 bytes (uint128, little-endian)
  range_end:    16 bytes (uint128, little-endian)
  count:        8 bytes  (uint64, number of even numbers)
  sha256:       32 bytes (hash of all certs in this chunk)

Body:
  p_index[0]:   1 byte   (index into small primes list)
  p_index[1]:   1 byte
  ...
  p_index[N]:   1 byte

  If p_index == 0xFF: next 2 bytes are the actual prime value (uint16)
  (escape for the rare case p > 254th prime)
```

**Storage estimates for 4e18 → 5e18 (500 billion even numbers):**
- Uncompressed: ~500 GB (1 byte per number)
- Compressed (zstd): ~50-100 GB estimated (p values heavily skewed toward small primes)
- One hard drive. Handable to a university.

**Reconstruction:** Given the binary file and the small primes list, anyone can reconstruct every `N=p+q` cert and verify independently.

### Beyond/Suspect/Custom Sampling: CSV Format

For non-sequential (sampled) results, each cert is a full line:

```csv
N,p,q,mode,verified_by
73213424391040018771418,127,73213424391040018771291,suspect,mr+bpsw
```

These are small files (millions of lines, not billions). No compression needed. Each line is a self-contained proof.

---

## Export Workflow by Mode

### Exhaustive Range (`--range` with `--export`)

```bash
# Single machine
./goldbach --range 4e18 5e18 --fast --export results/

# 50-server cluster: split range
./goldbach --range 4.00e18 4.02e18 --fast --export results_01/
./goldbach --range 4.02e18 4.04e18 --fast --export results_02/
# ... etc
```

**What happens internally:**
1. Program splits range into standard chunks (10^9 each = 500M even numbers)
2. For each chunk:
   - Sieve-verify every even number
   - Record which small prime `p` completed each Goldbach pair
   - Compute SHA-256 of all `N=p+q` cert strings (deterministic, thread-count-independent)
   - Write compact binary chunk file to export directory
   - Append `start,end,sha256` to manifest.csv
3. On completion: compute Merkle root from all chunk hashes, write to manifest

**On interrupt:**
- Completed chunk files and manifest entries survive
- Current in-progress chunk is lost (partial)
- On restart: reads manifest, skips completed chunks, resumes next one
- Zero wasted work except the partial chunk

**Export directory structure:**
```
results/
  manifest.csv          # chunk_start,chunk_end,sha256 per line + Merkle root
  chunk_000001.gbc      # compact binary cert file for chunk 1
  chunk_000002.gbc      # ...
  ...
```

### Beyond Record (`--beyond` with `--export`)

```bash
./goldbach --beyond 10000000 --export beyond_results.csv
```

1. Samples 10M random numbers from 4e18 to 10^38 (log-scale)
2. For each: find pair, dual-verify, write `N,p,q` to CSV in real-time
3. Each line survives a crash — written immediately

**On interrupt:** All completed certs on disk. Resume counts lines, continues.

### Suspect Mode (`--suspect` with `--export`)

```bash
./goldbach --suspect 10000000 --export suspect_results.csv
```

Same as beyond — CRT-constructed adversarial numbers, certs written in real-time.

### Custom Range

Same as exhaustive range with user-specified boundaries. Auto-aligns to chunk boundaries.

---

## Upload Workflow

### For Exhaustive Ranges

```bash
./goldbach --upload results/
```

1. Reads `manifest.csv` from the directory
2. Submits each `start,end,sha256` to server via `POST /api/import`
3. Server creates/finds matching chunks, records hashes
4. Chunks marked `needs_doublecheck`
5. Optionally uploads compact binary files for permanent storage

### For Beyond/Suspect/Custom Sampling

```bash
./goldbach --upload beyond_results.csv
```

1. Reads CSV cert file
2. Submits in batches to server via `POST /api/import-certs`
3. Server stores certs permanently

---

## Server-Side Verification

### Verifying Exhaustive Range Submissions (Spot-Check)

**Cost: 0.02% of original computation**

1. Submitter uploads manifest (500,000 lines of `start,end,sha256`)
2. Server computes Merkle root from claimed chunk hashes
3. Server selects 100 random chunks
4. Server (or trusted verifier client) re-verifies those 100 chunks
5. Compares computed hashes to claimed hashes
6. **All match** → accept entire dataset, mark all chunks as verified
7. **Any mismatch** → reject everything, flag submitter

**Math:** If submitter faked 1% of chunks (5,000 / 500,000), probability that none of 100 random spot-checks hit a fake = (1 - 0.01)^100 = 0.366%. With 200 spot-checks: 0.13%. Practically zero.

**Time:** 100 chunks × ~14 seconds each = ~23 minutes to verify an 81-day computation.

### Verifying Beyond/Suspect Submissions (Direct Check)

**Cost: Nearly free**

Each cert `N,p,q` is self-proving:
1. Check: p + q = N? (one addition)
2. Check: is p prime? (one primality test)
3. Check: is q prime? (one primality test)

No Goldbach search needed — just verify the claimed answer.

Pick 1,000 random certs from their file. Verify each in microseconds. **Total: under one second.**

---

## SHA-256 Hashing Fix

### Current (Weak)
Hashes a summary string: `range=X-Y verified=N max_attempts=M counterexample=0`

This confirms two runs got the same stats but doesn't prove which numbers were checked.

### Proposed (Strong)
Hash every cert string `N=p+q\n` into the running SHA-256 during verification.

**Thread-count independence:** Use fixed-size blocks (e.g., 1M even numbers). Each block gets its own SHA-256 of certs in sequential order. Block boundaries are fixed regardless of thread count. Master chunk hash = SHA-256(block_0_hash || block_1_hash || ...).

**Cost:** One SHA-256 update per number. Negligible vs sieve work.

---

## Permanent Storage

### What We Keep Forever
- All chunk hashes and Merkle roots (tiny — KB per submission)
- All compact binary cert files from exhaustive ranges (~50-100 GB compressed per quintillion)
- All beyond/suspect CSV cert files (MB per submission)
- Submission metadata: who, when, verification status, spot-check results

### Academic Handoff
A university requesting proof receives:
- Compact binary files: every Goldbach pair for the entire verified range
- Reconstruction tool: `./goldbach --reconstruct chunk_000001.gbc` outputs human-readable `N=p+q` lines
- Verification tool: `./goldbach --verify-certs chunk_000001.gbc` re-checks every pair independently
- Merkle tree and spot-check audit trail

They can then:
1. Reconstruct any cert from the binary files
2. Verify any cert with any primality tool they trust
3. Run their own spot-checks on random chunks
4. Publish with full confidence

---

## New Server Endpoints Needed

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/config` | GET | Returns chunk size, current range, Merkle info |
| `/api/import` | POST | Submit chunk hash for offline-verified range |
| `/api/import-certs` | POST | Submit beyond/suspect cert batch |
| `/api/spot-check` | GET | Get random chunks for verification audit |
| `/api/merkle` | GET | Get Merkle tree / root for a range |

---

## New Client Flags Needed

| Flag | Purpose |
|------|---------|
| `--export <path>` | Save results (binary chunks or CSV certs) |
| `--upload <path>` | Submit saved results to server |
| `--reconstruct <file>` | Convert binary chunk to human-readable certs |
| `--verify-certs <file>` | Re-check every cert in a file independently |

---

## Implementation Priority

1. **Fix SHA-256 to hash actual certs** (required for any of this to work)
2. **`--export` for exhaustive mode** (chunk-aligned, manifest + binary files)
3. **`--export` for beyond/suspect** (CSV certs, already partially exists via `--cert`)
4. **`--upload`** (submit to server)
5. **Server `/api/import` endpoint** (accept offline results)
6. **Spot-check verification** (server picks random chunks for audit)
7. **`--reconstruct` and `--verify-certs`** (academic handoff tools)
8. **Merkle tree** (integrity proof over all chunks)
