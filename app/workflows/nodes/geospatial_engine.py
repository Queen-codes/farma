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
    is_vague: bool = Field(
        description="True if the landmark could not be precisely located"
    )
    clarifying_question: str | None = Field(
        description="Question to ask if location is vague"
    )


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
    """Initializes Google Earth Engine using a Service Account."""
    try:
        credentials = ee.ServiceAccountCredentials(service_account, "earth-engine.json")
        ee.Initialize(credentials)
        return True
    except Exception as e:
        return False


# step one:
def geocoding_node(state: FarmaState) -> dict:
    """
    NLP & Spatial Extraction (The Gemini Layer).
    Parses landmark, finds coordinate, and identifies AEZ.
    Handles retries if previous coordinates were invalid.
    """
    landmark = state.get("parsed_data", {}).get("landmark") or state.get("message")
    risk_flags = state.get("risk_flags", [])
    is_retry = "LOCATION_REVIEW_REQUIRED" in risk_flags

    print(f"Grounding: {landmark} {'(RETRY)' if is_retry else ''}")

    retry_instruction = ""
    if is_retry:
        prev_coords = state.get("coordinates", {})
        retry_instruction = (
            f"\n\nCRITICAL: The previous coordinates ({prev_coords.get('lat')}, {prev_coords.get('lng')}) were identified as WATER or BARREN ground (NDVI too low).\n"
            "DO NOT return the same coordinates. Look at the surrounding area (North, South, East, West) to find the actual agricultural plot associated with this landmark."
        )

    prompt = (
        f"You are a Nigerian Geospatial Specialist. Locate: '{landmark}'.\n"
        "1. Use Google Maps to find the precise anchor coordinate. Be extremely precise; check if the point is in a river or on a road.\n"
        "2. Identify the Agro-Ecological Zone (AEZ) based on the location.\n"
        "3. Assign 'spatial_uncertainty' (1-10) and 'suggested_buffer' (meters).\n"
        "4. If strictly impossible, ask a clarifying question."
        f"{retry_instruction}"
    )

    structured_llm = llm_grounding.with_structured_output(GeocodingResult)

    try:
        result = structured_llm.invoke(prompt)

        if result.is_vague and result.confidence < 0.4:
            print(
                f"Vague Result (Conf: {result.confidence}): {result.clarifying_question}"
            )
            return {
                "status": "AWAITING_FARMER_RESPONSE",
                "farmer_response": result.clarifying_question,
            }

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

        print(
            f"SUCCESS: ({result.lat}, {result.lng}) | Conf: {result.confidence} | AEZ: {identified_aez} | Unc: {result.spatial_uncertainty}"
        )

        # Retrieve AEZ Config
        aez_data = NIGERIA_AEZ_CONFIG.get(
            identified_aez, NIGERIA_AEZ_CONFIG["Northern Guinea"]
        )

        # Clear the flag if we found a new point
        new_flags = [f for f in risk_flags if f != "LOCATION_REVIEW_REQUIRED"]

        return {
            "coordinates": {
                "lat": result.lat,
                "lng": result.lng,
                "confidence": result.confidence,
                "suggested_buffer": result.suggested_buffer,
            },
            "location_query": landmark,
            "nigeria_aez_context": {
                "zone_name": identified_aez,
                "target_ndvi": aez_data["ndvi_target"],
                "seasonality": aez_data["seasonality"],
            },
            "risk_flags": new_flags,
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
    except:
        pass

    return current_ndvi, []


def get_sar_biomass(farm_area, zone_seasonality):
    """Sentinel-1: VV/VH Ratio for biomass in cloudy zones."""
    if zone_seasonality != "Bimodal":
        return 0.0  # Only critical for South

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
    except:
        return 0.0


def get_chirps_rainfall(farm_area):
    """CHIRPS: Last 30 days rainfall."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    try:
        chirps = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterBounds(farm_area)
            .filterDate(start_date, end_date)
        )
        total = (
            chirps.sum()
            .reduceRegion(reducer=ee.Reducer.mean(), geometry=farm_area, scale=5000)
            .get("precipitation")
            .getInfo()
        )
        return total or 0.0
    except:
        return 0.0


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
    If the pin is on a building/road, find the nearest greenest pixel in the buffer.
    Returns the new coordinates and the max NDVI found.
    """
    try:
        # Sample 50 random points in the buffer and find the one with highest NDVI
        # This is a robust way to 'snap' from a road/building to a nearby field
        sample_points = ee.FeatureCollection.randomPoints(farm_area, 50)
        samples = ndvi_img.sampleRegions(
            collection=sample_points, scale=10, geometries=True
        )

        # Sort by NDVI descending
        best_sample = samples.sort("NDVI", False).first().getInfo()

        if not best_sample:
            return None, 0.0

        new_coords = best_sample["geometry"]["coordinates"]  # [lng, lat]
        max_ndvi = best_sample["properties"]["NDVI"]

        return {"lat": new_coords[1], "lng": new_coords[0]}, max_ndvi
    except Exception as e:
        print(f"   ⚠️ Field Snap Failed: {e}")
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
                    risk_flags.append("GHOST_FARM_DETECTED")
                    print("Failed: No vegetation found in vicinity.")

        rainfall_30d = get_chirps_rainfall(farm_area)
        sar_biomass = get_sar_biomass(farm_area, aez_context.get("seasonality"))
        z_score = get_z_score_stat(farm_area, current_ndvi)

        targets = aez_context.get("target_ndvi", (0.0, 1.0))
        phenology_flag = "NORMAL"
        if current_ndvi < targets[0]:
            phenology_flag = "BELOW_TARGET"

        print(
            f"Final Signals: NDVI={current_ndvi:.2f} (Z={z_score:.2f}) | Rain={rainfall_30d:.1f}mm | SAR={sar_biomass:.1f}"
        )

        plot_path = generate_visuals(
            state, current_ndvi, targets, aez_context.get("zone_name")
        )

        return {
            "coordinates": coords,  # Return updated coords if there was a retry logic to get it the actual coords
            "satellite_report": {
                "ndvi": current_ndvi,
                "z_score": z_score,
                "rainfall_30d": rainfall_30d,
                "sar_biomass": sar_biomass,
                "phenology_status": phenology_flag,
                "aez_meta": aez_context,
            },
            "visualization_artifacts": {"scatter_plot": plot_path},
            "risk_flags": risk_flags,
            "climate_score": 0.0,
        }

    except Exception as e:
        print(f"Scorer Error: {e}")
        return {"risk_flags": ["SCORING_ERROR"]}
