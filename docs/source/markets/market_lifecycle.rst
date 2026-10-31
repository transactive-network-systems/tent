.. _markets_market_lifecycle:

=====================================
Market Lifecycle & State Transitions
=====================================

Markets in TENT advance through deterministic sequential states across rolling time horizons.

State Transition Diagram
------------------------

.. code-block:: text

   [Created] ---> Active ---> Negotiation ---> MarketLead ---> DeliveryLead ---> Delivery ---> Reconciliation ---> [Archived]

State Definitions
-----------------

1. ``Active``:
   The market is instantiated for an upcoming delivery window. Initial default schedules and price models are established.

2. ``Negotiation``:
   Local assets and transactive neighbors submit iterative price-quantity bids (vertices). The market recalculates aggregate supply and demand curves until convergence or until the bidding window closes.

3. ``MarketLead``:
   Convergence has been reached. Final market clearing prices (MCPs) and cleared quantities are determined across all intervals.

4. ``DeliveryLead``:
   Transactive orders are generated, matched, and committed between counterparties.

5. ``Delivery``:
   The physical delivery interval is active. Scheduled setpoints and power commands are dispatched to equipment or physical simulators (``actuate()``).

6. ``Reconciliation``:
   Physical metervalues are measured and compared against committed contracts to compute imbalance settlement and deviations.
