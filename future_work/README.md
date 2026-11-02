# Future work

Legacy modules kept for later integration, not part of the `tent` package and not imported by it. They are not
installed, not tested by the TENT suite, and may not run against the current code.

- `bellman.py`: a dynamic-programming scheduler (Bellman model-predictive control) for assets with state, such as
  storage or thermal mass. Integration plan: `planning/THERMAL_ZONE_AND_SCHEDULER_PLAN.md` in the deployment repository
  (section 3, the scheduler as a scheduling-sub-problem solver for a model-frame asset).
- `building_zone_model.py`: a thermal zone model. Integration plan: the same document, section 2 (a ThermalZone
  model-frame model).
- `reconciliation_market.py`: an exploration of reconciliation and price tracking as a market subclass (2021), written
  against the pre-refactor module layout; the reconciliation state belongs to the base `Market` when it is built.
- `tests/bellman_tests/`: the Bellman module's own tests, moved with it.
