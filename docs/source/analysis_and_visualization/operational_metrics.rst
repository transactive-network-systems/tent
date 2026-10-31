.. _Operational_Metrics:

===================
Operational Metrics
===================

TENT automatically logs operational metrics to the relational database managed by ``DataManager``.

Key Operational Metrics
-----------------------

1. **Market Clearing Price (MCP)**:
   The cleared price ($/kWh) calculated for each market time interval, reflecting grid congestion and energy balance.

2. **Cleared vs. Scheduled Power**:
   Tracks the difference between initial asset baseline forecasts and final transacted commitments.

3. **Convergence Speed & Iteration Count**:
   The number of negotiation rounds and duality gap metric achieved before market convergence.

4. **Actuation Tracking & Deviations**:
   Measures real-time tracking accuracy when physical simulator adapters follow transactive dispatch setpoints.
