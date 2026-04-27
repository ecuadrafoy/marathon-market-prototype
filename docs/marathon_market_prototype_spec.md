# Marathon Market Simulator — Python Prototype Specification

## Concept Overview
A covert economic strategy game set in the Marathon (2026) universe. The player acts as an Earth-based financial analyst monitoring runner activity on Tau Ceti IV, buying and selling company stocks based on runner contract outcomes across map zones. The core fantasy is part portfolio management, part corporate espionage.

---

## Core Loop
1. **Planning phase** — player reviews zone information and allocates capital across company shares
2. **Simulation phase** — the week plays out, runners attempt extractions, outcomes resolve
3. **Resolution phase** — stock prices update based on company performance, player portfolio value updates
4. Repeat

---

## Entities

### Companies
Four publicly traded companies whose stock prices are driven by runner contract outcomes:

| Company | Domain |
|---|---|
| CyberAcme | AI & software |
| Sekiguchi | Runner bioweaving & augmentation |
| Traxus | Resource extraction |
| NuCaloric | Bioindustrial products & agriculture |

### Non-traded Factions (future expansion)
- **MIDA** — anti-UESC political movement. Future intervention tool for disrupting company operations
- **Arachne** — death cult with rich patrons. Future intervention tool for targeted runner elimination
- **UESC** — governing authority. Future source of regulatory events and heat/scrutiny system

---

## World State Variables

### Zone
- **Difficulty** — scale 1 to 5. Prototype uses fixed value of **3**
- Represents a map area on Tau Ceti IV where runners are allocated and runs happen

### Runner Roster
- **Total runners per zone** — between **6 and 12** per week
- **Company allocation** — randomly weighted across the 4 companies. Constraints:
  - Minimum 1 runner per company
  - No company exceeds 50% of total roster
- **Runner skill** — single composite float, scale 0 to 1
  - Generated from a bell curve: **mean 0.5, standard deviation 0.15**
  - Most runners cluster around average; elite or poor runners are rare
  - **Note:** This is an intentional abstraction. In future versions, skill will expand into a multi-stat system with class compositions and team synergies. All formulas consume this composite value as a black box — the interface stays stable when internals are expanded.

---

## Week Resolution Logic

### Success Probability
For each runner:
```
success_probability = skill - ((difficulty - 1) × 0.1)
```
Example: skill 0.5 runner in difficulty 3 zone → 0.5 - 0.2 = **0.30 success chance**

> ⚠️ This formula may feel punishing once running. Tune this first if outputs look wrong.

### Extraction Yield
For each successful runner:
```
yield = 50 + (skill × 100)
```
Range: 50 to 150 credits. Elite runners trend toward 150, average runners toward 100.

> **Future expansion — Loot Table System:** Extraction yield is an intentional abstraction over what will become a discrete item system. Each zone will have a loot table with tiered rarities — common items worth little, rare items worth significantly more. Runner skill will influence both success probability and the quality tier of items extracted. The performance score formula stays the same; the inputs get richer. Rare item extraction events may also trigger outsized stock movements beyond the standard performance delta — a runner pulling a high-value artifact is a categorically different market signal than a week of consistent common loot. This is considered a key system for the full game once the core loop is validated.

### Company Performance Score
Per company, aggregated across all their runners in the zone:
```
performance_score = success_rate × average_yield
```
Where:
- `success_rate` = successful runs / total runs attempted
- `average_yield` = mean yield across successful runs only

---

## Market Response Logic

### Baseline Expectation
- Derived from runner headcount only (what the player can see early game)
- More runners allocated to a company = higher market expectation for that company

### Performance Delta
```
delta = actual_performance_score - baseline_expectation
```
Normalized so it can be positive or negative.

### Stock Price Movement
```
price_change (%) = (delta × 10) + random_noise
random_noise = uniform(-2, +2)
```

---

## Player Layer

### Starting Capital
- **10,000 credits**

### Baseline Stock Prices
| Company | Starting Price (credits/share) |
|---|---|
| CyberAcme | 450 |
| Sekiguchi | 380 |
| Traxus | 300 |
| NuCaloric | 200 |

At 10,000 credits the player cannot hold meaningful positions in all four companies simultaneously — diversification has a real cost.

### Portfolio Tracking
- Track shares held per company
- Track credits remaining
- Calculate portfolio value each week: `shares × current_price` summed across all holdings

---

## Information Asymmetry (Prototype vs Future)
In the prototype the player sees:
- Runner headcount per company in the monitored zone
- Stock prices before and after each week

They do NOT see:
- Individual runner skill values
- Outcomes in unmonitored zones (these resolve but are invisible)

Future progression unlocks progressively richer information: runner history, yield stats, class compositions, multi-zone monitoring.

---

## Prototype Build Order
Build and verify each step before proceeding to the next:

1. **Runner and zone generation** — spawn a zone, populate with runners, assign to companies
2. **Week resolution** — run success and yield formulas across all runners
3. **Market response** — translate company performance scores into stock price movement
4. **Player layer** — capital management, share purchasing, portfolio value tracking
5. **Loop** — wire into a repeatable week cycle with console output

> Start with console output only. No UI. Goal is to run 50+ weeks quickly and validate that market behavior feels meaningful — not random, not perfectly predictable.

---

## Out of Scope for Prototype
The following are designed but not implemented in this phase:
- Multiple zones / zone monitoring unlocks
- News and world events
- Player intervention actions (MIDA, Arachne, experimental gear)
- UESC regulatory events and heat system
- AI opponent investors
- Fog of war mechanics
- Mid-week pause ability
- Animations and visual effects
- Godot implementation
- **Loot table system** — discrete item rarities per zone replacing the continuous yield abstraction. Considered a key system for the full game; implement after core loop is validated.

---

## Target Platform
- **Prototype:** Python (console output, no UI)
- **Final:** Godot, primarily 2D
