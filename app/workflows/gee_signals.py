"""Google Earth Engine signal extraction utilities for agronomic risk analysis.

Purpose:
- Initialize Earth Engine credentials in local/cloud environments.
- Compute NDVI, rainfall, SAR, and baseline/z-score indicators.
- Derive deterministic helper metrics for loan underwriting nodes.

Used by:
- `app.workflows.nodes.loan.satellite`.
- `app.workflows.nodes.climate.chirps`.
- `app.workflows.gee_artifacts`.

Assumptions:
- Service-account credentials are available through env var or expected files.
- Callers supply valid `ee.Geometry` objects.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ee

from app.config import service_account

# nigeria aez
NIGERIA_AEZ_CONFIG: Dict[str, Dict[str, Any]] = {
    "Mangrove/Coastal": {
        "rainfall_threshold": 2500,
        "ndvi_target": (0.80, 0.90),
        "stress_threshold": 0.60,
        "seasonality": "Bimodal",
    },
    "Freshwater Swamp": {
        "rainfall_threshold": 2000,
        "ndvi_target": (0.75, 0.85),
        "stress_threshold": 0.55,
        "seasonality": "Bimodal",
    },
    "Tropical Rainforest": {
        "rainfall_threshold": 1500,
        "ndvi_target": (0.70, 0.82),
        "stress_threshold": 0.50,
        "seasonality": "Bimodal",
    },
    "Derived Savanna": {
        "rainfall_threshold": 1200,
        "ndvi_target": (0.65, 0.75),
        "stress_threshold": 0.45,
        "seasonality": "Bimodal",
    },
    "Southern Guinea": {
        "rainfall_threshold": 1000,
        "ndvi_target": (0.60, 0.75),
        "stress_threshold": 0.40,
        "seasonality": "Bimodal",
    },
    "Northern Guinea": {
        "rainfall_threshold": 800,
        "ndvi_target": (0.55, 0.70),
        "stress_threshold": 0.35,
        "seasonality": "Unimodal",
    },
    "Sudan Savanna": {
        "rainfall_threshold": 600,
        "ndvi_target": (0.45, 0.60),
        "stress_threshold": 0.30,
        "seasonality": "Unimodal",
    },
    "Sahel Savanna": {
        "rainfall_threshold": 300,
        "ndvi_target": (0.35, 0.50),
        "stress_threshold": 0.25,
        "seasonality": "Unimodal",
    },
}


def aez_from_lat(lat: float) -> str:
    """Deterministic AEZ lookup from latitude (south → north)."""
    if lat > 12:
        return "Sahel Savanna"
    if lat > 11:
        return "Sudan Savanna"
    if lat > 9:
        return "Northern Guinea"
    if lat > 7:
        return "Southern Guinea"
    if lat > 6:
        return "Derived Savanna"
    if lat > 5:
        return "Tropical Rainforest"
    if lat > 4.5:
        return "Freshwater Swamp"
    return "Mangrove/Coastal"


# gee initialziation
_GEE_INITIALIZED: bool = False


def ensure_gee_initialized() -> bool:
    """Idempotent Earth Engine initialization for Cloud Run/local dev."""
    global _GEE_INITIALIZED
    if _GEE_INITIALIZED:
        return True

    try:
        # inline JSON in env var
        creds_json = os.getenv("GEE_CREDENTIALS_JSON")
        if creds_json:
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                f.write(creds_json)
                temp_path = f.name
            credentials = ee.ServiceAccountCredentials(service_account, temp_path)
            ee.Initialize(credentials)
            _GEE_INITIALIZED = True
            return True

        # known file paths
        root = Path(__file__).resolve().parents[2]
        candidates = [
            root / "earth-engine.json",
            Path("/app/earth-engine.json"),
        ]
        for p in candidates:
            if p.exists():
                credentials = ee.ServiceAccountCredentials(service_account, str(p))
                ee.Initialize(credentials)
                _GEE_INITIALIZED = True
                return True

        return False
    except Exception:
        return False


# sentinel 2
def _mask_s2_sr(image: ee.Image) -> ee.Image:
    """Apply Sentinel-2 QA60 cloud/cirrus mask and scale reflectance values."""
    qa = image.select("QA60")
    cloud_bit_mask = 1 << 10
    cirrus_bit_mask = 1 << 11
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cirrus_bit_mask).eq(0))
    return image.updateMask(mask).divide(10000)


def _add_ndvi(image: ee.Image) -> ee.Image:
    """Add NDVI band derived from NIR/RED channels."""
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    return image.addBands(ndvi)


# ee queries
def get_ndvi_timeseries(
    farm_area: ee.Geometry, months_back: int = 12
) -> List[Dict[str, Any]]:
    """Real monthly NDVI mean series (Sentinel-2 SR Harmonized).

    Does server-side computation and performs ONE getInfo() for the whole series.
    """
    end_dt = datetime.now(timezone.utc)
    end = ee.Date(end_dt.strftime("%Y-%m-%d"))
    start = end.advance(-int(months_back), "month")

    def month_feature(i: Any) -> ee.Feature:
        """Build one monthly NDVI summary feature for server-side mapping."""
        i = ee.Number(i)
        m_start = start.advance(i, "month")
        m_end = m_start.advance(1, "month")

        coll = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(farm_area)
            .filterDate(m_start, m_end)
            .map(_mask_s2_sr)
            .map(_add_ndvi)
            .select("NDVI")
        )

        count = coll.size()

        ndvi_mean = ee.Algorithms.If(
            count.gt(0),
            coll.mean()
            .reduceRegion(
                reducer=ee.Reducer.mean(), geometry=farm_area, scale=10, bestEffort=True
            )
            .get("NDVI"),
            None,
        )

        return ee.Feature(
            None,
            {
                "date": m_start.format("YYYY-MM-01"),
                "ndvi_mean": ndvi_mean,
                "scene_count": count,
            },
        )

    fc = ee.FeatureCollection(
        ee.List.sequence(0, int(months_back) - 1).map(month_feature)
    )
    info = fc.getInfo()
    feats = (info or {}).get("features") or []

    out: List[Dict[str, Any]] = []
    for f in feats:
        props = (f or {}).get("properties") or {}
        out.append(
            {
                "date": props.get("date"),
                "ndvi_mean": props.get("ndvi_mean"),
                "scene_count": props.get("scene_count") or 0,
            }
        )
    return out


# chirps
def get_chirps_rainfall_30d_mm(
    farm_area: ee.Geometry,
) -> Tuple[Optional[float], Optional[str]]:
    """CHIRPS rainfall over last 30 available days (mm). Returns (value, error)."""
    try:
        chirps_all = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterBounds(farm_area)
        latest = chirps_all.sort("system:time_start", False).first()
        latest_date = ee.Date(latest.get("system:time_start"))
        start_date = latest_date.advance(-30, "day")

        coll = chirps_all.filterDate(start_date, latest_date)
        count = coll.size()

        rainfall = ee.Algorithms.If(
            count.gt(0),
            coll.sum()
            .reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=farm_area,
                scale=5000,
                bestEffort=True,
            )
            .get("precipitation"),
            None,
        )
        val = rainfall.getInfo()
        if val is None:
            return None, "NO_RAINFALL_VALUE"
        return float(val), None
    except Exception as e:
        return None, str(e)


# sar biomass
def get_sar_biomass(farm_area: ee.Geometry, seasonality: str) -> Optional[float]:
    """Sentinel-1 VV/VH ratio for biomass estimation in cloudy bimodal zones.

    SAR penetrates clouds, providing an independent vegetation signal
    for southern Nigeria where optical imagery is frequently obscured.
    Returns None for unimodal zones where SAR adds less value.
    """
    if seasonality != "Bimodal":
        return None

    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=60)

        s1 = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(farm_area)
            .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .mean()
        )

        ratio = s1.select("VH").subtract(s1.select("VV")).rename("ratio")
        val = (
            ratio.reduceRegion(reducer=ee.Reducer.mean(), geometry=farm_area, scale=10)
            .get("ratio")
            .getInfo()
        )
        return float(val) if val is not None else None
    except Exception:
        return None


# z baseline
def get_modis_zscore_baseline(
    farm_area: ee.Geometry,
) -> Tuple[Optional[float], Optional[float]]:
    """10-year MODIS NDVI baseline for the current calendar month.

    Returns (historical_mean, historical_std) scaled to Sentinel-2 range (0-1).
    Compares against the same month across 10 years to account for seasonality.
    """
    try:
        # Expand buffer for MODIS (250m resolution)
        hist_area = farm_area.centroid().buffer(500)

        now = datetime.now(timezone.utc)
        month = now.month
        start_year = now.year - 10

        modis = ee.ImageCollection("MODIS/061/MOD13Q1").filterBounds(hist_area)
        hist_col = modis.filter(
            ee.Filter.calendarRange(month, month, "month")
        ).filterDate(f"{start_year}-01-01", now.strftime("%Y-%m-%d"))

        if hist_col.size().getInfo() == 0:
            return None, None

        stats_img = hist_col.select("NDVI").reduce(
            ee.Reducer.mean().combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True)
        )

        stats = stats_img.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=hist_area,
            scale=250,
        ).getInfo()

        # MODIS NDVI is scaled by 10000
        mean = (stats.get("NDVI_mean") or 0.0) / 10000.0
        std = (stats.get("NDVI_stdDev") or 0.0) / 10000.0

        if std < 0.01:
            return mean, None
        return mean, std
    except Exception:
        return None, None


def compute_modis_zscore(
    current_ndvi: float,
    hist_mean: Optional[float],
    hist_std: Optional[float],
) -> Optional[float]:
    """Compute z-score from MODIS baseline. Falls back to None if baseline unavailable."""
    if hist_mean is None or hist_std is None:
        return None
    if hist_std < 0.01:
        return None
    return round((current_ndvi - hist_mean) / hist_std, 2)


def field_snap(farm_area: ee.Geometry) -> Tuple[Optional[Dict[str, float]], float]:
    """Find nearest agricultural land if pin is on a non-farm surface.

    Samples 50 random points in the buffer zone, filters for vegetated pixels
    (NDVI > 0.15), and snaps to the point closest to the median NDVI.

    Using median (not max) prevents positive bias — we assess the typical
    farm condition, not the single best pixel.

    Returns (new_coords_dict_or_None, representative_ndvi).
    """
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=60)

        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(farm_area)
            .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        )

        if s2.size().getInfo() == 0:
            return None, 0.0

        ndvi_img = s2.median().normalizedDifference(["B8", "B4"]).rename("NDVI")

        sample_points = ee.FeatureCollection.randomPoints(farm_area, 50)
        samples = ndvi_img.sampleRegions(
            collection=sample_points, scale=10, geometries=True
        )

        # Filter to vegetated pixels only
        vegetated = samples.filter(ee.Filter.gt("NDVI", 0.15))
        veg_count = vegetated.size().getInfo()

        if veg_count == 0:
            return None, 0.0

        # Get statistics of vegetated pixels
        stats = vegetated.aggregate_stats("NDVI").getInfo()
        median_ndvi = stats.get("mean", 0.0)  # mean as proxy for median

        # Find the sample closest to median NDVI (not max — avoids positive bias)
        samples_list = vegetated.toList(50).getInfo()

        best_sample = None
        min_diff = float("inf")
        for sample in samples_list:
            sample_ndvi = sample["properties"]["NDVI"]
            diff = abs(sample_ndvi - median_ndvi)
            if diff < min_diff:
                min_diff = diff
                best_sample = sample

        if not best_sample:
            return None, 0.0

        new_coords = best_sample["geometry"]["coordinates"]  # [lng, lat]
        representative_ndvi = best_sample["properties"]["NDVI"]

        return {"lat": new_coords[1], "lng": new_coords[0]}, representative_ndvi
    except Exception:
        return None, 0.0


# helpers
def z_score_from_series(
    ndvi_series: List[Dict[str, Any]], current_ndvi: Optional[float]
) -> Optional[float]:
    """Fallback z-score from 12-month Sentinel-2 series (used when MODIS unavailable)."""
    if current_ndvi is None:
        return None
    vals = []
    for row in ndvi_series or []:
        v = row.get("ndvi_mean")
        if v is None:
            continue
        try:
            vals.append(float(v))
        except Exception:
            continue
    if len(vals) < 6:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / max(1, (len(vals) - 1))
    std = var**0.5
    if std <= 1e-6:
        return None
    return (float(current_ndvi) - mean) / std


def compute_ndvi_trend(ndvi_series: List[Dict[str, Any]]) -> Optional[float]:
    """Return a simple last-step delta: (last - prev), using last non-null points."""
    vals: List[float] = []
    for row in ndvi_series or []:
        v = row.get("ndvi_mean")
        if v is None:
            continue
        try:
            vals.append(float(v))
        except Exception:
            continue
    if len(vals) < 2:
        return None
    return vals[-1] - vals[-2]
