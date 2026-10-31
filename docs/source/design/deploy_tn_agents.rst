.. _Deploy_Agents:

=====================================
Deploying Transactive Node Agents
=====================================

Transactive Node agents can be executed in multiple deployment configurations:

1. Standalone Python Simulation
--------------------------------

For research, algorithm development, and academic simulations:

.. code-block:: python

   from tent.transactive_node import TransactiveNode

   node = TransactiveNode.from_json("sample_configs/tn.campus.json")
   node.start()

2. VOLTTRON™ Platform Agent
---------------------------

For building automation and real hardware integration:

* Deploy the Transactive Node as a native VOLTTRON agent communicating over the VIP message bus.
* Subscribes to campus BACnet telemetry topics and publishes cleared actuation setpoints to building automation systems (BAS).

3. Co-Simulation via HELICS™
-----------------------------

For large-scale grid and power system co-simulation:

* Connect multiple TENT nodes across a HELICS federation, synchronizing time with grid simulators (such as GridLAB-D or OpenDSS) and communication network simulators.
