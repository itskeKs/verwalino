"""Largest-Remainder rounding -- the single place money is rounded (ADR-0005).

`distribute_amount(total, weights)` distributes a cent-exact total over n
addressees with non-negative exact weights, rounding each share to cent such
that the rounded shares sum EXACTLY back to the total.  This is the core
correctness guarantee of M3: "Sum of shares == total cost, no cent lost"
(a formal BGH requirement; ADR-0005 section 4.2).

EVERY distribution in the engine -- every key, every time split -- routes
through this one function.  There is no other place money is rounded to cent.

Algorithm (ADR-0005, Entscheidung; Hamilton / Hare / Largest-Remainder):

  1. exact share        e_i = total * w_i / sum(w)          (exact Decimal)
  2. floor to cent      f_i = floor(e_i, 2)   -- toward -inf (see note below)
  3. whole-cent remnant k   = round((total - sum(f_i)) * 100)   (0 <= k < n)
  4. fractional rank    r_i = e_i - f_i        (the "lost" sub-cent, >= 0)
  5. give +0.01 to the k addressees with the largest r_i
     tie-break (deterministic, NOT random): larger e_i first, then by the
     caller-supplied stable input order (= unit/addressee id order).
  6. result c_i = f_i (+0.01 if selected).      => sum(c_i) == total exactly.

Why floor toward -inf (ROUND_FLOOR), not toward zero (ROUND_DOWN):
  Flooring toward -inf guarantees r_i = e_i - f_i is ALWAYS in [0, 0.01),
  for positive AND negative totals.  Then k is always a NON-NEGATIVE count of
  +0.01 cents to hand out, and the method is sign-consistent: a negative
  total (credit / Gutschrift) is handled by the identical code path, the
  "extra cent" simply moves the share toward zero.  ROUND_DOWN (toward zero)
  would make r_i negative for negative totals and break the invariant.
  This nails down the "negative amounts -- precise definition in
  implementation" open point of ADR-0005 section 4.4.

Determinism (ADR-0005 "not negotiable" #4): the tie-break is fully
deterministic, so finalizing the same inputs twice yields a bit-identical
result -> stable snapshot hash (ADR-0003).
"""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

CENT = Decimal("0.01")


class DistributionError(ValueError):
    """Raised when a distribution cannot be performed correctly.

    Deliberately fail-loud rather than silently producing a wrong split
    (m3-abrechnungs-engine.md section 2.6 / section 4.4): e.g. sum of weights
    is zero, or a weight is negative.
    """


def _floor_to_cent(value: Decimal) -> Decimal:
    """Floor a Decimal to 2 places toward -inf (see module docstring)."""
    return value.quantize(CENT, rounding=ROUND_FLOOR)


def distribute_amount(
    total: Decimal,
    weights: list[Decimal],
) -> list[Decimal]:
    """Distribute `total` (cent-exact) over `weights` via Largest-Remainder.

    Args:
        total: the amount to distribute, exact to cent (Decimal). May be
            negative (credit) or zero.
        weights: non-negative exact weights, one per addressee, in a STABLE
            order (the caller is responsible for ordering by unit/addressee
            id so the tie-break is reproducible). At least one weight, and
            sum(weights) must be > 0.

    Returns:
        A list of cent-rounded Decimals, same length and order as `weights`,
        whose sum equals `total` EXACTLY.

    Raises:
        DistributionError: if weights is empty, any weight is negative, or
            sum(weights) == 0 (no silent zero-distribution -- section 4.4).
    """
    n = len(weights)
    if n == 0:
        raise DistributionError("distribute_amount: empty weight vector")

    weight_sum = Decimal(0)
    for i, w in enumerate(weights):
        if w < 0:
            raise DistributionError(
                f"distribute_amount: negative weight at index {i}: {w}"
            )
        weight_sum += w

    if weight_sum == 0:
        raise DistributionError(
            "distribute_amount: sum of weights is zero -- cannot distribute "
            "(would be a silent 0-split; escalate instead)"
        )

    total = total.quantize(CENT)

    # Trivial single-addressee case: everything goes to the one addressee.
    if n == 1:
        return [total]

    # Step 1 + 2: exact shares and their floors.
    exact_shares: list[Decimal] = []
    floors: list[Decimal] = []
    for w in weights:
        e = (total * w) / weight_sum
        exact_shares.append(e)
        floors.append(_floor_to_cent(e))

    floor_sum = sum(floors, Decimal(0))

    # Step 3: remaining whole cents to hand out (always a non-negative int
    # because of ROUND_FLOOR; quantize to int via round to kill any 1e-27
    # Decimal noise from the division).
    remainder_cents_dec = ((total - floor_sum) / CENT).quantize(Decimal(1))
    k = int(remainder_cents_dec)

    if k < 0 or k >= n:
        # Should be impossible with ROUND_FLOOR + exact arithmetic; this is a
        # hard guard so a logic error never silently mis-distributes money.
        raise DistributionError(
            f"distribute_amount: remainder cents out of range "
            f"(k={k}, n={n}, total={total}, floor_sum={floor_sum})"
        )

    result = list(floors)
    if k == 0:
        return result

    # Step 4 + 5: rank indices by largest fractional remainder, tie-broken by
    # larger exact share, then by stable input order (index). We sort a list
    # of indices; Python's sort is stable, so equal keys keep input order.
    fractional = [exact_shares[i] - floors[i] for i in range(n)]
    order = sorted(
        range(n),
        key=lambda i: (fractional[i], exact_shares[i]),
        reverse=True,
    )

    for i in order[:k]:
        result[i] = result[i] + CENT

    return result
