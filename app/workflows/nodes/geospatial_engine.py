import matplotlib

matplotlib.use("Agg")
import ee
import json
import asyncio
import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from langchain_google_genai import ChatGoogleGenerativeAI
from google.genai import types
from app.workflows.state import FarmaState
from app.config import (
    GOOGLE_API_KEY,
    MODEL_GROUNDING,
    MODEL_FLASH,
    service_account,
)
from pathlib import Path


NIGERIA_AEZ_CONFIG = {
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


class GeocodingResult(BaseModel):
    """Result of converting landmark to coordinates"""

    lat: float = Field(description="Latitude of the farm")
    lng: float = Field(description="Longitude of the farm")
    confidence: float = Field(
        description="Confidence score of the location match (0.0 to 1.0)"
    )
    spatial_uncertainty: int = Field(
        description="1-10 score. 1=Exact Pinpoint, 10=Entire City Region."
    )
    suggested_buffer: float = Field(
        description="Suggested search radius in meters based on uncertainty (e.g., 100m to 1000m)"
    )
    identified_aez: str = Field(
        description="The Nigerian Agro-Ecological Zone for this location."
    )
    identified_state: str = Field(
        description="Nigerian state where the location is found (e.g., Kano, Kaduna, Anambra)"
    )
    identified_lga: str | None = Field(
        default=None, description="Local Government Area if identifiable"
    )
    is_vague: bool = Field(
        description="True if the landmark could not be precisely located"
    )
    clarifying_question: str | None = Field(
        description="Question to ask if location is vague"
    )


# Nigeria geographic bounds for validation
NIGERIA_BOUNDS = {
    "lat_min": 4.0,
    "lat_max": 14.0,
    "lng_min": 2.5,
    "lng_max": 14.7,
}

# Minimum confidence thresholds
MIN_CONFIDENCE_FIRST_TIME = 0.6  # Higher bar for unknown farmers
MIN_CONFIDENCE_RETRY = 0.5  # Slightly lower on retry


def validate_nigeria_coordinates(lat: float, lng: float) -> tuple[bool, str]:
    """Validate that coordinates fall within Nigeria's boundaries."""
    if lat < NIGERIA_BOUNDS["lat_min"] or lat > NIGERIA_BOUNDS["lat_max"]:
        return False, f"Latitude {lat} outside Nigeria bounds ({NIGERIA_BOUNDS['lat_min']}-{NIGERIA_BOUNDS['lat_max']})"
    if lng < NIGERIA_BOUNDS["lng_min"] or lng > NIGERIA_BOUNDS["lng_max"]:
        return False, f"Longitude {lng} outside Nigeria bounds ({NIGERIA_BOUNDS['lng_min']}-{NIGERIA_BOUNDS['lng_max']})"
    return True, "Coordinates within Nigeria"


# Grounding: Gemini 2.5 Pro
llm_base = ChatGoogleGenerativeAI(
    model=MODEL_GROUNDING,
    google_api_key=GOOGLE_API_KEY,
    convert_system_message_to_human=True,
)
tools = [
    types.Tool(google_maps=types.GoogleMaps()),
    types.Tool(google_search=types.GoogleSearch()),
]
llm_grounding = llm_base.bind(tools=tools)

# Logic: Gemini 3 Flash
llm_flash = ChatGoogleGenerativeAI(model=MODEL_FLASH, google_api_key=GOOGLE_API_KEY)


def init_gee():
    """Initializes Google Earth Engine using a Service Account.

    Checks multiple locations for credentials:
    1. /app/earth-engine.json (Cloud Run secret mount)
    2. ./earth-engine.json (local development)
    3. GEE_CREDENTIALS_JSON env var (inline JSON for CI/CD)
    """
    import os

    # Possible credential file locations
    credential_paths = [
        "/app/earth-engine.json",  # Cloud Run secret mount
        "earth-engine.json",  # Local development
        Path(__file__).parent.parent.parent.parent / "earth-engine.json",  # Project root
    ]

    try:
        # Option 1: Check for inline JSON in environment variable
        creds_json = os.getenv("GEE_CREDENTIALS_JSON")
        if creds_json:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(creds_json)
                temp_path = f.name
            credentials = ee.ServiceAccountCredentials(service_account, temp_path)
            ee.Initialize(credentials)
            print("GEE initialized from environment variable")
            return True

        # Option 2: Check file paths
        for cred_path in credential_paths:
            cred_file = Path(cred_path)
            if cred_file.exists():
                credentials = ee.ServiceAccountCredentials(service_account, str(cred_file))
                ee.Initialize(credentials)
                print(f"GEE initialized from {cred_file}")
                return True

        print("WARNING: No GEE credentials found. Satellite analysis will fail.")
        return False

    except Exception as e:
        print(f"GEE initialization error: {e}")
        return False


# step one:
def geocoding_node(state: FarmaState) -> dict:
    """
    NLP & Spatial Extraction (The Gemini Layer).
    Parses landmark, finds coordinate, and identifies AEZ.
    Handles retries if previous coordinates were invalid.
    Includes validation for Nigeria bounds and confidence thresholds.
    """
    landmark = state.get("parsed_data", {}).get("landmark") or state.get("message")
    prev_coords = state.get("coordinates", {})
    # Check if this is a retry using retry_count in coordinates (set by satellite_analysis_node)
    is_retry = prev_coords.get("retry_count", 0) > 0

    print(f"Grounding: {landmark} {'(RETRY)' if is_retry else ''}")

    retry_instruction = ""
    if is_retry:
        retry_instruction = (
            f"\n\nCRITICAL: The previous coordinates ({prev_coords.get('lat')}, {prev_coords.get('lng')}) were identified as WATER or BARREN ground (NDVI too low).\n"
            "DO NOT return the same coordinates. Look at the surrounding area (North, South, East, West) to find the actual agricultural plot associated with this landmark."
        )

    # Enhanced prompt for better accuracy
    prompt = f"""You are a Nigerian Agricultural Geospatial Specialist with deep knowledge of Nigerian farming regions.

TASK: Locate the farm described as: '{landmark}'

CRITICAL ACCURACY REQUIREMENTS:
1. Use Google Maps to find the PRECISE coordinates. The point MUST be on agricultural land, NOT on:
   - Rivers, lakes, or water bodies
   - Main roads or highways
   - Urban buildings or settlements
   - Rocky/barren terrain

2. For Nigerian farm landmarks:
   - "Near X bridge" = Look for farmland on FLOODPLAINS near the bridge (not the bridge itself)
   - "Near X market" = Look for farmland on the OUTSKIRTS of the market area
   - "X junction" = Look for farmland ALONG THE ROAD leading from the junction
   - River names (Anambra, Niger, Benue) = Look for FADAMA farmland on river floodplains

3. Identify the Nigerian State and LGA (Local Government Area) for this location.

4. Assign confidence score honestly:
   - 0.9+ = Exact landmark found with clear agricultural context
   - 0.7-0.9 = Landmark found, agricultural area nearby
   - 0.5-0.7 = General area identified, some uncertainty
   - <0.5 = Too vague, ask clarifying question

5. Nigerian Agro-Ecological Zones (from South to North):
   - Mangrove/Coastal: Lagos, Rivers, Bayelsa coastline
   - Freshwater Swamp: Delta, Rivers inland
   - Tropical Rainforest: South-East, South-South
   - Derived Savanna: Middle Belt (Anambra, Enugu, Benue)
   - Southern Guinea: Plateau, southern Kaduna
   - Northern Guinea: Central Kaduna, Niger
   - Sudan Savanna: Kano, Katsina, Sokoto
   - Sahel Savanna: Borno, Yobe (northern parts)
{retry_instruction}

OUTPUT: Provide the precise coordinates, state, LGA (if known), AEZ, and your confidence level."""

    structured_llm = llm_grounding.with_structured_output(GeocodingResult)

    try:
        result = structured_llm.invoke(prompt)

        # Determine minimum confidence threshold
        min_confidence = MIN_CONFIDENCE_RETRY if is_retry else MIN_CONFIDENCE_FIRST_TIME

        # Validation 1: Check if result is too vague
        if result.is_vague and result.confidence < min_confidence:
            print(
                f"Vague Result (Conf: {result.confidence} < {min_confidence}): {result.clarifying_question}"
            )
            return {
                "status": "AWAITING_FARMER_RESPONSE",
                "farmer_response": result.clarifying_question or "Please provide more details about your farm location (nearest town, landmark, or market).",
            }

        # Validation 2: Check Nigeria bounds
        is_valid, bounds_msg = validate_nigeria_coordinates(result.lat, result.lng)
        if not is_valid:
            print(f"BOUNDS ERROR: {bounds_msg}")
            return {
                "status": "AWAITING_FARMER_RESPONSE",
                "farmer_response": "The location appears to be outside Nigeria. Please provide a Nigerian farm location.",
            }

        # Validation 3: Confidence too low even if not marked vague
        if result.confidence < min_confidence:
            print(f"LOW CONFIDENCE: {result.confidence} < {min_confidence}")
            # On first attempt, ask for clarification
            if not is_retry:
                return {
                    "status": "AWAITING_FARMER_RESPONSE",
                    "farmer_response": f"We found a possible location but need more details. Can you provide the nearest town or a well-known landmark near your farm?",
                }
            # On retry, proceed with warning flag

        # Fallback AEZ logic if LLM is unsure
        identified_aez = result.identified_aez
        if identified_aez not in NIGERIA_AEZ_CONFIG:
            lat = result.lat
            if lat > 12:
                identified_aez = "Sahel Savanna"
            elif lat > 11:
                identified_aez = "Sudan Savanna"
            elif lat > 9:
                identified_aez = "Northern Guinea"
            elif lat > 7:
                identified_aez = "Southern Guinea"
            elif lat > 6:
                identified_aez = "Derived Savanna"
            elif lat > 5:
                identified_aez = "Tropical Rainforest"
            elif lat > 4.5:
                identified_aez = "Freshwater Swamp"
            else:
                identified_aez = "Mangrove/Coastal"

        # Extract state and LGA info
        identified_state = getattr(result, "identified_state", "Unknown")
        identified_lga = getattr(result, "identified_lga", None)

        print(
            f"SUCCESS: ({result.lat:.4f}, {result.lng:.4f}) | State: {identified_state} | Conf: {result.confidence} | AEZ: {identified_aez} | Unc: {result.spatial_uncertainty}"
        )

        # Retrieve AEZ Config
        aez_data = NIGERIA_AEZ_CONFIG.get(
            identified_aez, NIGERIA_AEZ_CONFIG["Northern Guinea"]
        )

        # Preserve retry_count from previous coordinates if this is a retry
        retry_count = prev_coords.get("retry_count", 0)

        # Build risk flags based on confidence
        geocoding_flags = []
        if result.confidence < 0.7:
            geocoding_flags.append("LOW_GEOCODING_CONFIDENCE")
        if result.spatial_uncertainty > 6:
            geocoding_flags.append("HIGH_SPATIAL_UNCERTAINTY")

        return {
            "coordinates": {
                "lat": result.lat,
                "lng": result.lng,
                "confidence": result.confidence,
                "suggested_buffer": result.suggested_buffer,
                "retry_count": retry_count,
                "state": identified_state,
                "lga": identified_lga,
            },
            "location_query": landmark,
            "nigeria_aez_context": {
                "zone_name": identified_aez,
                "target_ndvi": aez_data["ndvi_target"],
                "seasonality": aez_data["seasonality"],
            },
            "risk_flags": geocoding_flags,  # Pass geocoding-specific flags
        }
    except Exception as e:
        print(f"Grounding Error: {e}")
        return {
            "status": "AWAITING_FARMER_RESPONSE",
            "farmer_response": "Could not locate farm. Please provide nearest town or landmark.",
        }


def get_ndvi_series(farm_area):
    """Sentinel-2: 12-month NDVI history with cloud masking."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(farm_area)
        .filterDate(start_date, end_date)
    )

    def mask_clouds(image):
        qa = image.select("QA60")
        mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
        return (
            image.updateMask(mask)
            .divide(10000)
            .addBands(image.metadata("system:time_start"))
        )

    def add_ndvi(image):
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        return image.addBands(ndvi)

    s2_clean = s2.map(mask_clouds).map(add_ndvi)

    current_ndvi = 0.0
    try:
        recent = s2_clean.filterDate(
            end_date - timedelta(days=45), end_date
        )  # Expanded window for cloud-pierce
        if recent.size().getInfo() > 0:
            current_ndvi = (
                recent.max()
                .reduceRegion(
                    reducer=ee.Reducer.percentile([90]), geometry=farm_area, scale=10
                )
                .get("NDVI")
                .getInfo()
                or 0.0
            )
    except Exception as e:
        print(f"NDVI Series Error: {e}")
        # Return 0.0 to trigger field snap logic in satellite_analysis_node

    return current_ndvi, []


def get_sar_biomass(farm_area, zone_seasonality):
    """Sentinel-1: VV/VH Ratio for biomass in cloudy zones."""
    if zone_seasonality != "Bimodal":
        return 0.0  # critical for South

    end_date = datetime.now()
    start_date = end_date - timedelta(days=60)

    try:
        s1 = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(farm_area)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .mean()
        )

        ratio = s1.select("VH").subtract(s1.select("VV")).rename("ratio")
        val = (
            ratio.reduceRegion(reducer=ee.Reducer.mean(), geometry=farm_area, scale=10)
            .get("ratio")
            .getInfo()
        )
        return val or 0.0
    except Exception as e:
        print(f"SAR Biomass Error: {e}")
        return 0.0


def get_chirps_rainfall(farm_area):
    """CHIRPS: Last 30 days rainfall.

    Returns:
        dict with 'value' (float or None) and 'error' (str or None)
        If error occurs, value will be None and error will contain the message.
        This prevents silent failures that could affect credit decisions.

    Note: CHIRPS has ~3 week data latency, so we query up to the latest available date.
    """
    try:
        # First, find the latest available CHIRPS date (accounts for 3-week latency)
        chirps_all = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterBounds(farm_area)
        latest_image = chirps_all.sort("system:time_start", False).first()
        latest_date = ee.Date(latest_image.get("system:time_start"))

        # Query 30 days back from latest available date
        start_date = latest_date.advance(-30, "day")

        chirps = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterBounds(farm_area)
            .filterDate(start_date, latest_date)
        )

        # Check if collection is empty
        collection_size = chirps.size().getInfo()
        if collection_size == 0:
            print("   \u26a0\ufe0f CHIRPS Rainfall: No data available for date range")
            return {"value": None, "error": "NO_CHIRPS_DATA_FOR_PERIOD"}

        result = chirps.sum().reduceRegion(
            reducer=ee.Reducer.mean(), geometry=farm_area, scale=5000
        ).getInfo()

        # Check if precipitation key exists
        if "precipitation" not in result:
            print("   \u26a0\ufe0f CHIRPS Rainfall Error: No precipitation band in result")
            return {"value": None, "error": "PRECIPITATION_BAND_MISSING"}

        total = result.get("precipitation")
        if total is None:
            print("   \u26a0\ufe0f CHIRPS Rainfall: Null value returned")
            return {"value": None, "error": "NULL_PRECIPITATION_VALUE"}

        return {"value": float(total), "error": None}

    except Exception as e:
        print(f"   \u26a0\ufe0f CHIRPS Rainfall Error: {e}")
        return {"value": None, "error": str(e)}


def get_z_score_stat(farm_area, current_ndvi):
    """Calculates Z-Score against 10-year historical baseline."""
    try:
        # Expand buffer for MODIS (250m resolution) to ensure pixel capture
        hist_area = farm_area.centroid().buffer(500)

        end_date = datetime.now()
        month = end_date.month
        start_year = end_date.year - 10

        modis = ee.ImageCollection("MODIS/061/MOD13Q1").filterBounds(hist_area)
        hist_col = modis.filter(
            ee.Filter.calendarRange(month, month, "month")
        ).filterDate(f"{start_year}-01-01", end_date)

        if hist_col.size().getInfo() == 0:
            print("Z-Score: No historical MODIS data found.")
            return 0.0

        # 1. Reduce the collection to a mean/stdDev image
        # This calculates pixel-wise stats across the 10-year time series
        stats_img = hist_col.select("NDVI").reduce(
            ee.Reducer.mean().combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True)
        )

        # 2. Reduce the resulting stats image to a single value for the region
        stats = stats_img.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=hist_area,
            scale=250,
        ).getInfo()

        # MODIS NDVI is scaled by 10000
        mean = (stats.get("NDVI_mean") or 0.0) / 10000.0
        std = (stats.get("NDVI_stdDev") or 0.0) / 10000.0

        if std < 0.01:  # Avoid division by near-zero
            print(
                f"   📊 Z-Score Base: Mean={mean:.2f} | Std={std:.4f} (Too low for Z)"
            )
            return 0.0

        z = (current_ndvi - mean) / std
        print(f"   📊 Z-Score Base: Mean={mean:.2f} | Std={std:.2f} | Result={z:.2f}")
        return round(z, 2)
    except Exception as e:
        print(f"   ❌ Z-Score Calculation Failed: {e}")
        return 0.0


def generate_visuals(state: FarmaState, current_ndvi, target_range, aez_name):
    """Generates Scatter Plot and Map."""
    try:
        fig, ax = plt.subplots(figsize=(6, 4))
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]

        base = 0.4
        if "Rainforest" in aez_name or "Swamp" in aez_name:
            base = 0.7
        y_vals = [base + 0.1 * np.sin(i / 12 * 2 * np.pi) for i in range(12)]

        ax.plot(
            months, y_vals, label=f"Typical {aez_name}", linestyle="--", color="gray"
        )
        curr_month_idx = (datetime.now().month - 1) % 12
        ax.scatter(
            [months[curr_month_idx]],
            [current_ndvi],
            color="red",
            label="Current Farm Status",
            zorder=5,
        )
        ax.axhspan(
            target_range[0],
            target_range[1],
            color="green",
            alpha=0.2,
            label="Healthy Target",
        )

        ax.set_title(f"Farm Performance: {aez_name} Zone")
        ax.set_ylim(0, 1.0)
        ax.legend()

        plot_path = Path("tmp_audio") / "valuation_report.png"
        plt.savefig(plot_path)
        plt.close(fig)
        return str(plot_path)
    except Exception as e:
        print(f"⚠️ Visualization Failed: {e}")
        return None


def find_best_vegetation(farm_area, ndvi_img):
    """
    Ag-Tech 'Field Snap' Logic:
    If the pin is on a building/road, find the nearest agricultural area in the buffer.

    IMPORTANT: We use MEDIAN NDVI of vegetated pixels, not MAX.
    Using max creates positive bias (approving based on best pixel, not farm average).

    Returns the new coordinates (centroid of vegetated area) and median NDVI.
    """
    try:
        # Sample points in the buffer
        sample_points = ee.FeatureCollection.randomPoints(farm_area, 50)
        samples = ndvi_img.sampleRegions(
            collection=sample_points, scale=10, geometries=True
        )

        # Filter to vegetated pixels only (NDVI > 0.15)
        vegetated = samples.filter(ee.Filter.gt("NDVI", 0.15))
        veg_count = vegetated.size().getInfo()

        if veg_count == 0:
            return None, 0.0

        # Get statistics of vegetated pixels
        stats = vegetated.aggregate_stats("NDVI").getInfo()
        median_ndvi = stats.get("mean", 0.0)  # Use mean as proxy for median

        # Find a representative point (one closest to median NDVI)
        # Instead of picking the MAX, pick one near the median
        samples_list = vegetated.toList(50).getInfo()

        # Find sample closest to median
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

        print(f"   Field snap stats: {veg_count} vegetated pixels, median NDVI: {median_ndvi:.2f}")

        return {"lat": new_coords[1], "lng": new_coords[0]}, representative_ndvi
    except Exception as e:
        print(f"   \u26a0\ufe0f Field Snap Failed: {e}")
        return None, 0.0


# TODO: REFACTOR INTO MODULAR NODES


def satellite_analysis_node(state: FarmaState) -> dict:
    """
    Module B, C, D: The Multi-Sensor Pipeline & Underwriter Prep.
    Includes Ag-Tech 'Field Snap' to handle imprecise facility geocoding.
    """
    coords = state.get("coordinates")
    aez_context = state.get("nigeria_aez_context", {})

    if not coords or not init_gee():
        return {"risk_flags": ["SYSTEM_ERROR"], "climate_score": 0.0}

    print(f"--- NIGERIA AGRO-SCORER: {aez_context.get('zone_name')} ---")

    try:
        point = ee.Geometry.Point([coords["lng"], coords["lat"]])
        farm_area = point.buffer(
            coords.get("suggested_buffer", 200)
        )  # Increased default buffer for facility search

        # 1. Initial Biomass Check
        current_ndvi, _ = get_ndvi_series(farm_area)

        # a check to see if the first location geocoding process failed and returned ndvi of 0.00
        risk_flags = state.get("risk_flags", [])
        snapped_coords = None

        if current_ndvi < 0.10:
            print(
                f"Failed to get correct coords for farm: (NDVI={current_ndvi:.2f}): Likely a building or road. Searching nearby fields..."
            )

            # Get the NDVI image for sampling
            end_date = datetime.now()
            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(farm_area)
                .filterDate(end_date - timedelta(days=60), end_date)
            )
            if s2.size().getInfo() > 0:
                ndvi_img = s2.median().normalizedDifference(["B8", "B4"]).rename("NDVI")
                snapped_coords, snapped_ndvi = find_best_vegetation(farm_area, ndvi_img)

                if snapped_coords and snapped_ndvi > 0.15:
                    print(
                        f"Success: Moved to ({snapped_coords['lat']:.4f}, {snapped_coords['lng']:.4f}) | New NDVI: {snapped_ndvi:.2f}"
                    )
                    coords = {**coords, **snapped_coords}
                    current_ndvi = snapped_ndvi
                    # Re-calculate area at new center
                    point = ee.Geometry.Point([coords["lng"], coords["lat"]])
                    farm_area = point.buffer(100)  # Tighten buffer once snapped
                else:
                    # Check if we've already retried geocoding (tracked in coordinates)
                    retry_count = coords.get("retry_count", 0)
                    if retry_count < 1:
                        # First failure: trigger geocoding retry
                        risk_flags.append("LOCATION_REVIEW_REQUIRED")
                        coords["retry_count"] = retry_count + 1
                        print("No vegetation found. Requesting geocoding retry...")
                    else:
                        # Already retried: mark as ghost farm
                        risk_flags.append("GHOST_FARM_DETECTED")
                        print(
                            "Failed: No vegetation found after retry. Ghost farm detected."
                        )

        # Get rainfall with proper error handling
        rainfall_result = get_chirps_rainfall(farm_area)
        rainfall_30d = rainfall_result.get("value")
        rainfall_error = rainfall_result.get("error")

        sar_biomass = get_sar_biomass(farm_area, aez_context.get("seasonality"))
        z_score = get_z_score_stat(farm_area, current_ndvi)

        targets = aez_context.get("target_ndvi", (0.0, 1.0))
        phenology_flag = "NORMAL"
        if current_ndvi < targets[0]:
            phenology_flag = "BELOW_TARGET"

        # Build data quality tracking
        data_quality = {
            "ndvi_available": current_ndvi is not None and current_ndvi > 0,
            "rainfall_available": rainfall_30d is not None,
            "zscore_available": z_score is not None and z_score != 0.0,
            "ndvi_error": None,
            "rainfall_error": rainfall_error,
            "zscore_error": None if z_score != 0.0 else "INSUFFICIENT_HISTORICAL_DATA",
        }

        # If rainfall data missing, flag it but don't use default 0.0
        if rainfall_error:
            risk_flags.append("RAINFALL_DATA_INCOMPLETE")

        # Display with proper None handling
        rain_display = f"{rainfall_30d:.1f}mm" if rainfall_30d is not None else "N/A"
        print(
            f"Final Signals: NDVI={current_ndvi:.2f} (Z={z_score:.2f}) | Rain={rain_display} | SAR={sar_biomass:.1f}"
        )

        plot_path = generate_visuals(
            state, current_ndvi, targets, aez_context.get("zone_name")
        )

        return {
            "coordinates": coords,  # Return updated coords if there was a retry logic to get it the actual coords
            "satellite_report": {
                "ndvi": current_ndvi,
                "z_score": z_score,
                "rainfall_30d": rainfall_30d,  # Can be None now
                "sar_biomass": sar_biomass,
                "phenology_status": phenology_flag,
                "aez_meta": aez_context,
                "data_quality": data_quality,  # New: track data completeness
            },
            "visualization_artifacts": {"scatter_plot": plot_path},
            "risk_flags": risk_flags,
            "climate_score": 0.0,
        }

    except Exception as e:
        print(f"Scorer Error: {e}")
        return {"risk_flags": ["SCORING_ERROR"]}
