"""
Market layer — orchestrates the weekly market simulation by combining
the persistent runner ecosystem (runner_sim.runners) with the tick-based
zone simulation (runner_sim.zone_sim).

Public surface:
    CompanyRoster, create_roster, replace_dead_runners        (roster.py)
    ShellMarket, update_prices, choose_affordable_shell       (shell_market.py)
    assign_squads                                              (deployment.py)
    simulate_week, apply_zone_outcome                          (week.py)
    CompanyWeekResult, compute_baseline, compute_price_change  (pricing.py)
    headless_calibration, bootstrap_default_state              (calibration.py)

The marathon_market.py entry point should only depend on this package
(not directly on runner_sim.zone_sim or runner_sim.runners).
"""
