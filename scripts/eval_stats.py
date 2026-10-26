"""Small-sample statistics for the evaluation scripts (Python standard library only).

exact_paired_sign_flip() is the two-sided paired sign-flip test of
evaluate_recall.paired_randomization without Monte Carlo noise: it enumerates
every sign pattern of the k non-zero paired differences (k <= 20) with the same
statistic and tie rule.

rate_upper_bounds() gives one-sided upper confidence bounds for a binomial
proportion: the Wilson score bound and the exact Clopper-Pearson bound
(bisection on the binomial CDF whose log coefficients come from math.lgamma;
zero events use the closed form 1 - alpha^(1/n)).

hypergeometric_upper_bound() gives the exact one-sided upper bound on the
number of misses K in a finite population of N items from x misses in a
uniform random sample of k items drawn without replacement: the largest K with
P(X <= x | N, K, k) >= alpha (Callaghan and Mueller-Hansen, Systematic Reviews
9:273, 2020, inverted from a test into a bound). It compares exact integers
against alpha taken from the decimal text of the confidence, so no float CDF
is involved. scripts/real_vault_audit.py uses it; evaluate_recall.py and
flaky_repeat.py do not.

All of them describe uncertainty for human judgement. Callers report them next
to the existing metrics and never turn them into gates.
"""
from __future__ import annotations

import bisect
from fractions import Fraction
import math
from statistics import NormalDist

EXACT_PAIRED_MAX_NONZERO = 20
# Same tie rule as paired_randomization: a pattern counts when |sum| >= |observed| - 1e-12.
TIE_TOLERANCE = 1e-12
EXACT_PAIRED_METHOD = ('paired sign-flip; two-sided; exact enumeration of all 2^k sign patterns '
                       'of the k non-zero differences')
HYPERGEOMETRIC_METHOD = 'hypergeometric_exact_inversion; integer bisection over K'


def paired_differences(baseline, improved) -> list:
    """Return improved - baseline per pair; inputs are validated like paired_randomization."""
    if (not isinstance(baseline, (list, tuple)) or not isinstance(improved, (list, tuple))
            or len(baseline) != len(improved)):
        raise ValueError('paired values must have identical lengths')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (*baseline, *improved)):
        raise ValueError('paired values must be finite numbers')
    return [b - a for a, b in zip(baseline, improved)]


def minimum_attainable_p_value(nonzero_pairs: int) -> float:
    """Smallest exact two-sided sign-flip p for k non-zero pairs: 2^(1-k), or 1.0 when k = 0."""
    if type(nonzero_pairs) is not int or nonzero_pairs < 0:
        raise ValueError('nonzero_pairs must be a non-negative integer')
    return 2.0 ** (1 - nonzero_pairs) if nonzero_pairs else 1.0


def _signed_sums(values) -> list:
    """Left-to-right sums of every sign pattern of values; index 0 is the all-plus pattern."""
    sums = [0.0]
    for value in values:
        sums = [total + value for total in sums] + [total - value for total in sums]
    return sums


def exact_paired_sign_flip(baseline, improved) -> dict:
    """Exact two-sided paired sign-flip p over the 2^k patterns of the k non-zero differences.

    A zero difference never changes a sum, so these 2^k patterns give the same
    null distribution as all 2^n patterns. The observed pattern and its full
    reversal always count, so p >= 2^(1-k). With no pairs p is None, as in the
    Monte Carlo test. More than EXACT_PAIRED_MAX_NONZERO non-zero pairs raise
    ValueError; callers fall back to the Monte Carlo test.
    """
    delta = paired_differences(baseline, improved)
    nonzero = [value for value in delta if value != 0]
    k = len(nonzero)
    if k > EXACT_PAIRED_MAX_NONZERO:
        raise ValueError(f'exact enumeration supports at most {EXACT_PAIRED_MAX_NONZERO} non-zero pairs')
    n = len(delta)
    p_value = None
    if n:
        # Meet in the middle: each pattern sum is fl(left + right) of two half sums.
        # The observed statistic uses the same association, so it equals abs(sum(delta))
        # up to float rounding and the observed pattern always counts.
        left, right = _signed_sums(nonzero[:k // 2]), _signed_sums(nonzero[k // 2:])
        threshold = abs(left[0] + right[0]) - TIE_TOLERANCE
        if threshold <= 0:
            p_value = 1.0
        else:
            right.sort()
            extreme = 0
            for half in left:
                def key(value, half=half):
                    return half + value
                # fl(half + value) never decreases as value grows, so each tail of the
                # sorted half sums is contiguous and bisect counts it without approximation.
                extreme += len(right) - bisect.bisect_left(right, threshold, key=key)
                extreme += bisect.bisect_right(right, -threshold, key=key)
            p_value = extreme / 2 ** k
    total = sum(delta)
    return {'method': EXACT_PAIRED_METHOD, 'paired_query_count': n, 'nonzero_pair_count': k,
            'mean_delta': total / n if n else None, 'p_value': p_value,
            'minimum_attainable_p_value': minimum_attainable_p_value(k)}


def _check_counts(events, trials) -> None:
    if type(events) is not int or type(trials) is not int:
        raise ValueError('events and trials must be integers')
    if trials < 1 or not 0 <= events <= trials:
        raise ValueError('rate bounds need at least one trial and 0 <= events <= trials')


def confidence_alpha(confidence) -> float:
    """Validate a one-sided confidence level and return alpha = 1 - confidence."""
    if type(confidence) is not float or not 0.5 < confidence < 1:
        raise ValueError('confidence must be a float strictly between 0.5 and 1')
    return 1.0 - confidence


def wilson_upper_bound(events: int, trials: int, *, confidence: float = 0.95) -> float:
    """One-sided Wilson score upper bound: the upper root with z at the one-sided quantile."""
    _check_counts(events, trials)
    confidence_alpha(confidence)
    z = NormalDist().inv_cdf(confidence)
    rate, z2 = events / trials, z * z
    centre = rate + z2 / (2 * trials)
    spread = z * math.sqrt(rate * (1 - rate) / trials + z2 / (4 * trials * trials))
    return min(1.0, (centre + spread) / (1 + z2 / trials))


def _binomial_cdf(log_coefficients: list, trials: int, probability: float) -> float:
    """P(X <= len(log_coefficients) - 1) for X ~ Binomial(trials, probability), 0 < probability < 1."""
    log_p, log_q = math.log(probability), math.log1p(-probability)
    return math.fsum(math.exp(log_c + i * log_p + (trials - i) * log_q)
                     for i, log_c in enumerate(log_coefficients))


def _log_binomial_coefficients(trials: int, events: int) -> list:
    """log C(trials, i) for i = 0..events from math.lgamma, without big integers.

    math.comb grows to thousands of digits for large trials (n = 20,000), which
    made the coefficients the slow part of the bound; lgamma keeps every term a
    float. Both ends are exactly 0.0 because lgamma(1) is 0.
    """
    log_factorial = math.lgamma(trials + 1)
    return [log_factorial - math.lgamma(i + 1) - math.lgamma(trials - i + 1) for i in range(events + 1)]


def _clopper_pearson_bisection(events: int, trials: int, alpha: float) -> float:
    """Solve P(X <= events | trials, p) = alpha for p; the CDF falls as p grows.

    Returns the bracket end whose computed CDF is at most alpha, so the bound is
    never below the root of the computed CDF by more than float spacing.
    """
    log_coefficients = _log_binomial_coefficients(trials, events)
    low, high = 0.0, 1.0
    for _ in range(2048):
        middle = (low + high) / 2
        if middle in (low, high):
            break
        if _binomial_cdf(log_coefficients, trials, middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def clopper_pearson_upper_bound(events: int, trials: int, *, confidence: float = 0.95) -> float:
    """One-sided exact (Clopper-Pearson) upper bound for a binomial proportion.

    Zero events use the closed form 1 - alpha^(1/n); all events give 1.
    """
    _check_counts(events, trials)
    alpha = confidence_alpha(confidence)
    if events == trials:
        return 1.0
    if events == 0:
        return -math.expm1(math.log(alpha) / trials)
    return _clopper_pearson_bisection(events, trials, alpha)


def rate_upper_bounds(events: int, trials: int, *, confidence: float = 0.95) -> dict:
    """Both one-sided upper bounds with the numerator and denominator they describe."""
    clopper_pearson = clopper_pearson_upper_bound(events, trials, confidence=confidence)
    return {'numerator': events, 'denominator': trials, 'observed_rate': events / trials,
            'confidence': confidence,
            'wilson_upper': wilson_upper_bound(events, trials, confidence=confidence),
            'clopper_pearson_upper': clopper_pearson,
            'clopper_pearson_method': ('all_events' if events == trials else
                                       'zero_event_closed_form' if not events else 'binomial_cdf_bisection')}


def exact_alpha(confidence) -> Fraction:
    """Validate a confidence like confidence_alpha and return 1 - confidence as an exact fraction.

    The fraction comes from the decimal text of the float (0.95 -> 1/20).
    1 - Fraction(0.95) would carry the binary error of the float instead
    (0.050000000000000044 as a float).
    """
    confidence_alpha(confidence)
    return 1 - Fraction(repr(confidence))


def _check_population_counts(misses, sample, population) -> None:
    if type(misses) is not int or type(sample) is not int or type(population) is not int:
        raise ValueError('misses, sample and population must be integers')
    if population < 1 or not 0 <= misses <= sample <= population:
        raise ValueError('hypergeometric bounds need population >= 1 and 0 <= misses <= sample <= population')


def _hypergeometric_cdf_numerator(misses: int, sample: int, population: int, total: int) -> int:
    """C(N, k) * P(X <= misses) for X ~ Hypergeometric(N, total, k), as an exact integer.

    The sum of C(K, i) * C(N - K, k - i) over the feasible i <= misses. Each
    term after the first comes from the one before it; the division is exact
    because both terms are integers. Terms grow with k, not with N.
    """
    first = max(0, sample - (population - total))
    last = min(misses, total)
    if first > last:
        return 0
    term = math.comb(total, first) * math.comb(population - total, sample - first)
    numerator = term
    for i in range(first, last):
        term = term * (total - i) * (sample - i) // ((i + 1) * (population - total - sample + i + 1))
        numerator += term
    return numerator


def hypergeometric_upper_bound(misses: int, sample: int, population: int, *,
                               confidence: float = 0.95) -> dict:
    """Exact one-sided upper bound on the misses K among N items from x misses in k sampled.

    Returns the largest K in [x, N] with P(X <= x | N, K, k) >= alpha, where
    X ~ Hypergeometric(N, K, k) and alpha = exact_alpha(confidence). The CDF
    never increases as K grows, so a bisection finds it; K = x always passes,
    and every K above N - (k - x) fails because the CDF is 0 there. The bound
    is below the true K with probability at most alpha when the k items are a
    uniform random draw without replacement. x = k gives N, k = N gives x, and
    k = 0 gives N (no information). upper_miss_share is upper_misses / N.
    """
    _check_population_counts(misses, sample, population)
    alpha = exact_alpha(confidence)
    # P(X <= x) >= p/q, compared as C(N, k) * P(X <= x) * q >= p * C(N, k).
    threshold = alpha.numerator * math.comb(population, sample)
    low, high = misses, population - (sample - misses) + 1
    while high - low > 1:
        middle = (low + high) // 2
        if _hypergeometric_cdf_numerator(misses, sample, population, middle) * alpha.denominator >= threshold:
            low = middle
        else:
            high = middle
    return {'misses': misses, 'sample': sample, 'population': population, 'confidence': confidence,
            'upper_misses': low, 'upper_miss_share': low / population, 'method': HYPERGEOMETRIC_METHOD}
