# Combat Resolution — Design Ideas

Collected improvements to `_resolve_combat` / `resolve_week` in `encounters.py`.
Current system: squad scores compared with Gaussian noise → binary winner-takes-all.

---

## A — Margin-based loser survival

**What:** After a fight, each loser runner independently survives with probability `1 - margin`,
where `margin = |winner_roll - loser_roll| / (winner_roll + loser_roll)`.

- Close fight (margin ≈ 0.1) → ~90% of losers escape alive, don't extract.
- Blowout (margin ≈ 0.9) → nearly all losers die.

**Why it matters:** Narrow losses currently feel identical to blowouts. This makes squad
composition a real decision — a tanky combat squad can guarantee kills without guaranteeing
wipes, while extraction-heavy squads that lose still live to run again.

**Implementation sketch:**
```python
# In resolve_week, replacing the loser loop:
for idx, runner in enumerate(loser):
    outcomes[runner.id] = WeeklyOutcome(
        survived=random.random() > margin,
        extracted=False,
        ...
    )
```

**Coupling note:** `apply_outcome` in `harness.py` gates affinity gain and attribute drift on
`survived=True`, so loser survivors automatically continue growing — no harness changes needed.
Decide whether loser survivors should count as an `extraction_attempt` (attempted, failed).

---

## B — Bilateral kill credit

**What:** The losing squad scores kills proportional to their combat share of the fight.

```
loser_kill_pool  = int(l_combat / (w_combat + l_combat) * len(winner))
winner_kill_pool = len(loser) - loser_kill_pool
```

Total eliminations remain conserved. Kills on each side distributed via existing
`_distribute_eliminations`, weighted by individual combat contribution.

**Why it matters:** Currently losers accumulate zero meaningful career stats. Combat-specialist
runners who repeatedly lose close fights look identical to runners who never fought. This makes
high-combat builds feel rewarding even in defeat.

**Requires:** `_resolve_combat` to also return `loser_breakdown` (currently discarded as `_loser_breakdown`).

---

## A + B combined

Both changes compose cleanly — the margin drives survival, the combat ratio drives kill split.
See the design conversation for a full code sketch of the contested-pair loop with both applied.

---

## C — Multi-round attrition (future / more complex)

**What:** Resolve combat in rounds. Each round, the weaker side loses one runner (with noise).
Yield goes to all survivors on both sides at the end.

**Why it matters:** Creates a spectrum between "clean win" and "pyrrhic victory." A squad that
wins 3v1 after losing two runners earns partial yield; a squad that sweeps 3v0 earns full yield.

**Cost:** Significant refactor of `resolve_week`. `BASE_SQUAD_YIELD` and `EXTRACTION_YIELD_MULTIPLIER`
would need recalibration since yield recipients expand. Harness reporting also needs updating.

---

## D — Zone / environment modifier (future)

**What:** Each week, zones roll an effective difficulty drawn from `gauss(base_difficulty, volatility)`.
High-volatility zones swing wildly, creating price surprises independent of runner quality.

**Why it matters:** Adds a third hidden variable driving market outcomes, directly feeding the
information asymmetry design in `marathon_market.py`. Currently zone difficulty is static.

**Scope:** Requires a new `volatility` field on `Zone`, a pre-week difficulty roll, and
`BASE_EXPECTATION` recalibration via `headless_calibration()`.

---

## Open questions

- Should loser survivors (Option A) count as `extraction_attempt += 1`? (i.e. "they tried and failed")
- Should bilateral kills (Option B) use the raw breakdown combat or the noisy roll scores?
- Is there value in a "retreat" outcome separate from "survived but did not extract"?
