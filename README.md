# Goldbach Verification Engine v2.0

A high-performance, dual-verified tool for testing [Goldbach's Conjecture](https://en.wikipedia.org/wiki/Goldbach%27s_conjecture) — the oldest unsolved problem in mathematics.

Every result is checked by two independent primality tests (deterministic Miller-Rabin + Baillie-PSW) using exact integer arithmetic. If they ever disagree, the program halts. Results are output as independently verifiable proof certificates with SHA-256 hashing.

Single C file. Zero dependencies. Runs anywhere with a C compiler.

**Current status:** Tool complete. Verified exhaustively to 10^10, sampled to 10^38. No records have been set — the current world record (4 × 10^18, Oliveira e Silva 2012) stands. Scaling estimates in this document are projections.

---

## Quick Start

```bash
git clone https://github.com/musicims/goldbach.git
cd goldbach
./build.sh          # Auto-detects compiler + CPU
./goldbach           # Interactive menu
```

Or manually:

```bash
gcc -O3 -march=native -pthread goldbach.c -o goldbach -lm
./goldbach
```

**Requirements:** Any C compiler (gcc, clang, zig) on Linux or macOS. No libraries. No dependencies.

---

## Interactive Menu

Running `./goldbach` with no arguments presents an interactive menu:

```
MODES:

  1. Extend the Record   Push past 4×10^18 (fast mode + checkpoint)
  2. Benchmark           Test your hardware speed
  3. Beyond Record       Sample random numbers past 4×10^18
  4. Suspect Mode        Test adversarial (hardest possible) numbers
  5. Custom Range        Specify your own range
  6. Self-Test Only      Validate all components
  0. Exit
```

All modes auto-detect CPU cores. Press `q` during any running test to stop and return to the menu. All modes run a self-test before starting.

---

## Modes

### 1. Extend the Record

The primary mode. Starts exhaustive verification from the current world record (4×10^18), pushing forward with fast mode enabled and checkpointing on. Safe to interrupt and resume.

**Interactive:** Menu option 1 — starts immediately.

**CLI:**
```bash
./goldbach --range 4e18 5e18 --fast --checkpoint progress.txt
```

### 2. Benchmark

Test your hardware speed on already-proven ranges. Useful for estimating how long a real run would take before committing to server costs.

**Interactive:** Menu option 2, then choose:
- **a–d:** Range size (10^8 to 10^11)
- **Fast or dual mode**
- **0:** Back to menu

**CLI:**
```bash
./goldbach 1e9                    # Dual mode
./goldbach 1e9 --fast             # Fast mode
./goldbach 1e10 8 --fast          # Fast, 8 threads
```

### 3. Beyond Record

Sample random numbers past the world record. Each result is dual-verified. This is **sampling** — it tests specific random numbers, not every number in the range. Outputs the top 10 largest numbers verified.

**Interactive:** Menu option 3, then choose:
- **a:** Default 100K samples
- **b:** Custom sample count
- Then choose proven-only (up to 3.317×10^24) or include probabilistic range (up to 10^38)

**CLI:**
```bash
./goldbach --beyond 100000                      # 100K, default zones past 4×10^18
./goldbach --beyond 1000000 4e18 3.317e24       # 1M across full proven range
./goldbach --beyond 50000 1e24 1e38             # 50K in probabilistic range
./goldbach --beyond 10000 4e18 1e38 --cert c.txt  # With certificate file
```

**Verification boundaries (automatic, per-number):**

| Range | Witnesses | Mode |
|-------|-----------|------|
| Below 3.317 × 10^24 | 12 MR (proven) + BPSW | PROVEN |
| Above 3.317 × 10^24 | 24 MR (probabilistic, error < 10^-14) + BPSW | PROBABILISTIC |

The engine auto-selects for each number. Output labels which mode was used and shows the top 10 largest numbers tested.

### 4. Suspect Mode

Test numbers specifically constructed via the Chinese Remainder Theorem to be maximally difficult for the shortcut. These are the worst-case inputs — if anything could break it, these would.

**Interactive:** Menu option 4, then choose sample count and proven/probabilistic range.

**CLI:**
```bash
./goldbach --suspect 10000                    # 10K adversarial near 10^24
./goldbach --suspect 10000 1e30               # 10K adversarial near 10^30
./goldbach --suspect 50000 --cert s.txt       # With certificates
```

**Finding:** Even optimal adversarial construction only achieves ~1.7-2x more attempts than random inputs. This ~2x ceiling is fundamental — prime density prevents any construction from making numbers significantly harder. See [GOLDBACH_RESEARCH.md](GOLDBACH_RESEARCH.md) for the full analysis.

### 5. Custom Range

Exhaustive verification of any range you specify.

**Interactive:** Menu option 5 — enter start, end, and choose fast or dual mode.

**CLI:**
```bash
./goldbach --range 1e15 2e15 --fast --checkpoint p.txt
```

**Limit:** Exhaustive `--range` handles up to ~1.84 × 10^19 (uint64, sieve-based).

### 6. Cluster Mode

Split ranges across multiple machines. Each node works independently — no inter-node communication needed.

```bash
# Machine 1
./goldbach --range 4e18 4.25e18 --fast --checkpoint node1.txt

# Machine 2
./goldbach --range 4.25e18 4.5e18 --fast --checkpoint node2.txt

# Machine 3
./goldbach --range 4.5e18 4.75e18 --fast --checkpoint node3.txt
```

Each node outputs a SHA-256 hash. The hash is thread-count-independent — same range always produces the same hash. It proves the run completed with the stated parameters. For per-number proof, use `--cert` + `--verify`.

### 7. Verify Certificates

Independently check a certificate file produced by `--cert`. Dual-checks every entry.

```bash
./goldbach --verify certificates.txt
```

### 8. Self-Test

Runs 8 validation checks (both primality tests, sieve consistency, modular arithmetic, brute-force cross-check, large-number Goldbach). Refuses to proceed if any fail.

```bash
./goldbach --selftest
```

---

## How It Works

For a deep dive into the math, development history, and benchmarks, see [GOLDBACH_RESEARCH.md](GOLDBACH_RESEARCH.md).

### The Shortcut

Instead of searching all primes up to N/2, we try small primes p = 2, 3, 5, 7, 11... and check if N-p is prime. This is a well-known consequence of the prime number theorem — each N-p has roughly a 1/ln(N) chance of being prime, so O(log N) attempts usually suffice. The technique is not new (Oliveira e Silva's record-setting work used the same approach); what's new here is the engineering.

### Fast Mode (`--fast`)

Uses **sieve-only** for routine primality checks — deterministic and exact. ~39x faster than dual mode. If the shortcut ever fails, auto-escalates to full dual MR+BPSW. Oliveira e Silva's record used sieve-only; fast mode is the same approach with automatic escalation.

### Three-Tier Counterexample Detection

| Tier | What Happens |
|------|-------------|
| Shortcut finds a pair | Normal. Verified, done. |
| Shortcut exhausted (2,048 primes tried) | Auto brute-force with full dual MR+BPSW. Never triggered. |
| Brute-force finds nothing | **Goldbach counterexample.** Halts. Every prime was dual-checked. |

### Checkpointing

`--checkpoint FILE` auto-saves every 60 seconds. Resume by re-running the same command. Deleted on successful completion. Overhead: ~3 seconds per 3 weeks.

---

## Performance

Tested on a 4-core machine:

| Range | Even Numbers | Dual (default) | Fast (`--fast`) |
|-------|-------------|----------------|-----------------|
| 10^8 | 50M | 7 seconds | 0.2 seconds |
| 10^9 | 500M | 78 seconds | 1.8 seconds |
| 10^10 | 5B | ~13 minutes | ~18 seconds |

Scales linearly with cores. Benchmark on your hardware with `./goldbach 1e9 --fast` before planning large runs.

---

## Files

| File | Description |
|------|-------------|
| `goldbach.c` | The entire engine — single file, zero dependencies |
| `build.sh` | Auto-detect compiler + CPU, build with optimal flags |
| `GOLDBACH_RESEARCH.md` | Full research notes, benchmarks, bug history, math references |
| `goldbach_fft.py` | NTT convolution approach (research prototype) |
| `verify_shortcut.py` | Shortcut scaling verification |
| `goldbach_engine.py` | Python segmented sieve verifier |
| `goldbach_beyond.py` | Python beyond-record tester (handles numbers > 10^19) |
| `goldbach_certify.py` | Python certificate generator |

---

## References

- Sorenson & Webster, "Strong Pseudoprimes to Twelve Prime Bases", *Mathematics of Computation*, 2015
- Baillie & Wagstaff, "Lucas Pseudoprimes", *Mathematics of Computation*, 1980
- Oliveira e Silva, Herzog & Pardi, "Empirical Verification of the Even Goldbach Conjecture up to 4 x 10^18", *Mathematics of Computation*, 2014

---

## License

Public domain. Use freely.
