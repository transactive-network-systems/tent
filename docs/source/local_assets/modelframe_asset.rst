.. _local_assets_modelframe_asset:

============================================
ModelFrameAsset Architecture & Aggregation
============================================

``ModelFrameAsset`` is TENT's native composite asset model, combining the market bidding capabilities of ``LocalAsset`` with the multi-model composition engine of ``ModelFrame``.

Compositional Hierarchy
-----------------------

Rather than treating an entire commercial building or facility as a black box with a single rigid model, ``ModelFrameAsset`` hosts an arbitrary collection of sub-models within its ``models`` dictionary:

* **Thermostat Models** (``tent.local_asset.model_frame.models.thermostat``): Second-order equivalent thermal parameter (RC) models for heating and cooling.
* **Lighting Models** (``tent.local_asset.model_frame.models.light``): Illuminance and schedule-driven lighting power.
* **Uncontrolled Models** (``tent.local_asset.model_frame.models.uncontrolled``): Plug loads, appliances, and non-shiftable baseload.
* **Physical FMI Models** (``tent.local_asset.model_frame.models.fmi_adapter``): Physics-backed models powered by OCHRE, GridLAB-D, pvlib, or Replay datasets.

Aggregation Mechanics
---------------------

During market scheduling:

1. **Scheduled Power Calculation**:
   ``ModelFrameAsset.schedule_power(market)`` maps the market's marginal price for the interval (the series default
   price while the market has none) to a set point for every sub-model (see below) and sums the models' power at those
   set points (``ModelFrame.model_power_at``):

   .. math::

      P_{\text{asset}}(t, \pi) = \sum_{m \in \text{models}} P_m\bigl(t, s_m(\pi)\bigr)

2. **Flexibility Aggregation**:
   ``ModelFrameAsset.update_vertices(market)`` sweeps the set points: for ``demand_curve_points`` = :math:`n` ratios
   :math:`r_i = i / (n - 1)` every model is at ratio :math:`r_i` of its set-point range (its ``predict_flexibility``
   is its power along the linspace of that range), and ``ModelFrame.model_flexibility`` sums the models' powers index
   by index.

3. **Vertex Generation**:
   The price span is the market price model's average at the interval minus and plus one standard deviation,
   :math:`[\pi_{\min}, \pi_{\max}]`; vertex :math:`i` is at the price :math:`\pi_{\min} + r_i (\pi_{\max} - \pi_{\min})`
   with the summed power at ratio :math:`r_i`. Two points give the ends of the span; one point gives a single,
   inelastic vertex at ratio 0.

   **One mapping for all three.** The asset owns the price-to-set-point mapping: ``price_ratio(price, interval, market)``
   is the price's position in the span the curve was built with, clamped to :math:`[0, 1]`, and
   ``set_points_at_price`` gives each model its set point at that ratio of its range, run in the direction in which the
   model consumes less (``ModelFrame.set_points_at_ratio``: a cooling thermostat's set point rises with the price, a
   light's level falls with it, and a thermostat whose regression slope has the other sign in some hour is run the
   other way in that hour), so the curve never consumes more at a higher price. The curve, the schedule and the
   actuation manager all use it, so the scheduled power is the curve read at the price, and the set points written
   produce the scheduled power. A model without a set-point range (an uncontrolled load, an FMI adapter) contributes
   its own ``predict_flexibility`` to the curve and is asked for its power without a set point.

4. **Closed-Loop Actuation**:
   Upon market clearance, ``ModelFrameAsset.actuate(market)`` hands the market to the actuation manager;
   ``DirectRatioActuationManager`` takes the cleared price of the delivery interval, asks the asset for
   ``set_points_at_price`` and writes each model's set point to its ``actuation_key``. Without a manager the asset
   iterates through its sub-models and calls ``model.actuate(self, market)``, dispatching cleared set points and power
   targets to the physical simulators.
