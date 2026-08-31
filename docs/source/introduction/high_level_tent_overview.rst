.. _high_level_tent_overview:

===========================
High-Level Overview of TENT
===========================

The **Transactive Energy Network Template (TENT)** is the reference Python metamodel developed at the Pacific Northwest National Laboratory (PNNL).

System Topology
---------------

A TENT simulation or deployment consists of interconnected nodes called **Transactive Nodes (TNs)**. Nodes interact across transactive boundaries via standardized market objects.

.. code-block:: text

   +--------------------------------------------------------------+
   |                       Transactive Node                       |
   |                                                              |
   |  +-------------------+  +-----------------+  +------------+  |
   |  | Local Assets      |  | Transactive     |  | Market     |  |
   |  | (ModelFrameAsset) |  | Neighbors       |  | Series     |  |
   |  +-------------------+  +-----------------+  +------------+  |
   |            |                     |                 |         |
   |            +--------------+------+-----------------+         |
   |                           |                                  |
   |  +--------------------+   |    +--------------------------+  |
   |  | InformationServices|---+    | DataManager (Archiving)  |  |
   |  | (Weather / OAT)    |        | (SQLite / TimeSeries)    |  |
   |  +--------------------+        +--------------------------+  |
   +--------------------------------------------------------------+

Core Execution Loop
-------------------

At each simulation or market step, the Transactive Node executes the following sequence:

1. **Information Ingestion**: Ingests new telemetry, meter data, and external forecasts (weather, temperature, solar irradiance).
2. **Asset Flexibility Scheduling**: Each local asset queries its physical model (via ``tent.flexibility`` or ModelFrame) to determine its power baseline and flexibility boundaries.
3. **Vertex Generation**: Assets formulate price-quantity demand/supply curves (piecewise linear vertices) representing their willingness to shift consumption.
4. **Negotiation & Market Clearing**: The node runs a double auction across all local assets and transactive neighbors, calculating the Market Clearing Price (MCP) and cleared quantities across all active time intervals.
5. **Actuation & Dispatch**: Cleared market powers are committed, and dispatch commands are transmitted to physical simulator backends (e.g., OCHRE, GridLAB-D, pvlib).
6. **Data Archiving**: Market prices, orders, asset schedules, and physical telemetry are archived to the relational database.
