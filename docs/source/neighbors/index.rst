.. _neighbors_index:

=========
Neighbors
=========

A ``Neighbor`` (``tent.neighbor.Neighbor``) models a party on the other side of one of the node's boundary meters: an
upstream supplier, a downstream customer, or a peer node. Every neighbor contributes a supply or demand curve (its
*active vertices*) to each market interval, stated from this node's side: a positive quantity is power imported from
the neighbor, a negative one is power exported to it. ``Market.sum_vertices`` adds these curves to the local assets'
and the market clears where the sum crosses zero. Losses on the link (``loss_factor``) and demand charges
(``demand_rate`` above ``demand_threshold``) are applied to imported power as the curve is built.

Three neighbor classes ship with TENT:

``Neighbor``
    A *transactive* neighbor (``transactive: true``) exchanges transactive signals: its curve in each interval is the
    last signal received from the remote node, and the node sends it the residual flexibility of the rest of the system
    in return (see :doc:`../design/consensus_market`). A non-transactive ``Neighbor`` uses its ``default_vertices`` in
    every interval.

``BulkSupplierDC``
    A non-transactive bulk supplier on a published tariff: a flat two-vertex curve at the month's heavy-load-hour or
    light-load-hour energy rate spanning the configured power limits, with the demand charge added above the demand
    threshold in heavy-load hours.

``ResponseNeighbor``
    A non-transactive counterparty that states only a price or only a quantity; described below.

ResponseNeighbor
================

``ResponseNeighbor`` (``tent.neighbor.response_neighbor``) represents a counterparty in an existing market system that
neither sends nor accepts transactive signals: a utility publishing a dynamic price or a tariff, a demand-response
program issuing an instruction, a distribution operator setting an import cap. The stated value becomes the degenerate
curve that interpolation clears against exactly, so no iteration with the counterparty is needed.

Configuration
-------------

``response_type``
    ``"price"`` or ``"quantity"``.

``subscribe_key``
    The key on the node's ordinary subscription mechanism (``TransactiveNode.register_subscription``) on which the
    schedule arrives.

``curtailment_reward``
    Optional, quantity form only, in $/kWh. A reward the counterparty pays for every kilowatt-hour imported below the
    stated quantity.

``minimum_power``, ``maximum_power``
    The power limits inherited from ``Neighbor``. The price form and the curtailment reward need a range
    (``minimum_power`` below ``maximum_power``); the plain quantity form accepts any finite quantity when the limits
    are left at their 0.0 defaults and otherwise requires the stated quantity to lie within them.

``loss_factor``, ``demand_rate``, ``demand_threshold``, ``cost_parameters`` and the other ``Neighbor`` keys keep their
meaning. Losses and demand charges are applied to the built curve exactly as for any non-transactive neighbor; set
both to zero when the stated price is the whole price. ``transactive`` must be false.

.. code-block:: json

    {
      "class_name": "ResponseNeighbor",
      "name": "utility",
      "response_type": "price",
      "subscribe_key": "utility/day_ahead_price",
      "up_or_down": "upstream",
      "minimum_power": 0.0,
      "maximum_power": 1000.0,
      "loss_factor": 0.0,
      "demand_rate": 0.0,
      "cost_parameters": [0.0, 0.0, 0.0]
    }

The schedule message
--------------------

A message on ``subscribe_key`` is either a list of entries or a single number.

.. code-block:: json

    [
      {"start_time": "2017-07-01T00:00:00+00:00", "value": 0.062},
      {"start_time": "2017-07-01T14:00:00+00:00", "value": 0.095}
    ]

Entries are kept by start time, so a later message adds to or overrides earlier entries, and a value holds until an
entry with a later start time changes it. ``start_time`` must be timezone-aware ISO 8601; ``value`` is a number in
$/kWh (price form) or kW (quantity form, positive for import into this node). A single number replaces the whole
schedule and applies to every interval. An empty list, a naive time, a non-numeric or non-finite value, a quantity
outside a configured power range, or a start time given twice in one message is a ``ValueError``; the stored schedule
is unchanged.

Each market interval takes the value in force at its start time. An interval that starts before the earliest entry has
no value: that is logged as an error once per market and interval, naming the neighbor and the interval, and the
interval is left without a curve for this neighbor (``Market.sum_vertices`` leaves it out of the system curve with its
own warning) and with a scheduled power of zero.

The curves
----------

Price form
    Two vertices at the stated price, at ``minimum_power`` and ``maximum_power``: any quantity within the limits is
    available at that price. This is the shape ``BulkSupplierDC`` uses for the tariff, and a market whose other
    participants are inelastic or price-responsive clears at exactly the stated price.

Quantity form
    One inelastic vertex at the stated quantity. The local price is read where the local flexibility meets the
    instruction: a campus with a flexible load under an import cap clears at the price at which the load equals the
    cap.

Quantity form with a curtailment reward
    Two vertices at the reward price, at ``minimum_power`` and at the stated quantity. Every kilowatt-hour imported
    below the instruction forgoes the reward, so the local system curtails when its own flexibility values power below
    the reward and otherwise imports the stated quantity; more is not available. An instruction at ``minimum_power``
    leaves nothing to curtail and is the inelastic vertex.

Scheduled power
    Both the price form and the reward form have a flat segment, and when the market clears on it the price alone does
    not say how much is exchanged. ``Market.balance`` therefore assigns every entity its power at the cleared price
    (``Market.assign_cleared_powers``): an entity whose curve is flat at that price takes the residual of the others,
    shared among several flat entities in proportion to their segment widths, and ``Neighbor.schedule_power`` reads
    that assignment (``Entity.market_power``) when the market has balanced against the current curve, otherwise the
    curve at the price. For the inelastic form the assignment is the stated quantity.

Changes during a market
-----------------------

On every message the neighbor rebuilds its curves for every market of the node that has not yet reached DeliveryLead
and calls ``market.on_local_change(self)``. A ``ConsensusMarket`` marks itself unbalanced and rebalances on its next
tick, telling its transactive neighbors if that changes anything; the new value takes effect for the intervals it
covers while earlier values hold for the rest. An ``Auction`` inherits the base ``on_local_change``, which does
nothing, and takes the new value up when it next schedules this neighbor (MarketLead for a downstream neighbor,
DeliveryLead for the upstream one).

Transactive signals
-------------------

``prep_transactive_signal``, ``send_transactive_signal`` and ``receive_transactive_signal`` log that the neighbor is a
``ResponseNeighbor`` and return ``None``; nothing is prepared, sent, stored or parsed.

Archive
-------

The neighbor's row is written to the ``response_neighbor`` table, which inherits ``neighbor`` (and so ``entity`` and
``component``) and adds ``response_type``, ``subscribe_key`` and ``curtailment_reward``.
