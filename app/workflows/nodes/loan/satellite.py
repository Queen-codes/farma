"""
This module;
- provides satellite analysis using: NDVI series, rainfall, thumbnails, SAR biomass, MODIS baseline
"""

from __future__ import annotations

import logging

from app.workflows.state import FarmaState

logger = logging.getLogger(__name__)


async def satellite_analysis_node(state: FarmaState) -> dict:
    """Multi satellite data for loan underwriting."""
    from app.workflows.job_events import emit_event
    from app.workflows.gee_signals import (
        NIGERIA_AEZ_CONFIG,
        aez_from_lat,
        compute_modis_zscore,
        compute_ndvi_trend,
        ensure_gee_initialized,
        field_snap,
        get_chirps_rainfall_30d_mm,
        get_modis_zscore_baseline,
        get_ndvi_timeseries,
        get_sar_biomass,
        z_score_from_series,
    )
    from app.workflows.gee_artifacts import get_gee_thumbnails

    emit_event("satellite_started", step="satellite_check")

    coords = state.get("coordinates") or {}
    if not coords:
        emit_event(
            "satellite_done",
            status="failed",
            step="satellite_check",
            payload={"error": "missing_coordinates"},
        )
        return {"risk_flags": ["SYSTEM_ERROR"], "climate_score": 0.0}

    if not ensure_gee_initialized():
        emit_event(
            "satellite_done",
            status="failed",
            step="satellite_check",
            payload={"error": "gee_not_initialized"},
        )
        return {"risk_flags": ["SYSTEM_ERROR"], "climate_score": 0.0}

    try:
        import asyncio

        import ee

        risk_flags: list[str] = []

        lat = float(coords["lat"])
        lng = float(coords["lng"])
        radius_m = int(
            coords.get("uncertainty_radius_m") or coords.get("suggested_buffer") or 800
        )
        radius_m = max(150, min(radius_m, 1200))

        # Determine AEZ from latitude
        zone_name = aez_from_lat(lat)
        aez_config = NIGERIA_AEZ_CONFIG.get(
            zone_name, NIGERIA_AEZ_CONFIG["Northern Guinea"]
        )
        seasonality = aez_config["seasonality"]
        stress_threshold = aez_config["stress_threshold"]
        ndvi_target = aez_config["ndvi_target"]

        point = ee.Geometry.Point([lng, lat])
        farm_area = point.buffer(radius_m)

        # Run all independent EE queries concurrently
        (
            ndvi_series,
            (rainfall_30d, rainfall_err),
            thumbs,
            sar_biomass,
            (hist_mean, hist_std),
        ) = await asyncio.gather(
            asyncio.to_thread(get_ndvi_timeseries, farm_area, 12),
            asyncio.to_thread(get_chirps_rainfall_30d_mm, farm_area),
            asyncio.to_thread(get_gee_thumbnails, farm_area, None),
            asyncio.to_thread(get_sar_biomass, farm_area, seasonality),
            asyncio.to_thread(get_modis_zscore_baseline, farm_area),
        )

        # Extract current NDVI from most recent non-null entry
        current_ndvi: float | None = None
        for row in reversed(ndvi_series):
            v = row.get("ndvi_mean")
            if v is None:
                continue
            try:
                current_ndvi = float(v)
                break
            except Exception:
                continue

        # then,  field snap — if pin is on non-farm surface due to low ndvi < 0.1 being detected
        snapped = False
        retry_count = coords.get("retry_count", 0)

        if current_ndvi is not None and current_ndvi < 0.10:
            logger.info(
                "PIN FAILED (NDVI=%.2f): Likely a building or road. Searching nearby fields...",
                current_ndvi,
            )
            try:
                snapped_coords, snapped_ndvi = await asyncio.wait_for(
                    asyncio.to_thread(field_snap, farm_area),
                    timeout=30.0,
                )
            except TimeoutError:
                logger.warning("field_snap timed out after 30s")
                snapped_coords, snapped_ndvi = None, 0.0

            if snapped_coords and snapped_ndvi > 0.15:
                logger.info(
                    f"FIELD SNAP SUCCESS: Moved to "
                    f"({snapped_coords['lat']:.4f}, {snapped_coords['lng']:.4f}) "
                    f"| New NDVI: {snapped_ndvi:.2f}"
                )
                coords = {**coords, **snapped_coords}
                current_ndvi = snapped_ndvi
                snapped = True
                # tighten buffer once snapped to actual farm
                point = ee.Geometry.Point([coords["lng"], coords["lat"]])
                farm_area = point.buffer(100)
            else:
                # Field snap failed — check retry count
                if retry_count < 1:
                    risk_flags.append("LOCATION_REVIEW_REQUIRED")
                    coords["retry_count"] = retry_count + 1
                    logger.info("No vegetation found. Flagging for location review.")
                else:
                    risk_flags.append("GHOST_FARM_DETECTED")
                    logger.info("No vegetation found after retry. Ghost farm detected.")

        #  Compute z-score — prefer MODIS 10-year, fallback to series
        z_score = (
            compute_modis_zscore(current_ndvi, hist_mean, hist_std)
            if current_ndvi is not None
            else None
        )
        if z_score is None:
            z_score = z_score_from_series(ndvi_series, current_ndvi)

        ndvi_trend = compute_ndvi_trend(ndvi_series)

        # AEZ-aware risk flags
        if rainfall_err:
            risk_flags.append("RAINFALL_DATA_INCOMPLETE")
        if current_ndvi is None:
            risk_flags.append("SATELLITE_DATA_INSUFFICIENT")
        elif (
            current_ndvi < stress_threshold and "GHOST_FARM_DETECTED" not in risk_flags
        ):
            risk_flags.append("BELOW_AEZ_STRESS_THRESHOLD")

        phenology_status = "NORMAL"
        if current_ndvi is not None and current_ndvi < ndvi_target[0]:
            phenology_status = "BELOW_TARGET"

        satellite_report = {
            "ndvi": current_ndvi,
            "ndvi_series": ndvi_series,
            "ndvi_trend": ndvi_trend,
            "rainfall_30d": rainfall_30d,
            "sar_biomass": sar_biomass,
            "z_score": z_score,
            "z_score_source": (
                "MODIS_10yr"
                if (hist_mean is not None and hist_std is not None)
                else "S2_12mo"
            ),
            "field_snapped": snapped,
            "phenology_status": phenology_status,
            "data_quality": {
                "ndvi_available": current_ndvi is not None,
                "rainfall_available": rainfall_30d is not None,
                "zscore_available": z_score is not None,
                "sar_available": sar_biomass is not None,
                "rainfall_error": rainfall_err,
            },
            "provenance": {
                "ndvi_dataset": "COPERNICUS/S2_SR_HARMONIZED",
                "rainfall_dataset": "UCSB-CHG/CHIRPS/DAILY",
                "sar_dataset": "COPERNICUS/S1_GRD" if sar_biomass is not None else None,
                "zscore_dataset": (
                    "MODIS/061/MOD13Q1" if hist_mean is not None else "S2_12mo_fallback"
                ),
                "buffer_m": radius_m,
            },
        }

        # AEZ context for the underwriter
        aez_context = {
            "zone_name": zone_name,
            "target_ndvi": ndvi_target,
            "stress_threshold": stress_threshold,
            "seasonality": seasonality,
        }

        emit_event(
            "satellite_done",
            status="completed",
            step="satellite_check",
            payload={
                "ndvi": current_ndvi,
                "zone": zone_name,
                "field_snapped": snapped,
                "sar_available": sar_biomass is not None,
            },
        )

        climate_score = 0.0
        if current_ndvi is not None:
            climate_score = max(0.0, min(float(current_ndvi) / 0.8, 1.0))

        return {
            "coordinates": coords,
            "satellite_report": satellite_report,
            "nigeria_aez_context": aez_context,
            "visualization_artifacts": {
                "rgb_thumb_url": thumbs.get("rgb_thumb_url"),
                "ndvi_thumb_url": thumbs.get("ndvi_thumb_url"),
            },
            "risk_flags": risk_flags,
            "climate_score": climate_score,
        }

    except Exception as e:
        logger.exception("Satellite analysis error: %s", e)
        emit_event(
            "satellite_done",
            status="failed",
            step="satellite_check",
            payload={"error": str(e)},
        )
        return {"risk_flags": ["SCORING_ERROR"], "climate_score": 0.0}


__all__ = ["satellite_analysis_node"]
