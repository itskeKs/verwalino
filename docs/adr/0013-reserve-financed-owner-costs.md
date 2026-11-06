# ADR-0013: Exclude reserve financing from owner cost distribution

Status: Accepted
Date: 2026-11-03
Issue: #191 (owner decision following #151)

## Context

A confirmed repair can be the evidence for a maintenance reserve payout.
Distributing its full cost to owners while reducing the reserve by the financed
amount charges the same expense twice. The reserve ledger already defines net
financing across a receipt's correction chain, booked payouts and evidenced
withdrawal corrections.

## Decision

For a newly computed WEG annual settlement, deduct that net financing from the
owner-relevant cost before distributing it by the existing owner key. Reuse the
ledger's receipt-chain calculation, including legacy `FUNDING` links, signed
corrections and the exclusion of draft/reversed payouts. Financing is the amount
known at computation, even when the cash movement belongs to another period.
Do not infer financing from a planned reserve contribution or an opening balance.

Freeze the original gross cost and the financing deduction in the settlement
snapshot. The sum distributed to owners is the remaining cost; a fully financed
receipt distributes zero. The reserve withdrawal remains in the reserve
development. The economic plan's Soll remains unchanged. The existing PDF reads
the frozen settlement lines and reports the same cost totals and withdrawal.

## Consequences

7,000.00 EUR of costs including a reserve-financed 1,000.00 EUR repair distribute
6,000.00 EUR: 3,600.00 / 2,400.00 EUR at 600/400 MEA. Costs without financing keep
their current calculation and snapshot shape. Finalized statements, their
snapshots, hashes and PDFs remain unchanged; correcting a finalized year uses
the existing correction workflow. No migration or new payment interface is needed.
Complex allocation of several financing sources beyond their simple net sum is
outside this decision.
