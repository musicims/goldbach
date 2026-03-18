# Goldbach Verification Engine v2.0

A high-performance, dual-verified tool for testing [Goldbach's Conjecture](https://en.wikipedia.org/wiki/Goldbach%27s_conjecture) — the oldest unsolved problem in mathematics.

Every result is checked by two independent primality tests (deterministic Miller-Rabin + Baillie-PSW) using exact integer arithmetic. If they ever disagree, the program halts. Results are output as independently verifiable proof certificates with SHA-256 hashing.

Single C file. Zero dependencies. Runs anywhere with a C compiler.

**Current status:** Tool complete. Verified exhaustively to 10^10, sampled to 10^24. No records have been set — the current world record (4 × 10^18, Oliveira e Silva 2012) stands. Scaling estimates in this document are projections.

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

The current world record for exhaustive verification is **4 x 10^18** (Oliveira e Silva, 2012). This tool is designed to extend that — though doing so requires significant distributed compute (see [Scaling Estimates](#scaling-estimates) below). On a single machine, it can verify billions of numbers per day and sample-test numbers up to 10^24+.

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

Test random numbers past the 4 x 10^18 world record. Each result is a dual-verified proof certificate. This is **sampling**, not exhaustive verification — it tests specific numbers but does not check every number in the range.

```bash
./goldbach --beyond 100000                     # 100K from default zones (4-10 x 10^18)
./goldbach --beyond 1000000 4e18 5e18          # 1M in [4x10^18, 5x10^18]
./goldbach --beyond 10000 1e20 1e21            # 10K in [10^20, 10^21]
./goldbach --beyond 1000 1e23 1e24 --cert c.txt  # 1K near 10^24 with certificates
```

**Limits:** Exhaustive `--range` handles up to ~1.84 x 10^19 (uint64, sieve-based). Sampling `--beyond` uses 128-bit arithmetic — results are proven correct to 3.317 x 10^24 (deterministic Miller-Rabin boundary). Past that, the engine automatically switches to 24 MR witnesses + BPSW, where results are probabilistic with error < 10^-14 per test. The output clearly labels which mode was used.

### 5. Adversarial (Suspect) Mode

Test numbers specifically constructed to be maximally difficult.

```bash
./goldbach --suspect 10000                    # 10K adversarial numbers near 10^18
./goldbach --suspect 10000 1e24               # 10K near 10^24
./goldbach --suspect 50000 --cert s.txt       # With certificates
./goldbach --suspect 100000 --checkpoint p.txt  # With checkpoint/resume
```

### 6. Verify Certificates

Independently check a certificate file. Uses dual primality on every entry.

```bash
./goldbach --verify certificates.txt
```

### 7. Self-Test

Runs 8 validation checks and refuses to proceed if any fail.

```bash
./goldbach --selftest
```

---

## How It Works

For a deep dive into the math, development history, and benchmarks, see [GOLDBACH_RESEARCH.md](GOLDBACH_RESEARCH.md).

### The Shortcut

Instead of searching all primes up to N/2, we try small primes p = 2, 3, 5, 7, 11... and check if N-p is prime. This is a well-known consequence of the prime number theorem — each N-p has roughly a 1/ln(N) chance of being prime, so O(log N) attempts usually suffice. The technique is not new (Oliveira e Silva's record-setting work used the same approach); what's new here is the engineering: dual-verified primality, certificate output, cluster distribution, and checkpoint/resume.

We measured the scaling empirically across 20 orders of magnitude: at most ~300-400 primality tests per number even past 10^18. Each test costs O(log² N) to O(log³ N), making the total per-number cost O(log³ N) — dramatically better than brute force's O(N / ln N), but not as cheap as "O(log N)" without qualification.

### Dual Verification

Two independent primality tests check every result:

| Method | Basis | Status |
|--------|-------|--------|
| Miller-Rabin | Modular exponentiation | Proven correct below 3.317 x 10^24 (12 witnesses) |
| Baillie-PSW | Lucas sequences | No known counterexample in 45 years |

If they **ever disagree**, the program halts immediately.

**Past 3.317 x 10^24**, the engine automatically switches to 24 MR witnesses. Each witness independently has at most a 1/4 chance of missing a composite, so 24 witnesses give error probability < (1/4)^24 ~ 3 x 10^-15. Combined with BPSW (which uses entirely different math), both would have to fail on the same number — no such number has ever been found. Results in this range are labeled `PROBABILISTIC` in the output to distinguish them from the `PROVEN` results below the boundary.

### Adversarial Testing (`--suspect`)

The engine can generate numbers specifically constructed to be as hard as possible for the Goldbach shortcut. Using the Chinese Remainder Theorem with an optimally chosen residue class (N ≡ 5738 mod 30030, the primorial of 2×3×5×7×11×13), each number is built so that N-p shares a factor with the primorial for 233 out of 300 small primes — guaranteeing N-p is composite for most initial attempts.

The result: even these worst-case numbers only require ~1.7-2x more attempts than random inputs, across all scales tested from 10^18 to 10^24. Increasing the number of CRT conditions doesn't help — the primorial modulus grows faster than the benefit, and surviving primes remain dense enough to provide pairs quickly. This ~2x ceiling appears to be fundamental rather than an engineering limitation.

This means the shortcut is robust against worst-case inputs, not just favorable ones. Exhaustive `--range` verification (testing every number sequentially) remains the strongest form of evidence, but `--suspect` demonstrates that targeted attacks on the algorithm don't work.

### Three-Tier Counterexample Detection

| Tier | What Happens |
|------|-------------|
| Shortcut finds a pair | Normal. Dual-verified, hashed, done. This happens for every number tested so far. |
| Shortcut exhausted (2,048 small primes tried) | Automatic full brute-force search with dual verification. This has **never triggered** across all testing — included as a safety net. If it ever does, the pair is logged and the run continues. |
| Brute-force finds nothing (every prime up to N/2 checked) | **Goldbach counterexample.** Program halts with explicit warning. Every candidate was dual-checked. This would disprove the conjecture. |

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

## Scaling Estimates

The current record (4 x 10^18) has stood since 2012. Goldbach verification is embarrassingly parallel — each number is independent — so it distributes across commodity hardware with no inter-node communication.

**Pushing to 5 x 10^18** (5 x 10^17 even numbers past the record):

| Total Cores | Estimated Time |
|-------------|---------------|
| 96 (2x 48-core) | ~17 days |
| 240 (5x 48-core) | ~7 days |
| 480 (10x 48-core) | ~3.5 days |

**Doubling the record** to 8 x 10^18 (4 x 10^18 even numbers):

| Total Cores | Estimated Time |
|-------------|---------------|
| 960 | ~6 months |
| 2,400 | ~2.5 months |
| 4,800 | ~5 weeks |

Estimates based on ~7M dual-verified numbers/sec per 48 modern cores (AMD EPYC / Zen 4 class). Scales linearly.

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
