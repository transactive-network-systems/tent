"""pvlib adapter: real solar physics as a flexible resource.

Replaces hand-rolled solar approximations with pvlib's model chain.

For context, TENT currently models a 120 kW PV site as
(``solar_pv_resource_model.py:85``)::

    if hour_of_day < 5.5 or hour_of_day > 17.5:
        power = 0.0
    else:
        clear_sky_power = 0.5 * (1 + math.cos((hour_of_day - 12) * 2 * pi / 12))
        power = self.maximum_power * clear_sky_power * self.cloudFactor

Sunrise fixed at 05:30 and sunset at 17:30, every day of the year, with a
hand-typed ``cloud_factor``. No latitude, tilt, azimuth, module temperature,
inverter clipping, or actual weather.

This adapter routes the same question through
:class:`pvlib.modelchain.ModelChain`, which accounts for all of the above.

Sign convention
---------------
pvlib reports generation as a **positive** number. The canonical convention here
is load-positive, so PV output becomes **negative**. That conversion happens once,
via :meth:`~der_interface.conventions.SignConvention.generation`, and getting it
wrong is the single most likely bug in a PV integration.

Curtailment
-----------
With ``curtailable=False`` (the default), PV is must-take:
``q_min == q_max == q_baseline``. With ``curtailable=True``, ``q_min`` becomes
0 kW, and the same array can bid curtailment into a market. That is a one-flag
change, not a different class.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Protocol, Sequence

from ...conventions import Quality, SignConvention
from ...errors import AdapterError, CapabilityError
from ...schema import (
    EnvelopePoint,
    FlexibilityEnvelope,
    Forecast,
    ForecastPoint,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
)

__all__ = ["PvlibPvAdapter", "WeatherSource", "ClearSkyWeather", "CloudCoverWeather", "DataFrameWeather"]


def _require_pvlib():
    try:
        import pvlib  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise AdapterError(
            "the pvlib adapter requires pvlib and pandas. "
            "Install with: pip install 'der-interface[pvlib]'"
        ) from exc
    return pvlib


class WeatherSource(Protocol):
    """Supplies weather for a set of timestamps.

    The abstraction that lets the same adapter run on synthetic clear-sky data
    in a unit test, a TMY file in a planning study, and a live forecast API in
    deployment -- without the adapter or its consumers changing.

    Must return a DataFrame indexed by the requested times with at least
    ``ghi``, ``dni``, ``dhi``, and ideally ``temp_air`` and ``wind_speed``.
    """

    def get(self, times: Any) -> Any: ...


class ClearSkyWeather:
    """Clear-sky irradiance from pvlib's Ineichen model.

    Requires no data files and no network, so it is the right default for tests
    and first integration. Optimistic by construction -- it is a physical upper
    bound, not a forecast. ``cloud_factor`` provides a crude derate for
    comparison against legacy models, but a real weather source should replace it.
    """

    def __init__(self, location: Any, cloud_factor: float = 1.0,
                 temp_air_c: float = 20.0, wind_speed_m_s: float = 1.0) -> None:
        if not 0.0 <= cloud_factor <= 1.0:
            raise AdapterError(f"cloud_factor must be in [0, 1], got {cloud_factor}")
        self._location = location
        self._cloud_factor = float(cloud_factor)
        self._temp_air_c = float(temp_air_c)
        self._wind_speed_m_s = float(wind_speed_m_s)

    def get(self, times: Any) -> Any:
        weather = self._location.get_clearsky(times)
        if self._cloud_factor != 1.0:
            weather = weather * self._cloud_factor
        weather["temp_air"] = self._temp_air_c
        weather["wind_speed"] = self._wind_speed_m_s
        return weather


class CloudCoverWeather:
    """Intermittent / cloudy weather producing spiky, realistic irradiance profiles.

    Applies diurnal cloud transients, stochastic cloud passing dips, and rapid ramps
    on top of clear-sky irradiance to simulate non-clear days and solar variability.
    """

    def __init__(
        self,
        location: Any,
        cloud_factor: float = 0.75,
        volatility: float = 0.5,
        temp_air_c: float = 22.0,
        wind_speed_m_s: float = 2.5,
        seed: int = 42,
    ) -> None:
        if not 0.0 <= cloud_factor <= 1.0:
            raise AdapterError(f"cloud_factor must be in [0, 1], got {cloud_factor}")
        self._location = location
        self._cloud_factor = float(cloud_factor)
        self._volatility = float(volatility)
        self._temp_air_c = float(temp_air_c)
        self._wind_speed_m_s = float(wind_speed_m_s)
        self._seed = seed

    def get(self, times: Any) -> Any:
        import numpy as np
        import pandas as pd

        weather = self._location.get_clearsky(times).copy()
        n = len(weather)
        if n == 0:
            return weather

        rng = np.random.RandomState(self._seed)
        t = np.linspace(0, 4 * np.pi, n)
        slow_clouds = 0.15 * np.sin(t * 1.5) + 0.1 * np.cos(t * 3.2)
        fast_noise = rng.normal(0, 0.12 * self._volatility, size=n)

        dips = np.ones(n)
        num_dips = max(1, int(n / 8))
        for _ in range(num_dips):
            dip_idx = rng.randint(0, n)
            width = max(1, rng.randint(1, max(2, int(n / 12))))
            depth = rng.uniform(0.35, 0.85) * min(1.0, self._volatility + 0.2)
            low_idx = max(0, dip_idx - width)
            high_idx = min(n, dip_idx + width)
            dips[low_idx:high_idx] *= (1.0 - depth)

        transmittance = np.clip(self._cloud_factor + slow_clouds + fast_noise, 0.15, 1.0) * dips
        transmittance = np.clip(transmittance, 0.05, 1.0)

        for col in ["ghi", "dni", "dhi"]:
            if col in weather:
                weather[col] = weather[col] * transmittance

        weather["temp_air"] = self._temp_air_c + rng.normal(0, 0.5, size=n)
        weather["wind_speed"] = np.maximum(0.1, self._wind_speed_m_s + rng.normal(0, 0.8, size=n))
        return weather


class DataFrameWeather:
    """Weather from a pandas DataFrame: TMY, PVGIS, ERA5, or a live API.

    Reindexes onto the requested times. ``pvlib.iotools`` readers all produce
    frames compatible with this.
    """

    def __init__(self, frame: Any, method: str = "nearest", tolerance: str | None = "1h") -> None:
        self._frame = frame
        self._method = method
        self._tolerance = tolerance

    def get(self, times: Any) -> Any:
        try:
            return self._frame.reindex(times, method=self._method, tolerance=self._tolerance)
        except Exception as exc:
            raise AdapterError(
                f"weather frame could not be reindexed onto requested times: {exc}. "
                f"Check that the frame index is timezone-aware and overlaps the horizon."
            ) from exc


class PvlibPvAdapter:
    """A PV array exposed as a flexible resource.

    Satisfies :class:`~der_interface.protocols.FlexibilityProvider` and
    :class:`~der_interface.protocols.ForecastProvider`.

    Deliberately does **not** satisfy
    :class:`~der_interface.protocols.DispatchTarget`: you cannot command the sun.
    A curtailable array is still not dispatchable through pvlib -- curtailment
    would be enforced by an inverter adapter downstream.

    Example:
        >>> from pvlib.location import Location
        >>> from pvlib.pvsystem import PVSystem
        >>> from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS
        >>> loc = Location(46.28, -119.28, tz="US/Pacific")
        >>> system = PVSystem(
        ...     surface_tilt=30, surface_azimuth=180,
        ...     module_parameters={"pdc0": 120_000, "gamma_pdc": -0.004},
        ...     inverter_parameters={"pdc0": 120_000},
        ...     temperature_model_parameters=(
        ...         TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
        ...     ),
        ... )
        >>> adapter = PvlibPvAdapter.from_location(
        ...     "campus_pv", loc, system, rated_kw=120.0
        ... )
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        model_chain: Any,
        weather_source: WeatherSource,
        rated_kw: float,
        curtailable: bool = False,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> None:
        _require_pvlib()
        if rated_kw <= 0.0:
            raise AdapterError(f"rated_kw must be > 0, got {rated_kw}")

        self._identity = identity
        self._mc = model_chain
        self._weather = weather_source
        self._rated_kw = float(rated_kw)
        self._curtailable = bool(curtailable)
        self._sign = sign

    # -- construction ------------------------------------------------------

    @classmethod
    def from_location(
        cls,
        resource_id: str,
        location: Any,
        system: Any,
        rated_kw: float,
        weather_source: WeatherSource | None = None,
        curtailable: bool = False,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        aoi_model: str = "physical",
        spectral_model: str = "no_loss",
        **identity_kwargs: Any,
    ) -> "PvlibPvAdapter":
        """Build from a pvlib ``Location`` and ``PVSystem``.

        Defaults ``spectral_model="no_loss"`` because the spectral correction
        requires module parameters that simple PVWatts-style definitions lack;
        it would otherwise fail at construction for the most common setup.
        """
        pvlib = _require_pvlib()
        from pvlib.modelchain import ModelChain

        try:
            model_chain = ModelChain(
                system, location, aoi_model=aoi_model, spectral_model=spectral_model
            )
        except Exception as exc:
            raise AdapterError(f"could not build pvlib ModelChain: {exc}") from exc

        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.RENEWABLE_GENERATION,
            source=f"pvlib {pvlib.__version__}",
            location=identity_kwargs.pop(
                "location_name", f"{location.latitude},{location.longitude}"
            ),
            metadata={
                "latitude": location.latitude,
                "longitude": location.longitude,
                "tz": str(location.tz),
                "rated_kw": float(rated_kw),
                "curtailable": bool(curtailable),
                **identity_kwargs,
            },
        )
        return cls(
            identity=identity,
            model_chain=model_chain,
            weather_source=weather_source or ClearSkyWeather(location),
            rated_kw=rated_kw,
            curtailable=curtailable,
            sign=sign,
        )

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        location: Mapping[str, Any],
        system: Mapping[str, Any],
        rated_kw: float,
        weather: Mapping[str, Any] | None = None,
        curtailable: bool = False,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "PvlibPvAdapter":
        """Build entirely from plain dictionaries.

        Exists so a JSON or YAML configuration file can define a PV resource
        without constructing pvlib objects. :meth:`from_location` requires live
        ``Location`` and ``PVSystem`` instances, which a config file cannot
        express.

        Example config::

            {
              "resource_id": "campus_pv",
              "rated_kw": 120.0,
              "location": {"latitude": 46.28, "longitude": -119.28,
                           "tz": "US/Pacific", "altitude": 120},
              "system": {"surface_tilt": 30, "surface_azimuth": 180},
              "weather": {"kind": "clearsky", "cloud_factor": 0.85}
            }

        Args:
            location: ``latitude``, ``longitude``, and optionally ``tz``,
                ``altitude``, ``name``.
            system: ``surface_tilt``, ``surface_azimuth``, and optionally
                ``gamma_pdc``, ``temperature_model``, ``module_type``. The DC
                rating defaults to *rated_kw* so it need not be repeated.
            weather: ``{"kind": "clearsky", ...}`` or
                ``{"kind": "tmy", "path": ...}``. Defaults to clear-sky.
        """
        _require_pvlib()
        from pvlib.location import Location
        from pvlib.pvsystem import PVSystem
        from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

        location_kwargs = dict(location)
        try:
            latitude = float(location_kwargs.pop("latitude"))
            longitude = float(location_kwargs.pop("longitude"))
        except KeyError as exc:
            raise AdapterError(
                f"location config for {resource_id!r} is missing {exc}; "
                f"latitude and longitude are required"
            ) from exc

        # Only pass altitude when supplied. Omitting it lets pvlib look up real
        # terrain elevation, which is a better default than forcing sea level.
        altitude = location_kwargs.pop("altitude", None)
        location_args = {
            "latitude": latitude,
            "longitude": longitude,
            "tz": location_kwargs.pop("tz", "UTC"),
            "name": location_kwargs.pop("name", resource_id),
        }
        if altitude is not None:
            location_args["altitude"] = float(altitude)

        loc = Location(**location_args)

        system_kwargs = dict(system)
        pdc0 = float(system_kwargs.pop("pdc0", rated_kw * 1000.0))
        gamma_pdc = float(system_kwargs.pop("gamma_pdc", -0.004))
        temp_model = system_kwargs.pop("temperature_model", "open_rack_glass_glass")
        try:
            temp_params = TEMPERATURE_MODEL_PARAMETERS["sapm"][temp_model]
        except KeyError as exc:
            raise AdapterError(
                f"unknown temperature_model {temp_model!r}; valid options: "
                f"{sorted(TEMPERATURE_MODEL_PARAMETERS['sapm'])}"
            ) from exc

        pv_system = PVSystem(
            surface_tilt=float(system_kwargs.pop("surface_tilt", 30.0)),
            surface_azimuth=float(system_kwargs.pop("surface_azimuth", 180.0)),
            module_parameters={"pdc0": pdc0, "gamma_pdc": gamma_pdc},
            inverter_parameters={"pdc0": pdc0},
            temperature_model_parameters=temp_params,
            **system_kwargs,
        )

        return cls.from_location(
            resource_id=resource_id,
            location=loc,
            system=pv_system,
            rated_kw=rated_kw,
            weather_source=cls._weather_from_config(loc, weather),
            curtailable=curtailable,
            sign=sign,
            **kwargs,
        )

    @staticmethod
    def _weather_from_config(
        location: Any, config: Mapping[str, Any] | None
    ) -> WeatherSource:
        """Build a weather source from a config dict."""
        config = dict(config or {"kind": "clearsky"})
        kind = str(config.pop("kind", "clearsky")).lower()

        if kind == "clearsky":
            return ClearSkyWeather(location, **config)

        if kind in ("cloudy", "intermittent", "clouds"):
            return CloudCoverWeather(location, **config)

        if kind == "tmy":
            import os
            from pathlib import Path
            from pvlib.iotools import read_tmy3

            path = config.pop("path", None)
            if not path:
                raise AdapterError('weather kind "tmy" requires a "path"')
            if not os.path.exists(path):
                # Check bundled model directories
                pkg_root = Path(__file__).resolve().parents[4]
                candidates = [
                    pkg_root / "models" / "pvlib" / path,
                    pkg_root / "models" / "pvlib" / Path(path).name,
                    pkg_root / "models" / "gridlabd" / Path(path).name,
                ]
                found = None
                for c in candidates:
                    if c.exists():
                        found = str(c)
                        break
                if found:
                    path = found
                else:
                    raise AdapterError(f'weather TMY file not found at: {path}')

            frame, _ = read_tmy3(path, map_variables=True)
            return DataFrameWeather(frame, **config)

        if kind in ("dataframe", "frame"):
            frame = config.pop("frame", None)
            if frame is None:
                raise AdapterError('weather kind "dataframe" requires a "frame" object')
            return DataFrameWeather(frame, **config)

        raise AdapterError(
            f'unknown weather kind {kind!r}; supported: "clearsky", "cloudy", "tmy", "dataframe". '
            f"For custom sources build a DataFrameWeather or WeatherSource directly."
        )

    # -- identity ----------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def curtailable(self) -> bool:
        return self._curtailable

    # -- core computation --------------------------------------------------

    def _ac_kw(self, intervals: Sequence[Interval]) -> list[float]:
        """Run the model chain and return AC output in kW, one value per interval.

        Each interval is evaluated at its **midpoint**, which is the correct
        representative instant for an average-power value over the interval.
        Using the start instant systematically biases morning intervals low and
        evening intervals high.
        """
        import pandas as pd

        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        midpoints = pd.DatetimeIndex(
            [i.start + timedelta(seconds=i.duration_s / 2) for i in intervals]
        )

        weather = self._weather.get(midpoints)
        try:
            self._mc.run_model(weather)
        except Exception as exc:
            raise AdapterError(
                f"{self._identity.resource_id!r}: pvlib run_model failed: {exc}"
            ) from exc

        ac = self._mc.results.ac
        if hasattr(ac, "to_numpy"):
            values = ac.to_numpy()
        else:  # pragma: no cover
            values = list(ac)

        out: list[float] = []
        for value in values:
            watts = float(value)
            if watts != watts:  # NaN, from a weather gap
                watts = 0.0
            # pvlib inverters can report small negative AC (night tare loss).
            # Clamp: a market cannot act on -3 W of parasitic draw, and letting
            # it through would flip the sign of a generation-only resource.
            out.append(max(watts, 0.0) / 1000.0)
        return out

    # -- FlexibilityProvider ----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        """Feasible band for each interval.

        Must-take (default)::

            q_min == q_max == q_baseline == -(pvlib AC output)

        Curtailable::

            q_min = -(pvlib AC output)   full generation
            q_max = 0.0                  fully curtailed
            q_baseline = q_min           generate unless paid not to

        Under load-positive sign, generation is negative, so full output is the
        *minimum* and curtailment is the *maximum*. That inversion is why
        :meth:`EnvelopePoint.in_convention` swaps min and max.
        """
        ac_kw = self._ac_kw(intervals)
        points: list[EnvelopePoint] = []

        for interval, kw in zip(intervals, ac_kw):
            generation = self._sign.generation(kw)
            if self._curtailable:
                curtailed = self._sign.generation(0.0)
                lo, hi = min(generation, curtailed), max(generation, curtailed)
            else:
                lo = hi = generation
            points.append(
                EnvelopePoint(
                    interval=interval,
                    q_min_kw=lo,
                    q_max_kw=hi,
                    q_baseline_kw=generation,
                    quality=Quality.ESTIMATED,
                )
            )

        return FlexibilityEnvelope(self._identity, tuple(points), sign=self._sign)

    def get_limits(self, timestamp: Any = None) -> Limits:
        """Nameplate bounds, independent of weather."""
        rated = self._sign.generation(self._rated_kw)
        idle = self._sign.generation(0.0)
        return Limits(
            min_power_kw=min(rated, idle),
            max_power_kw=max(rated, idle),
            available=True,
            sign=self._sign,
            constraints={"rated_kw": self._rated_kw, "curtailable": self._curtailable},
        )

    # -- StateProvider (partial) -------------------------------------------

    def get_state(self, timestamp: Any = None) -> ResourceState:
        """Modeled instantaneous output.

        Always :attr:`~der_interface.conventions.Quality.ESTIMATED` -- this is a
        model, not a meter. Pair with a real meter adapter when measured output
        matters.
        """
        from ...conventions import utc_now

        moment = timestamp or utc_now()
        interval = Interval(moment, 60)
        kw = self._ac_kw([interval])[0]
        return ResourceState(
            identity=self._identity,
            timestamp=moment,
            values={"power_kw": self._sign.generation(kw), "ac_output_kw": kw},
            quality=Quality.ESTIMATED,
        )

    # -- ForecastProvider --------------------------------------------------

    def available_forecasts(self) -> tuple[str, ...]:
        return ("solar_power_kw", "ac_output_kw")

    def get_forecast(self, variable: str, intervals: Sequence[Interval]) -> Forecast:
        """Forecast PV output.

        ``solar_power_kw`` is sign-normalized (negative under load-positive).
        ``ac_output_kw`` is the raw generation magnitude, which is what other
        consumers -- OCHRE PV equipment, GridLAB-D solar objects, a TESP
        ``btm_solar_generation`` stream -- typically expect.
        """
        if variable not in self.available_forecasts():
            raise CapabilityError(
                f"{self._identity.resource_id!r} cannot forecast {variable!r}; "
                f"available: {self.available_forecasts()}"
            )
        ac_kw = self._ac_kw(intervals)
        normalize = variable == "solar_power_kw"
        points = tuple(
            ForecastPoint(
                interval=interval,
                value=self._sign.generation(kw) if normalize else kw,
                quality=Quality.ESTIMATED,
            )
            for interval, kw in zip(intervals, ac_kw)
        )
        return Forecast(variable, points, source=self._identity.source)

    def __repr__(self) -> str:
        return (
            f"PvlibPvAdapter({self._identity.resource_id!r}, "
            f"rated={self._rated_kw} kW, curtailable={self._curtailable})"
        )
