"""Google Earth Engine artifact helpers for visualization thumbnails.

Purpose:
- Generate RGB and NDVI thumbnail URLs for a farm geometry.
- Provide lightweight visualization metadata for frontend diagnostics.

Used by:
- `app.workflows.nodes.loan.satellite`.

Assumptions:
- Earth Engine has already been initialized by caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import ee

from app.workflows.gee_signals import _add_ndvi, _mask_s2_sr


def get_gee_thumbnails(
    farm_area: ee.Geometry,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return RGB and NDVI thumbnail URLs for a recent Sentinel-2 composite.

    Args:
        farm_area: Earth Engine geometry region to render.
        end_date: Optional end date for the 60-day compositing window.

    Returns:
        Dict[str, Any]: URLs and render parameters for RGB/NDVI thumbnails.

    Raises:
        Exception: Can propagate Earth Engine API failures.

    Side Effects:
        Makes Earth Engine API calls to compute composites and thumbnail URLs.

    Latency:
        Network-bound Earth Engine operations.
    """
    end_dt = end_date or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=60)

    start = ee.Date(start_dt.strftime("%Y-%m-%d"))
    end = ee.Date(end_dt.strftime("%Y-%m-%d"))

    coll = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(farm_area)
        .filterDate(start, end)
        .map(_mask_s2_sr)
    )

    composite = coll.median()
    rgb_vis = {"bands": ["B4", "B3", "B2"], "min": 0.0, "max": 0.3}
    rgb = composite.visualize(**rgb_vis)

    ndvi = _add_ndvi(composite).select("NDVI")
    ndvi_vis = {
        "min": 0.0,
        "max": 0.8,
        "palette": [
            "#7f0000",
            "#d7301f",
            "#fc8d59",
            "#fee08b",
            "#d9ef8b",
            "#91cf60",
            "#1a9850",
        ],
    }
    ndvi_img = ndvi.visualize(**ndvi_vis)

    params = {"region": farm_area, "dimensions": 1280, "format": "png"}
    rgb_url = rgb.getThumbURL(params)
    ndvi_url = ndvi_img.getThumbURL(params)

    return {
        "rgb_thumb_url": rgb_url,
        "ndvi_thumb_url": ndvi_url,
        "params": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "dimensions": 1280,
        },
    }
