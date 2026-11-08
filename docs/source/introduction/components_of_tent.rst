.. _components_of_tent:

==================
Components of TENT
==================

TENT is structured as a collection of modular, extensible object-oriented classes:

1. Transactive Node (``tent.transactive_node.TransactiveNode``)
---------------------------------------------------------------
The root container representing a transactive participant. It hosts local assets, neighbors, markets, information services, and data managers.

2. Local Asset (``tent.local_asset.LocalAsset``)
------------------------------------------------
Base class representing any energy-consuming or producing resource within the node's balance boundary. It generates supply/demand curves and follows cleared schedules.

3. ModelFrameAsset (``tent.local_asset.model_frame.ModelFrameAsset``)
---------------------------------------------------------------------
The primary composite asset implementation in TENT. It nests a ``ModelFrame`` container that aggregates multiple device models (HVAC, Lighting, Uncontrolled Base Load, and Physical FMI Adapters).

4. Transactive Neighbor (``tent.neighbor.Neighbor``)
----------------------------------------------------
Represents an adjacent transactive peer or upstream utility supplier. Neighbors exchange transactive orders across boundary meters.

5. Market Series & Auction (``tent.market.MarketSeries``, ``tent.market.Auction``)
-----------------------------------------------------------------------------------
Orchestrates continuous rolling double auctions across a configurable horizon (e.g. 24 intervals of 1 hour, or 96 intervals of 15 minutes).

6. Information Services (``tent.information_service``)
------------------------------------------------------
Provides environmental context and forecasts (e.g., outdoor air temperature, weather forecasts, solar irradiance) required by local assets to predict energy demand.

7. Data Manager (``archivable``, ``tent.data_manager``)
------------------------------------------------------
Handles relational database persistence (via SQLAlchemy) for market clearing prices, orders, asset schedules, and physical telemetry.
The data managers are the ``lib-archivable`` library's (import package ``archivable``): ``LocalDataManager`` writes to
the database named by ``db_params.url`` (a SQLAlchemy URL, required); ``NullDataManager`` accepts every record and keeps
none and is used when a node's configuration has no ``data_manager`` block and by tests. ``tent.data_manager`` holds
TENT's own side: the table definitions and enumerations TENT ships, their archive schema version, and ``prepare``, which
declares them on a data manager; ``tent.data_manager.reader.open_archive`` opens an existing archive with TENT's
queries.

The archive schema version (``tent.data_manager.ARCHIVE_SCHEMA_VERSION``, currently 6) is recorded in every database
TENT writes and bumped whenever a shipped table or enumeration changes; the README lists the versions. Version 6 (8
November 2026) added ``sd_curve.balance_point_record``: the record number, among the vertex rows of a curve version
(numbered in price order, not in wire order), of a transactive signal curve's balance point, the exchange the signal
proposes to the neighbor. Earlier archives do not say which vertex that was; ``open_archive(...).curves`` reads them
without the column.
