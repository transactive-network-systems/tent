.. _flexibility_overview:

=====================================
Flexibility Modeling & 3-Curve Schema
=====================================

Rather than relying on simplified fixed-profile predictions, modern transactive agents require continuous visibility into the dynamic physical operational envelope of their equipment.

The 3-Curve Flexibility Envelope
--------------------------------

For every requested time interval :math:`t \in [t_{\text{start}}, t_{\text{end}}]`, a flexibility model computes a three-point envelope:

1. **:math:`P_{\min}(t)` (Minimum Feasible Power)**:
   The lowest feasible power consumption (or maximum power generation / battery discharge) that the physical equipment can sustain without violating thermal, mechanical, or operational limits.

2. **:math:`P_{\max}(t)` (Maximum Feasible Power)**:
   The highest feasible power consumption (or maximum battery charging rate / full curtailment) feasible under current operating conditions.

3. **:math:`P_{\text{expected}}(t)` (Baseline Power)**:
   The expected default power trajectory if no transactive intervention occurs.

.. math::

   P_{\min}(t) \le P_{\text{expected}}(t) \le P_{\max}(t) \quad \forall t

Core Schemas in ``tent.flexibility``
------------------------------------

* **``Interval``**: An immutable, timezone-aware time span defined by ``start`` (UTC datetime) and ``duration_s`` (positive integer duration in seconds).
* **``EnvelopePoint``**: The physical bounds (:math:`q_{\min}`, :math:`q_{\max}`, :math:`q_{\text{baseline}}`) associated with a specific ``Interval``.
* **``FlexibilityEnvelope``**: An ordered sequence of non-overlapping ``EnvelopePoint`` records spanning the market horizon.
* **``DispatchCommand``**: Actuation command conveying the cleared target power, target interval, and originating market ID.
* **``DispatchResponse``**: Physical acknowledgment from the simulator confirming acceptance, actual power delivered, and any physical limit violations.
* **``Limits``**: Absolute physical ratings (e.g. nameplate inverter kW, maximum ramp rates) defining the device's operational boundary.
