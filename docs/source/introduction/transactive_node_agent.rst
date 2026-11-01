.. _transactive_node_agent:

==============================
Transactive Node Agent Runtime
==============================

The Transactive Node (TN) Agent can be deployed as a standalone simulation runtime or integrated into distributed middleware platforms such as VOLTTRON™ or HELICS™.

Instantiating a Transactive Node
--------------------------------

A host (the VOLTTRON agent, or a test harness such as ``tests/mock_tn.py``) subclasses ``TransactiveNode``, supplying
the message-bus methods, and configures it from a JSON configuration (validated through the configuration models, see
:ref:`Configuration_Models`):

.. code-block:: python

   import json
   from tests.mock_tn import MockTN

   with open("sample_configs/tn.campus.json") as f:
       tn = MockTN()
       tn.configure_node(json.load(f))

   tn.step()  # one iteration of the state machine; the agent runs state_machine_loop()

Event Timing & Horizon Rolling
------------------------------

The Transactive Node manages time progression via an internal ``Timer`` or external simulation clock:

* **Market Cycle Duration**: Frequency at which the market renegotiates (e.g., every 15 minutes or 1 hour).
* **Delivery Horizon**: Number of active time intervals projected into the future (e.g., 24 one-hour intervals).
* **State Updates**: Between market cycles, active markets advance from ``Active`` to ``Negotiation``, ``MarketLead``, ``DeliveryLead``, and ``Delivery``.
