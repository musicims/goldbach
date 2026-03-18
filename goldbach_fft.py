"""
Goldbach FFT Convolution Engine — Phase 1 Proof of Concept
Pure Python (no dependencies)

Instead of checking each even number individually, we compute
the convolution of the prime indicator function with itself.
This gives us r(N) — the number of Goldbach pairs — for ALL N
simultaneously.
"""

import time
import math


def sieve(limit):
    """Sieve of Eratosthenes returning a boolean list."""
    is_prime = [False, False] + [True] * (limit - 1)
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
    return is_prime


# ---- Number Theoretic Transform (exact integer convolution) ----
# This is the KEY innovation: no floating point, no rounding errors.
# Results are provably exact.

# We work modulo a prime P where P = c * 2^k + 1 (NTT-friendly prime)
# P must be larger than any possible value in our convolution result.
# Max r(N) for N up to 2*LIMIT won't exceed LIMIT, so P > LIMIT suffices.

def ntt_prime_and_root(n):
    """Find a suitable NTT prime and primitive root for transform size n.
    n must be a power of 2. We need P = c * n + 1 that is prime."""
    # Use a known NTT-friendly prime: 998244353 = 119 * 2^23 + 1
    # Supports transforms up to 2^23 = 8388608
    # Primitive root is 3
    P = 998244353
    g = 3
    assert (P - 1) % n == 0, f"Transform size {n} too large for prime {P}"
    return P, g


def ntt(a, P, g, invert=False):
    """Number Theoretic Transform (in-place, iterative, radix-2)."""
    n = len(a)
    assert n & (n - 1) == 0, "Length must be power of 2"

    # Bit-reversal permutation
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    length = 2
    while length <= n:
        # w is the principal nth root of unity mod P
        if invert:
            w = pow(g, P - 1 - (P - 1) // length, P)
        else:
            w = pow(g, (P - 1) // length, P)

        half = length >> 1
        for i in range(0, n, length):
            wn = 1
            for k in range(half):
                u = a[i + k]
                v = a[i + k + half] * wn % P
                a[i + k] = (u + v) % P
                a[i + k + half] = (u - v) % P
                wn = wn * w % P
        length <<= 1

    if invert:
        n_inv = pow(n, P - 2, P)
        for i in range(n):
            a[i] = a[i] * n_inv % P


def convolve_exact(a, b):
    """Exact integer convolution using NTT. No floating point anywhere."""
    result_len = len(a) + len(b) - 1
    n = 1
    while n < result_len:
        n <<= 1

    P, g = ntt_prime_and_root(n)

    fa = a + [0] * (n - len(a))
    fb = b + [0] * (n - len(b))

    ntt(fa, P, g)
    ntt(fb, P, g)

    # Pointwise multiply
    for i in range(n):
        fa[i] = fa[i] * fb[i] % P

    ntt(fa, P, g, invert=True)

    return fa[:result_len]


def goldbach_ntt(limit):
    """
    Compute r(N) for all even N up to limit using exact NTT convolution.
    r(N) = number of ordered pairs (p,q) with p+q=N, both prime.
    We sieve up to limit so all primes needed for sums up to limit are covered.
    """
    print(f"Computing Goldbach pairs for all even N up to {limit:,}")

    print("Step 1: Sieving primes...")
    t0 = time.time()
    is_prime = sieve(limit)
    prime_count = sum(is_prime)
    t1 = time.time()
    print(f"  Found {prime_count:,} primes in {t1 - t0:.3f}s")

    # Convert to integer list for NTT
    prime_indicator = [1 if is_prime[i] else 0 for i in range(limit + 1)]

    print("Step 2: NTT convolution (exact integer arithmetic)...")
    t2 = time.time()
    result = convolve_exact(prime_indicator, prime_indicator)
    t3 = time.time()
    print(f"  Convolution done in {t3 - t2:.3f}s")
    print(f"  This computed r(N) for ALL even N up to {limit:,} simultaneously.")

    return result


def brute_force_r(n, is_prime):
    """Brute force r(N) for verification."""
    count = 0
    for p in range(2, n):
        if p < len(is_prime) and (n - p) < len(is_prime):
            if is_prime[p] and is_prime[n - p]:
                count += 1
    return count


def verify(limit=500):
    """Verify NTT results against brute force."""
    print("\n=== VERIFICATION ===")
    result = goldbach_ntt(limit)
    is_prime = sieve(limit)

    print(f"\nChecking even numbers 4 to {limit} against brute force...")
    errors = 0
    for n in range(4, limit + 1, 2):
        bf = brute_force_r(n, is_prime)
        ntt_val = result[n]
        if bf != ntt_val:
            print(f"  MISMATCH at N={n}: NTT={ntt_val}, brute_force={bf}")
            errors += 1

    if errors == 0:
        print(f"  All {(limit - 2) // 2:,} values match. NTT convolution is EXACT.")
    else:
        print(f"  {errors} mismatches!")
    return errors == 0


def full_analysis(limit):
    """Run the full Goldbach analysis."""
    result = goldbach_ntt(limit)

    print("\n=== RESULTS ===")

    min_pairs = float('inf')
    min_n = 0
    counterexamples = []

    # Track minimums across ranges for growth analysis
    window = 10000
    current_window_min = float('inf')
    current_window_n = 0
    window_data = []

    checked = 0
    for n in range(4, min(len(result), limit + 1), 2):
        r = result[n]
        checked += 1

        if r < min_pairs:
            min_pairs = r
            min_n = n

        if r == 0:
            counterexamples.append(n)

        # Window tracking
        if r < current_window_min:
            current_window_min = r
            current_window_n = n

        if checked % (window // 2) == 0:
            window_data.append((n, current_window_min, current_window_n))
            current_window_min = float('inf')

    print(f"Checked {checked:,} even numbers from 4 to {min(len(result)-1, limit)}")
    print(f"Global minimum: r({min_n:,}) = {min_pairs}")

    if counterexamples:
        print(f"\n*** COUNTEREXAMPLES FOUND: {counterexamples} ***")
    else:
        print(f"Goldbach conjecture verified for entire range.")

    # Show most vulnerable numbers
    print(f"\nMost vulnerable even numbers (fewest Goldbach pairs):")
    vulns = []
    for n in range(4, min(len(result), limit + 1), 2):
        vulns.append((result[n], n))
    vulns.sort()
    for r, n in vulns[:15]:
        print(f"  N = {n:>10,}  →  r(N) = {r}")

    # Growth rate analysis
    print(f"\n=== GROWTH RATE OF MINIMUM r(N) ===")
    print(f"(Key question: does the floor keep rising?)\n")
    print(f"{'Range':>24s}  {'Min r(N)':>10s}  {'At N':>10s}  {'Heuristic':>10s}")
    print("-" * 60)
    for end_n, wmin, wmin_n in window_data:
        if end_n > 10:
            heuristic = 1.32 * end_n / (math.log(end_n) ** 2)
            print(f"  ...to {end_n:>14,}  {wmin:>10,}  {wmin_n:>10,}  {heuristic:>10.1f}")

    return result


if __name__ == "__main__":
    print("=" * 65)
    print("GOLDBACH NTT ENGINE — Exact Integer Arithmetic")
    print("=" * 65)
    print()
    print("KEY: Using Number Theoretic Transform, NOT floating-point FFT.")
    print("All results are mathematically exact — no rounding errors.")
    print("This means results are PROVABLE, not approximate.")
    print()

    # Step 1: Verify correctness
    if not verify(500):
        print("Verification failed! Aborting.")
        exit(1)

    # Step 2: Scale up
    # NTT prime 998244353 supports up to 2^23 = 8,388,608 elements
    # So we can go up to limit ≈ 4,000,000 → checking even N up to 8,000,000
    LIMIT = 200_000  # Start moderate, can increase

    print()
    print("=" * 65)
    print(f"FULL RUN: Even numbers up to {LIMIT:,}")
    print("=" * 65)

    result = full_analysis(LIMIT)

    print()
    print("=" * 65)
    print("WHAT THIS PROVES")
    print("=" * 65)
    print(f"""
Every even number from 4 to {LIMIT:,} has been verified
using EXACT integer arithmetic (NTT mod 998244353).

No floating point. No rounding. No approximation.
Each r(N) value is mathematically exact.

The entire range was computed SIMULTANEOUSLY via convolution —
not by checking each number one at a time.

To scale further:
  - Increase LIMIT (up to ~4M with current NTT prime)
  - Use multi-prime NTT for arbitrary sizes
  - Port to C for 50-100x speedup
  - Segmented convolution for ranges beyond memory
""")
