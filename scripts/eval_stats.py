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
import hashlib
import math
import random
from statistics import NormalDist
from typing import Callable, Sequence

EXACT_PAIRED_MAX_NONZERO = 20
# Same tie rule as paired_randomization: a pattern counts when |sum| >= |observed| - 1e-12.
TIE_TOLERANCE = 1e-12
EXACT_PAIRED_METHOD = ('paired sign-flip; two-sided; exact enumeration of all 2^k sign patterns '
                       'of the k non-zero differences')
HYPERGEOMETRIC_METHOD = 'hypergeometric_exact_inversion; integer bisection over K'
PAIRED_BOOTSTRAP_METHOD = 'paired percentile bootstrap; deterministic seed; flat and optional stratified macro'
CROSS_FIT_METHOD = 'deterministic two-fold SHA-256 hash cross-fitting; out-of-fold evaluation'


def paired_differences(baseline, improved) -> list:
    """Return improved - baseline per pair; inputs are validated like paired_randomization."""
    if (not isinstance(baseline, (list, tuple)) or not isinstance(improved, (list, tuple))
            or len(baseline) != len(improved)):
        raise ValueError('paired values must have identical lengths')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (*baseline, *improved)):
        raise ValueError('paired values must be finite numbers')
    diffs = [b - a for a, b in zip(baseline, improved)]
    if any(not math.isfinite(d) for d in diffs):
        raise ValueError('paired differences must be finite numbers')
    return diffs


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
    because both terms are integers. Every term is at most C(N, k) <= N**k, so
    the integers have at most about k*log2(N) bits: linear in k and only
    logarithmic in N.
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


def _percentile_ci(samples: list[float], confidence: float) -> tuple[float, float]:
    ordered = sorted(samples)
    count = len(ordered)
    alpha_half = exact_alpha(confidence) / 2
    low_idx = max(0, min(count - 1, math.floor(alpha_half * (count - 1))))
    high_idx = max(0, min(count - 1, math.ceil((1 - alpha_half) * (count - 1))))
    return ordered[low_idx], ordered[high_idx]


def paired_bootstrap_ci(
    baseline,
    improved,
    *,
    strata=None,
    confidence: float = 0.95,
    resamples: int = 2000,
    seed: int = 1707,
) -> dict:
    """Two-sided paired percentile bootstrap CI for mean(improved - baseline).

    When ``strata`` (a sequence of non-empty stratum labels aligned with the pairs)
    is given, also reports ``stratified_macro`` by resampling independently within
    each stratum and macro-averaging stratum means with equal stratum weight.
    Descriptive only; callers must never turn bootstrap intervals into gates.
    """
    delta = paired_differences(baseline, improved)
    confidence_alpha(confidence)
    if type(resamples) is not int or not 100 <= resamples <= 20000:
        raise ValueError('resamples must be an integer between 100 and 20000')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    n = len(delta)
    if strata is not None:
        if not isinstance(strata, (list, tuple)) or len(strata) != n:
            raise ValueError('strata must have the same length as paired values')
        if any(type(label) is not str or not label.strip() for label in strata):
            raise ValueError('strata labels must be non-empty strings')
    if not n:
        result = {
            'method': PAIRED_BOOTSTRAP_METHOD,
            'confidence': confidence,
            'resamples': resamples,
            'seed': seed,
            'paired_query_count': 0,
            'mean_delta': None,
            'ci_low': None,
            'ci_high': None,
        }
        if strata is not None:
            result['stratified_macro'] = None
        return result

    observed = math.fsum(delta) / n
    rng = random.Random(seed)
    flat_means = [
        math.fsum(delta[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    ]
    ci_low, ci_high = _percentile_ci(flat_means, confidence)
    result = {
        'method': PAIRED_BOOTSTRAP_METHOD,
        'confidence': confidence,
        'resamples': resamples,
        'seed': seed,
        'paired_query_count': n,
        'mean_delta': observed,
        'ci_low': ci_low,
        'ci_high': ci_high,
    }
    if strata is not None:
        groups: dict[str, list[float]] = {}
        for label, value in zip(strata, delta):
            groups.setdefault(label, []).append(value)
        ordered_keys = sorted(groups)
        group_values = [groups[key] for key in ordered_keys]
        group_count = len(group_values)
        obs_macro = math.fsum(math.fsum(vals) / len(vals) for vals in group_values) / group_count
        srng = random.Random(seed ^ 0x5F3759DF)
        macro_means = []
        for _ in range(resamples):
            stratum_means = [
                math.fsum(vals[srng.randrange(len(vals))] for _ in range(len(vals))) / len(vals)
                for vals in group_values
            ]
            macro_means.append(math.fsum(stratum_means) / group_count)
        s_low, s_high = _percentile_ci(macro_means, confidence)
        result['stratified_macro'] = {
            'stratum_count': group_count,
            'strata': {key: len(groups[key]) for key in ordered_keys},
            'observed_macro_delta': obs_macro,
            'ci_low': s_low,
            'ci_high': s_high,
        }
    return result


def cross_fit_two_fold(
    items: Sequence[dict],
    fit_fn: Callable[[list[dict]], object],
    score_fn: Callable[[object, dict], float],
    *,
    id_key: str = 'id',
    seed: int = 1707,
) -> dict:
    """Deterministic SHA-256 two-fold cross-fitting for threshold or weight evaluation.

    Partitions ``items`` into two balanced folds by SHA-256(f"{seed}:{item[id_key]}")
    order (alternating index parity). Fits ``fit_fn`` on fold 1 and scores fold 0,
    then fits ``fit_fn`` on fold 0 and scores fold 1 so no item is scored by a model
    fit on itself. Descriptive only; Python standard library only.
    """
    if not isinstance(items, (list, tuple)) or len(items) < 2:
        raise ValueError('cross_fit_two_fold requires a sequence of at least 2 items')
    if not callable(fit_fn) or not callable(score_fn):
        raise ValueError('fit_fn and score_fn must be callable')
    if type(id_key) is not str or not id_key.strip():
        raise ValueError('id_key must be a non-empty string')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')

    seen: set[str] = set()
    validated: list[dict] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f'items[{idx}] must be a dict')
        item_id = item.get(id_key)
        if type(item_id) is not str or not item_id.strip() or item_id in seen:
            raise ValueError(f'items[{idx}][{id_key!r}] must be a unique non-empty string')
        seen.add(item_id)
        validated.append(item)

    ordered = sorted(
        validated,
        key=lambda row: (hashlib.sha256(f'{seed}:{row[id_key]}'.encode('utf-8')).hexdigest(), row[id_key]),
    )
    fold_0 = [row for idx, row in enumerate(ordered) if idx % 2 == 0]
    fold_1 = [row for idx, row in enumerate(ordered) if idx % 2 == 1]

    model_for_0 = fit_fn(list(fold_1))
    model_for_1 = fit_fn(list(fold_0))

    fold_of: dict[str, int] = {}
    score_of: dict[str, float] = {}
    fold_scores: list[list[float]] = [[], []]
    for fold_idx, (eval_fold, model) in enumerate(((fold_0, model_for_0), (fold_1, model_for_1))):
        for row in eval_fold:
            raw = score_fn(model, row)
            if type(raw) not in (int, float) or not math.isfinite(raw):
                raise ValueError('score_fn must return finite numeric scores')
            val = float(raw)
            item_id = row[id_key]
            fold_of[item_id] = fold_idx
            score_of[item_id] = val
            fold_scores[fold_idx].append(val)

    per_item = [
        {'id': row[id_key], 'fold': fold_of[row[id_key]], 'score': score_of[row[id_key]]}
        for row in validated
    ]
    all_scores = [row['score'] for row in per_item]
    return {
        'method': CROSS_FIT_METHOD,
        'seed': seed,
        'item_count': len(validated),
        'fold_sizes': [len(fold_0), len(fold_1)],
        'fold_means': [math.fsum(scores) / len(scores) for scores in fold_scores],
        'overall_mean': math.fsum(all_scores) / len(all_scores),
        'per_item': per_item,
    }
