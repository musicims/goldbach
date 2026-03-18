"""
SHORTCUT VERIFICATION

Hypothesis: For every even N > 2, there exists a SMALL prime p
such that N - p is also prime. If true, we never need to search
far — verification becomes O(1) per number.

This script measures exactly how many primes you need to try
before finding a Goldbach pair, for every even N up to a limit.
"""

import time
import math


def sieve(limit):
    is_prime = bytearray(b'\x00\x00\x01\x01') + bytearray(limit - 3)
    for i in range(4, limit + 1, 2):
        is_prime[i] = 0
    for i in range(3, limit + 1):
        if not is_prime[i]:
            continue
        if i > 3:
            is_prime[i] = 1
        for j in range(i * i, limit + 1, i * 2 if i > 2 else i):
            is_prime[j] = 0
    # Fix: simple correct sieve
    return is_prime


def simple_sieve(limit):
    """Straightforward sieve."""
    s = bytearray(limit + 1)
    s[2] = 1
    for i in range(3, limit + 1, 2):
        s[i] = 1
    for i in range(3, int(limit**0.5) + 1, 2):
        if s[i]:
            for j in range(i*i, limit + 1, 2*i):
                s[j] = 0
    return s


def verify_shortcut(limit):
    print(f"Sieving primes up to {limit:,}...")
    t0 = time.time()
    is_prime = simple_sieve(limit)
    t1 = time.time()
    print(f"Sieve done in {t1-t0:.3f}s")

    # Small primes to try in order
    small_primes = [p for p in range(2, min(limit, 10000)) if is_prime[p]]

    # Track: for each even N, how many small primes do we need to try?
    max_attempts = 0
    max_attempts_n = 0
    worst_cases = []

    # Distribution of attempts needed
    attempt_dist = {}

    checked = 0
    t2 = time.time()

    for n in range(4, limit + 1, 2):
        found = False
        attempts = 0
        for p in small_primes:
            if p > n:
                break
            attempts += 1
            q = n - p
            if q >= 0 and q <= limit and is_prime[q]:
                found = True
                break

        if not found:
            print(f"*** NO PAIR FOUND for N={n} after {attempts} attempts! ***")
            continue

        checked += 1
        attempt_dist[attempts] = attempt_dist.get(attempts, 0) + 1

        if attempts > max_attempts:
            max_attempts = attempts
            max_attempts_n = n

        if attempts >= 20:
            worst_cases.append((n, attempts, p))

    t3 = time.time()

    print(f"\nVerified {checked:,} even numbers in {t3-t2:.3f}s")
    print(f"\n{'='*60}")
    print(f"SHORTCUT VERIFICATION RESULTS")
    print(f"{'='*60}")

    print(f"\nWorst case: N={max_attempts_n:,} needed {max_attempts} attempts")

    print(f"\nDistribution of attempts needed:")
    print(f"  {'Attempts':>10s}  {'Count':>10s}  {'%':>8s}  {'Cumulative%':>12s}")
    print(f"  {'-'*44}")
    cumulative = 0
    for a in sorted(attempt_dist.keys()):
        count = attempt_dist[a]
        pct = 100 * count / checked
        cumulative += pct
        print(f"  {a:>10d}  {count:>10,}  {pct:>7.3f}%  {cumulative:>11.3f}%")
        if cumulative > 99.99 and a > 10:
            remaining = sum(v for k, v in attempt_dist.items() if k > a)
            if remaining:
                print(f"  {'...':>10s}  {remaining:>10,}")
            break

    if worst_cases:
        print(f"\nHardest numbers (needed 20+ attempts):")
        worst_cases.sort(key=lambda x: -x[1])
        for n, att, p in worst_cases[:20]:
            print(f"  N = {n:>12,}  attempts = {att:>4d}  (pair: {p} + {n-p})")

    # Key metric: what's the maximum small prime index needed?
    print(f"\n{'='*60}")
    print(f"SHORTCUT VERDICT")
    print(f"{'='*60}")
    print(f"Max attempts needed: {max_attempts}")
    print(f"That means checking just the first {max_attempts} primes")
    print(f"is sufficient for ALL even N up to {limit:,}")
    print(f"\nThe {max_attempts}th prime is {small_primes[max_attempts-1]}")
    print(f"\nIf this bound grows slowly (logarithmically),")
    print(f"then verification is effectively O(1) per number.")

    return max_attempts


if __name__ == "__main__":
    # Test at multiple scales to see how the bound grows
    print("Testing shortcut at multiple scales...\n")

    results = []
    for exp in range(4, 8):
        limit = 10 ** exp
        print(f"\n{'#'*60}")
        print(f"SCALE: N up to {limit:,}")
        print(f"{'#'*60}")
        ma = verify_shortcut(limit)
        results.append((limit, ma))

    print(f"\n\n{'='*60}")
    print(f"SCALING SUMMARY")
    print(f"{'='*60}")
    print(f"{'Limit':>15s}  {'Max attempts':>15s}  {'Growth':>10s}")
    print(f"{'-'*45}")
    for i, (lim, ma) in enumerate(results):
        growth = ""
        if i > 0:
            growth = f"x{ma / results[i-1][1]:.2f}"
        print(f"{lim:>15,}  {ma:>15d}  {growth:>10s}")

    print(f"\nIf max_attempts grows as O(log N), this is a valid shortcut.")
    print(f"If it grows as O(N^c) for c > 0, it's not.")
