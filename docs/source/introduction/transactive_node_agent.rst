.. _transactive_node_agent:

==============================
Transactive Node Agent Runtime
==============================

The Transactive Node (TN) Agent can be deployed as a standalone simulation runtime or integrated into distributed middleware platforms such as VOLTTRON™ or HELICS™.

Instantiating a Transactive Node
--------------------------------

A node can be constructed directly from a JSON configuration file using the built-in loader:

.. code-block:: python

   from tent.transactive_node import TransactiveNode

   # Load node from JSON configuration
   tn = TransactiveNode.from_json("sample_configs/tn.campus.json")

   # Start transactive execution loop
   tn.start()

Event Timing & Horizon Rolling
------------------------------

The Transactive Node manages time progression via an internal ``Timer`` or external simulation clock:

* **Market Cycle Duration**: Frequency at which the market renegotiates (e.g., every 15 minutes or 1 hour).
* **Delivery Horizon**: Number of active time intervals projected into the future (e.g., 24 one-hour intervals).
* **State Updates**: Between market cycles, active markets advance from ``Active`` to ``Negotiation``, ``MarketLead``, ``DeliveryLead``, and ``Delivery``.
