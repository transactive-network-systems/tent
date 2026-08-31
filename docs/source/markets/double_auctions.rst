.. _markets_double_auctions:

================================================
Double Auction Clearing & Vertex Mathematics
================================================

TENT clears transactive markets using a continuous double auction mechanism based on piecewise linear supply and demand curves.

Supply / Demand Curve Construction
-----------------------------------

Every participating asset or neighbor submits a set of **Vertices** :math:`V = \{(p_1, q_1), (p_2, q_2), \dots, (p_n, q_n)\}` for each active time interval:

* **Price (:math:`p_i`)**: Marginal price in $/kWh.
* **Quantity (:math:`q_i`)**: Power in kW (positive for supply/generation, negative for demand/consumption).

Curve Aggregation
-----------------

The auction separates vertices into supply and demand functions:

1. **Supply Curve :math:`S(p)`**: Formed by sorting supply bids in ascending order of price.
2. **Demand Curve :math:`D(p)`**: Formed by sorting demand bids in descending order of price.

Market Clearing Price (MCP)
---------------------------

The clearing engine identifies the intersection where total supply equals total demand:

.. math::

   S(P_{\text{cleared}}) + D(P_{\text{cleared}}) = 0

* If supply and demand intersect at a single point, that point defines the unique Market Clearing Price (MCP) :math:`P_{\text{cleared}}` and Cleared Quantity :math:`Q_{\text{cleared}}`.
* If curves overlap over a price band, the midpoint or auction default price is selected.
* Assets whose bids clear receive binding power dispatch targets corresponding to :math:`Q_i(P_{\text{cleared}})`.
