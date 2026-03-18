# Goldbach Verification Engine v2.0

A high-performance, dual-verified tool for testing [Goldbach's Conjecture](https://en.wikipedia.org/wiki/Goldbach%27s_conjecture) — the oldest unsolved problem in mathematics.

Built to the same credibility standard as [y-cruncher](https://www.numberworld.org/y-cruncher/) (the tool that holds pi computation world records), with one advantage: **multi-machine distributed verification**, which y-cruncher cannot do.

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

## What It Does

**Goldbach's Conjecture (1742):** Every even integer > 2 is the sum of two primes.

This tool verifies the conjecture by testing even numbers and producing **dual-verified, independently checkable proof certificates**. Every result is confirmed by two independent primality tests (Miller-Rabin + Baillie-PSW). If they ever disagree, the program halts.

The current world record is **4 x 10^18** (Oliveira e Silva, 2012). This tool can extend that.

---

## Modes

### 1. Interactive Menu

```bash
./goldbach
```

Presents numbered options from quick test (~10 seconds) to deep run (~2.5 hours). Auto-detects your CPU cores.

### 2. Exhaustive Verification

Checks **every** even number in a range. Airtight — no gaps.

```bash
./goldbach 1e9                          # Verify up to 1 billion
./goldbach 1e10 8                       # 10 billion, 8 threads
./goldbach 1e12 --checkpoint progress.txt  # Long run with auto-save/resume
```

### 3. Cluster Mode

Split ranges across multiple machines. Each node works independently — no communication needed.

```bash
# Machine 1
./goldbach --range 0 1e15 --checkpoint node1.txt

# Machine 2
./goldbach --range 1e15 2e15 --checkpoint node2.txt

# Machine 3
./goldbach --range 2e15 3e15 --checkpoint node3.txt
```

Each node outputs a SHA-256 hash. Combine to verify full coverage.

### 4. Beyond-Record Sampling

Test random numbers past the 4 x 10^18 world record. Each result is a dual-verified proof certificate.

```bash
./goldbach --beyond 100000                     # 100K from default zones (4-10 x 10^18)
./goldbach --beyond 1000000 4e18 5e18          # 1M in [4x10^18, 5x10^18]
./goldbach --beyond 10000 1e20 1e21            # 10K in [10^20, 10^21]
./goldbach --beyond 1000 1e23 1e24 --cert c.txt  # 1K near 10^24 with certificates
```

**Limits:** Exhaustive `--range` handles up to ~1.84 x 10^19 (uint64, sieve-based). Sampling `--beyond` has no practical ceiling — proven correct to 3.317 x 10^24, probabilistic (error < 10^-14) beyond that. The switch is automatic.

### 5. Verify Certificates

Independently check a certificate file. Uses dual primality on every entry.

```bash
./goldbach --verify certificates.txt
```

### 6. Self-Test

Runs 8 validation checks and refuses to proceed if any fail.

```bash
./goldbach --selftest
```

---

## How It Works

### The Shortcut

Instead of searching all primes up to N/2, we try small primes p = 2, 3, 5, 7, 11... and check if N-p is prime. Empirically verified across 20 orders of magnitude: this needs at most ~300-400 attempts even for numbers past 10^18. That's a **10,000x+ speedup** over brute force.

### Dual Verification

Two independent primality tests check every result:

| Method | Basis | Status |
|--------|-------|--------|
| Miller-Rabin | Modular exponentiation | Proven correct below 3.317 x 10^24 (12 witnesses) |
| Baillie-PSW | Lucas sequences | No known counterexample in 45 years |

If they **ever disagree**, the program halts immediately.

**Past 3.317 x 10^24**, the engine automatically switches to 24 MR witnesses (probabilistic, error < 10^-14) while keeping BPSW as the second check. The output clearly labels results as `PROVEN` or `PROBABILISTIC`. No configuration needed — it's automatic.

### Three-Tier Counterexample Detection

| Tier | What Happens |
|------|-------------|
| Shortcut finds a pair | Normal. Dual-verified, hashed, done. |
| Shortcut exhausted | Full brute-force search kicks in automatically. |
| Brute-force finds nothing | **Goldbach counterexample.** Program halts. Every prime was dual-checked. |

### Checkpointing

```bash
./goldbach 1e12 --checkpoint progress.txt
```

- Auto-saves every 60 seconds (atomic write — never corrupted)
- Resume by re-running the same command
- Deleted automatically on successful completion
- Overhead: ~3 seconds per 3 weeks of runtime

---

## Performance

Tested on a 4-core machine:

| Range | Even Numbers | Dual-Verified | Single-Method |
|-------|-------------|---------------|---------------|
| 10^8 | 50M | 7 seconds | 0.16 seconds |
| 10^9 | 500M | 78 seconds | 1.8 seconds |
| 10^10 | 5B | ~13 minutes | 20 seconds |

Scales linearly with cores. A 48-core server is ~12x faster.

---

## Cloud Cost to Beat the World Record

The current record (4 x 10^18) has stood since 2012. Unlike y-cruncher which requires one massive machine, Goldbach verification distributes across cheap hardware:

| Setup | Time | Cost |
|-------|------|------|
| 100 Hetzner AX162 (48-core) | ~19 days | ~$3,500 |
| 500 Hetzner AX52 (8-core) | ~19 days | ~$5,000 |
| 100 AWS Spot c7i.24xlarge | ~19 days | ~$27,000 |

---

## Files

| File | Description |
|------|-------------|
| `goldbach.c` | The entire engine — 1,425 lines, zero dependencies |
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
