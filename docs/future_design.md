# Future Design — Ideas Captured for Later

**Status:** Vision and idea capture, not a specification. Each section is a
self-contained design topic that can be picked up independently when its
time comes. The integration shipped on the `runner-zone-integration` branch
(PR #4) is the foundation; everything here builds on top of it.

When you start working on any of these, expect to spend an hour or two
writing a focused spec for *that section* before coding — the questions
listed in each section are deliberately open and need explicit answers
before implementation work makes sense.

---

## Big Picture: A Progression Arc

The integration we shipped gives the player one game shape: *decode Sector 7
signals → bet on stocks → win or lose*. The ideas below combine to evolve
the game shape across a long session, without needing new content:

| Phase | Capital | Player activity |
|---|---|---|
| **Early** | Low | Read Sector 7, place small bets, spectator-investor |
| **Mid** | Some | Pattern-match on adapting company AI, predictive investor |
| **Late** | Significant ownership | Direct strategy of companies you own, active operator |

Two systems unlock this arc: **Adaptive Company AI** (companies develop
personalities through history) and **Player-as-Board-Member** (sufficient
ownership grants strategic influence). Together they create a natural
power curve where information and control accumulate alongside capital.

---

## Adaptive Company AI

**Idea:** Companies don't just deterministically chunk-and-deploy runners
each week. They develop personalities through experience and adapt their
strategy based on past performance and discrete events.

### Motivation

Today's `assign_squads` is intentionally dumb: sort by id, chunk into 3,
random zone shuffle. Doctrine variety emerges from random simplex
attributes, but companies don't *act* differently. With adaptive AI,
each company becomes a recognizable character whose patterns the player
can learn to predict.

### Concrete example: GREEDY-in-Sector-7 as a recovery play

A struggling company should prefer **GREEDY squads in low-difficulty
zones** — not because it's exciting, but because GREEDY (stay long,
fight aggressively) in Sector 7 (low-stakes encounters) is a *reliable
floor* play. Low ceiling, but bounded variance. A struggling company
needs predictable income to stop the price slide, not Memory Crystal
jackpots that won't materialize.

A thriving company can afford the inverse — GREEDY-in-The-Shelf, where
the variance might pay off in big finds.

### Key design questions

1. **Memory model.** Trailing window (last 5 weeks), exponential decay,
   or full history with a decay function? Different choices produce
   different feels:
   - Short memory → volatile/reactive companies
   - Long memory → stable/stubborn companies
   - Decaying memory → recent matters most but old shocks echo
2. **Personality stability.** Can a company's character drift back and
   forth (oscillate based on streaks), or do early traumas shape it
   permanently (path dependence)? The latter creates run-to-run variety.
3. **Self-assessment metrics.** What does "doing poorly" mean?
   - Stock price below starting? Below a moving average?
   - Cash flow trend?
   - Roster churn (high recent death count)?
   - Some weighted combination?
4. **"Other events" hook.** Beyond performance, what events shift
   strategy? Candidates:
   - Catastrophic squad wipe (shock → conservative for N weeks)
   - 5-week winning streak (overconfidence → more risk)
   - Roster suddenly losing all Destroyers (forced doctrine shift)
   - Competitor-relative events ("Sekiguchi just beat our market cap")
5. **Granularity.** Does each company adapt one global strategy, or
   adapt per-zone? *"We've been bad at The Shelf lately → send safer
   squads there specifically"* vs. *"We're bad overall → safer everywhere"*.

### Strategy translates to deployment via four sub-decisions

1. **Self-assessment** → company state (thriving / stable / struggling)
2. **Strategy selection** → desired (doctrine, zone) pairs this week
3. **Squad composition** → which 3 runners produce that doctrine
4. **Zone assignment** → which squad to which zone

Sub-decision 3 is the constraining one: doctrines emerge from shell
composition. To field a GREEDY squad you need ≥2 Destroyer-wearers
grouped together. **A company's roster shape limits its strategic
options** — if you have zero Destroyers, you can't field GREEDY. This
creates pressure on recruitment (next section).

### Architectural breadcrumbs already in place

- `assign_squads(roster, zones) → dict[zone, Squad]` is a pure function
  — easy to upgrade to take more inputs:
  ```
  assign_squads(roster, zones, company_history, shell_market, strategy_profile)
  ```
- `CompanyWeekResult` already records per-company history — needs a
  longer-timeline aggregation (`list[CompanyWeekResult]` per company).
- Each company would carry a `CompanyStrategy` state object that
  mutates after each week based on history + events.

### Recruitment as long-game strategy

Today, `_hire_one` calls `choose_affordable_shell(runner, prices, budget)`
— picks the best capability shell within budget for *this runner's*
attributes. Strategic recruitment overrides that:

```
choose_strategic_shell(runner, prices, budget, company_strategy_needs)
```

where `company_strategy_needs` says "we have 1 Destroyer, want 3" or
"we lack support, prefer Triage/Recon even if the recruit's stats
don't match perfectly." This creates tension between *runner-best-fit*
and *company-need*, with interesting cascading effects through the
shell market.

### Emergent properties to watch for

- **Strategy coupling through the shell market.** If three struggling
  companies all want GREEDY-Sector-7, they all need Destroyers, driving
  Destroyer prices up and choking off the strategy. Self-balancing
  equilibrium emerges.
- **Death-doctrine feedback.** Aggressive strategies → more deaths →
  middle-shell rookies → roster loses Destroyer count → can no longer
  field GREEDY. Conservative strategies preserve veteran rosters.
  Path dependence over weeks.
- **Counter-cyclical investing.** Recruiting Triage when everyone wants
  Destroyer becomes a viable contrarian play. Cheaper shells, less
  competition, niche strategy support.

---

## Player-as-Board-Member

**Idea:** When the player accumulates enough shares in a company, they
gain influence over its strategy. Tiered access: more shares = more
control.

### Motivation

The current Portfolio is purely financial — buy low, sell high. Adding
board influence introduces a non-monetary reason to accumulate shares.
Where the player wants *power* may differ from where they want *return*,
creating richer portfolio decisions.

### Information access as the killer reward

Currently the player only sees Sector 7 outcomes — Deep Reach and The
Shelf are hidden. **Board membership is the natural way to lift this
veil for owned companies.**

The architecture already supports it: `WeekSimulationResult` cleanly
separates `company_results` (player-facing, monitored zone only) from
`zone_results` (engine-internal, all zones with match logs and combat
events). For a company where the player has board membership, render
the full `zone_results` view for that company — selectively reveal
the asymmetric-info veil.

This creates a virtuous loop:
- More visibility → better decisions
- Better decisions → more capital
- More capital → more board seats
- More board seats → more visibility

### Key design questions

1. **Tiers.** Linear (51% = full control, period) or graduated?
   Graduated gives a smoother progression curve:
   - 10% = "advisor" (suggest a play; AI weighs it)
   - 25% = "board seat" (one vote, can veto deployments)
   - 51% = "majority owner" (full strategic control)
   - 75% = "absolute majority" (also influences recruitment)
2. **Influence type.**
   - **Override** — player directly chooses deployment for the company.
     Direct but feels like switching games (suddenly you're the deploy AI).
   - **Nudge** — player adjusts dials (risk tolerance, doctrine preference)
     and the AI decides within those bounds. Subtler, more sustainable.
   - Mixed: nudge at lower tiers, override at majority.
3. **Veto vs. proposal.** At board-seat tier, does the player propose
   plays the AI considers, or veto plays the AI proposes? The latter
   is a smaller per-turn commitment.
4. **Conflict of interest.** If the player has board seats in multiple
   companies, do they have to favor one (realism) or help all
   simultaneously (gameplay clarity)?
5. **Pace of progression.** What credit total / week count typically
   reaches first board seat? First majority? This shapes how long the
   "early game" lasts.

### Required model upgrade: total shares outstanding

Today's `Company` has `name` and `price` only — no concept of total
shares. The player can buy unbounded shares at the current price. For
percentage ownership to be meaningful, we need either:

- **(a) Fixed share count per company** (e.g., 1000 outstanding). Player
  ownership = `holdings[name] / 1000 * 100%`. More realistic, creates
  emergent scarcity ("can't buy more than what's available"), but means
  the player can't always buy as much as they want.
- **(b) Absolute share thresholds** (e.g., 100 = advisor, 500 = board).
  Simpler but less elegant; doesn't scale with company size.

Option (a) is more interesting and creates new gameplay (share scarcity
as a strategic constraint), but requires reworking `Portfolio.buy` to
account for available supply.

### Architectural breadcrumbs already in place

- `WeekSimulationResult.zone_results` is the data source for veil-lifting.
  `print_results` would gain a "show full zone data for boarded companies"
  branch.
- `Portfolio.holdings: dict[str, int]` is the foundation for ownership
  tracking. Add `Company.total_shares: int` and a `Portfolio.ownership_pct(company)`
  method.
- `assign_squads` is the override point — would gain a `player_directives`
  parameter at majority-ownership tier.

---

## Recruitment & Roster Ecosystem Adjustments

A loose group of smaller adjustments that came up while documenting the
recruitment flow. Each is independent and can be picked up alone.

### Permadeath softening

Currently a runner's shell, `credit_balance`, career stats — everything —
is destroyed with them on squad elimination. Alternatives worth considering:

- **Partial shell-price refund** on death. Incentivizes survival without
  rendering deaths costless. Refund rate could vary by zone (Sector 7
  death = 80% recovered, The Shelf = 20%).
- **Salvage by killing squad.** The squad that won the combat recovers
  a fraction of the dead runner's shell value. Mirrors the existing
  Uncommon+ kill-loot transfer mechanic.
- **Partial-survival rolls.** Each runner in an eliminated squad rolls
  vs. their combat stat to survive (wounded, retreats with no loot).
  Softer game feel; more bookkeeping; needs zone_sim extension to expose
  per-runner combat outcomes.

### Name reuse cleanup

`_random_runner_name` only avoids names *currently in use across all
rosters*. Dead runners' names return to the pool, so future recruits
can be named "Vega" again after the original Vega died. Possibilities:

- **Retire names on death.** Once Vega dies, the name is gone forever.
  Realistic if names are real names; runs out of names eventually
  (current pool is 48; with 36 active runners and churn, retirement
  pressure builds).
- **Document as callsigns.** Names are reissued; "Vega" is a role, not
  a person. Frame it lore-wise.
- **Expand the name pool** — add 100+ names so reuse is rare.

### Mid-career shell upgrades

Currently shells are sticky from recruitment. A surviving runner's
`credit_balance` accumulates from extractions but is never spent.
A future system could let runners *upgrade* their shell when they've
saved enough:

- Threshold-based: when `credit_balance > current_shell_price * 1.5`,
  the runner *attempts* a shell change (sell back current, buy new).
- Player-directed: at majority ownership, the player can *force* a
  veteran into a new shell.
- Naturally-emerging: runners whose attributes have drifted far from
  their shell affinity *consider* swapping (cost-benefit calculation).

This creates a within-career progression beyond the simplex drift —
runners can intentionally specialize differently from their starting
shell.

### Inter-company runner movement (poaching)

Currently runners only come from the void via recruitment, and never
move between companies. A "poaching" mechanic would let:

- A wealthy company *buy* a veteran from a poor company at a premium.
- Player at majority ownership trade runners between companies they
  control.
- Free-agent system: at runner career milestones, they "test the
  market" and can move to the highest bidder.

This dramatically increases inter-company strategic interaction.

### Shell market scarcity

Currently the shell market has infinite supply at the listed price —
any company can buy any shell at the going rate. A scarcity model:

- **Per-week shell quotas.** Only N Destroyers available this week,
  contested across companies. First-come-first-served or auction.
- **Deep market.** Shell supply varies week-to-week based on a
  separate stochastic process; rare weeks have one Memory Crystal
  appear.

Scarcity creates real strategic tension between companies — they're
not just price-takers, they're competing for limited resources.

### Bench / rotation

Today's `STARTING_ROSTER_SIZE = 9` means every runner deploys every
week — zero bench. Larger rosters with bench:

- Player decides who deploys (strategic choice surface).
- Recruits can "develop" by bench-rotation in safer zones.
- Wounded runners (if partial-survival is implemented) can rest.
- Specialization paths: rotate a runner through different shells
  for cross-training before settling.

Adds complexity but creates per-runner career arcs.

---

## Shell Market Deepening

The shell market resolves middle-shell dominance for **Recon and Rook**
(verified at ~28-42% middle adoption with `SHELL_PRICE_SENSITIVITY=4.0`),
but **Vandal and Assassin remain capability-dominated even within the
affordable tier**. They're 3rd-best on every axis — pricing alone can't
make them first-pick anywhere.

### Why pricing can't fix Vandal/Assassin

The affinity matrix structure:
- **Combat axis:** Destroyer 0.7 > Assassin 0.6 > Vandal 0.5
- **Extraction axis:** Thief 0.7 > Rook 0.5 > Vandal 0.4
- **Support axis:** Triage 0.8 > Recon 0.5 > Rook 0.2 > Vandal 0.1

Vandal has no axis where it's the best of any tier. Assassin gets
adopted only when Destroyer is unaffordable; Vandal only when both
Destroyer *and* Assassin are unaffordable.

### Two paths to viability

1. **Mechanical differentiation** (option (b) from `runner_design.md:264-271`)
   — give Vandal/Assassin unique non-affinity properties:
   - Vandal: extra credits per Common item extracted (specialist for low-tier zones)
   - Assassin: bonus kill-loot transfer rate (better than Destroyer at scavenging from kills)
   They stop being "Destroyer-but-worse" and become specialists for
   niche zone profiles. This is the **most game-design-rich path** but
   requires extending zone_sim mechanics.
2. **Affinity rebalancing** — change the numerical affinities themselves.
   Original design doc warned this would change the model character;
   should be a last resort.

---

## Verification & Calibration Carryovers

Items from the original integration plan that were deferred:

### `charts.py` rewrite

Currently stubbed. New visualizations to add:
- Squad credit distribution per zone (histogram from a calibration run)
- Squad elimination rate by zone
- Doctrine performance — average credits by GREEDY/CAUTIOUS/BALANCED/SUPPORT
- Shell adoption over time (from `market.adoption_history`)
- Shell prices over time (from `market.price_history`)

### Per-zone baseline scaling

Currently all zones contribute equally to a company's total credits,
which feeds the price formula. If Sector 7 dominates total credits 90/10
over The Shelf in long runs, baseline should scale per zone separately.
Not currently observed but worth re-checking after long calibration runs.

### Long-run divergence softening

After 30 weeks at the current calibration, weak companies drift toward
PRICE_FLOOR while one company tends to dominate. This is realistic and
creates urgency, but may feel deterministic. Levers to soften:
- Raise `BASE_EXPECTATION` slightly (lower bar = more companies "meet" it)
- Add a "comeback bonus" for under-baseline companies (hidden support)
- Cap the per-week price drop to e.g. -8% (limit downside spiral)

### Tuning toward more middle-shell adoption

Current equilibrium: ~58% premium / ~42% middle. To push further:
- Lower `RECRUIT_ALLOWANCE` (250 → 220) shrinks the affordable tier
- Raise `SHELL_PRICE_SENSITIVITY` further (4.0 → 6.0) makes premium
  shells unaffordable faster

---

## Quick-Reference: Where Each Idea Plugs In

| Idea | Primary file | Function/class to change |
|---|---|---|
| Adaptive Company AI | `runner_sim/market/deployment.py` | `assign_squads` |
| Strategic recruitment | `runner_sim/market/roster.py` | `_hire_one`, `choose_affordable_shell` |
| Board membership tiers | `marathon_market.py`, new `Portfolio` methods | `Portfolio.ownership_pct`, `Company.total_shares` |
| Veil lifting on owned companies | `marathon_market.py:print_results` | conditional zone_results render |
| Permadeath softening | `runner_sim/market/week.py:apply_zone_outcome` | death branch |
| Mid-career shell upgrades | `runner_sim/market/week.py` (new function) | called per-week |
| Shell market scarcity | `runner_sim/market/shell_market.py` | new supply tracking |
| Per-zone baseline | `runner_sim/market/pricing.py:compute_baseline` | take zone-mix arg |
| Vandal/Assassin mechanical roles | `runner_sim/zone_sim/extraction_ai.py` | extend doctrine effects per shell |
| `charts.py` rewrite | `charts.py` | full rewrite |

---

## Final Notes

The integration we shipped intentionally carved out the right seams for
all of these extensions. None of them require fighting the architecture
— each is a natural elaboration of an existing function or data path.

When picking up any of these, the right opening move is to draft a focused
spec answering the **Key design questions** in that section. The
implementation tends to fall out cleanly once those answers exist;
the design questions are the actual work.
