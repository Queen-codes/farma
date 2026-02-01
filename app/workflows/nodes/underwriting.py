"""Deterministic Underwriting Rules for FARMA Credit Scoring.

This module handles rule-based credit decisions deterministically,
reserving LLM calls only for edge cases that require reasoning.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from enum import Enum


class DecisionType(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_INSURANCE = "APPROVED_WITH_INSURANCE"
    REJECTED = "REJECTED"
    HELD = "HELD"
    REVIEW = "REVIEW"  # Edge case - needs LLM reasoning


class UnderwritingResult(BaseModel):
    """Result of deterministic underwriting evaluation."""

    decision: DecisionType
    confidence: float = Field(ge=0.0, le=1.0)
    flags: List[str] = Field(default_factory=list)
    requires_llm_review: bool = False
    explanation: str = ""


class DataQuality(BaseModel):
    """Tracks quality of satellite data for decision confidence."""

    ndvi_available: bool = True
    rainfall_available: bool = True
    zscore_available: bool = True
    ndvi_error: Optional[str] = None
    rainfall_error: Optional[str] = None
    zscore_error: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return self.ndvi_available and self.rainfall_available and self.zscore_available

    @property
    def missing_count(self) -> int:
        return sum(
            [
                not self.ndvi_available,
                not self.rainfall_available,
                not self.zscore_available,
            ]
        )


# Loan amount limits by crop type (in Naira)
LOAN_LIMITS = {
    "rice": {"min": 20000, "max": 500000},
    "maize": {"min": 15000, "max": 400000},
    "beans": {"min": 15000, "max": 300000},
    "groundnut": {"min": 15000, "max": 350000},
    "cassava": {"min": 20000, "max": 400000},
    "yam": {"min": 25000, "max": 450000},
    "sorghum": {"min": 15000, "max": 300000},
    "millet": {"min": 10000, "max": 250000},
    "default": {"min": 10000, "max": 300000},
}


def validate_loan_amount(amount: float, crop_type: str = "default") -> tuple[bool, str]:
    """Validate loan amount against limits for crop type."""
    crop_key = crop_type.lower() if crop_type else "default"
    limits = LOAN_LIMITS.get(crop_key, LOAN_LIMITS["default"])

    if amount < limits["min"]:
        return (
            False,
            f"Amount N{amount:,.0f} below minimum N{limits['min']:,.0f} for {crop_type}",
        )
    if amount > limits["max"]:
        return (
            False,
            f"Amount N{amount:,.0f} exceeds maximum N{limits['max']:,.0f} for {crop_type}",
        )
    return True, "Amount within acceptable range"


def apply_underwriting_rules(
    ndvi: float,
    z_score: float,
    rainfall_30d: float,
    target_ndvi_range: tuple[float, float],
    data_quality: DataQuality,
    aegis_risk_flags: List[str] = None,
    loan_amount: float = None,
    crop_type: str = None,
) -> UnderwritingResult:
    """
    Apply deterministic underwriting rules to satellite data.

    Decision Logic:
    1. Check Aegis risk flags first (conflict zones, food crisis)
    2. Ghost farm detection (NDVI < 0.05)
    3. Data quality check - if incomplete, route to REVIEW
    4. Crop failure detection (Z-Score < -2.0)
       - If rainfall low: Climate exception -> APPROVED_WITH_INSURANCE
       - If rainfall normal: Farmer negligence -> REJECTED
    5. Healthy farm detection (NDVI in target, Z-Score > -0.5)
    6. Edge cases -> REVIEW (needs LLM reasoning)

    Returns:
        UnderwritingResult with decision, confidence, and flags
    """
    aegis_risk_flags = aegis_risk_flags or []
    flags = []

    # Rule 0: Aegis Security/Crisis Overrides
    if "ACTIVE_CONFLICT" in aegis_risk_flags:
        return UnderwritingResult(
            decision=DecisionType.HELD,
            confidence=0.95,
            flags=["SECURITY_HOLD", "ACTIVE_CONFLICT"],
            explanation="Loan held due to active conflict in region. Safety of farmer and assets cannot be guaranteed.",
        )

    if "FOOD_CRISIS_ZONE" in aegis_risk_flags:
        flags.append("HUMANITARIAN_ZONE")
        # Don't reject outright, but flag for special handling

    if "HIGH_DISPLACEMENT" in aegis_risk_flags:
        flags.append("HIGH_IDP_AREA")

    # Rule 1: Loan Amount Validation
    if loan_amount is not None:
        valid, msg = validate_loan_amount(loan_amount, crop_type)
        if not valid:
            return UnderwritingResult(
                decision=DecisionType.REJECTED,
                confidence=0.99,
                flags=["AMOUNT_OUT_OF_RANGE"],
                explanation=msg,
            )

    # Rule 2: Ghost Farm Detection
    if ndvi < 0.05:
        return UnderwritingResult(
            decision=DecisionType.HELD,
            confidence=0.0,
            flags=["GHOST_FARM_DETECTED"],
            requires_llm_review=True,
            explanation="NDVI < 0.05 indicates no vegetation. Location may be water, rock, or incorrect coordinates.",
        )

    # Rule 3: Data Quality Check
    if not data_quality.is_complete:
        missing = []
        if not data_quality.ndvi_available:
            missing.append("NDVI")
        if not data_quality.rainfall_available:
            missing.append("Rainfall")
        if not data_quality.zscore_available:
            missing.append("Z-Score")

        if data_quality.ndvi_available and ndvi > 0.10:
            if z_score > -1.5:  # Within 1.5 std devs of historical average = NORMAL
                confidence = 0.75 if z_score > 0 else 0.65 if z_score > -0.5 else 0.55
                extra_flags = []
                if z_score > 0.5:
                    extra_flags.append("ABOVE_HISTORICAL_AVERAGE")
                elif z_score < -0.5:
                    extra_flags.append("BELOW_AVERAGE_BUT_ACCEPTABLE")

                return UnderwritingResult(
                    decision=DecisionType.APPROVED,
                    confidence=confidence,
                    flags=flags
                    + ["DATA_INCOMPLETE", f"MISSING_{'+'.join(missing)}"]
                    + extra_flags,
                    explanation=f"Approved - Z-Score {z_score:.2f} within normal seasonal range. Missing: {', '.join(missing)}",
                )

        return UnderwritingResult(
            decision=DecisionType.REVIEW,
            confidence=0.3,
            flags=flags + ["DATA_INCOMPLETE"],
            requires_llm_review=True,
            explanation=f"Insufficient data for automated decision. Missing: {', '.join(missing)}",
        )

    # Rule 4: Zero Data Trap
    if abs(ndvi) < 0.01 and abs(z_score) < 0.01:
        return UnderwritingResult(
            decision=DecisionType.REVIEW,
            confidence=0.2,
            flags=flags + ["ZERO_DATA_ANOMALY"],
            requires_llm_review=True,
            explanation="Both NDVI and Z-Score near zero indicates data quality issue, not a healthy farm.",
        )

    # Rule 5: Crop Failure Detection (Z-Score < -2.0)
    if z_score < -2.0:
        flags.append("CROP_STRESS_SEVERE")

        # Check rainfall to determine cause
        if rainfall_30d < 50:
            # Climate exception - drought caused failure
            return UnderwritingResult(
                decision=DecisionType.APPROVED_WITH_INSURANCE,
                confidence=0.75,
                flags=flags + ["CLIMATE_RISK_DETECTED", "DROUGHT_INDICATED"],
                explanation="Farm shows stress (Z-Score < -2.0) but rainfall < 50mm indicates drought. Approved with crop insurance requirement.",
            )
        elif rainfall_30d > 100:
            # Adequate rainfall but crop failed - likely negligence
            return UnderwritingResult(
                decision=DecisionType.REJECTED,
                confidence=0.80,
                flags=flags + ["FARMER_NEGLIGENCE_LIKELY"],
                explanation="Farm shows severe stress (Z-Score < -2.0) despite adequate rainfall (>100mm). Indicates poor farm management.",
            )
        else:
            # Borderline rainfall (50-100mm) - edge case
            return UnderwritingResult(
                decision=DecisionType.REVIEW,
                confidence=0.5,
                flags=flags + ["BORDERLINE_CONDITIONS"],
                requires_llm_review=True,
                explanation="Crop stress detected with borderline rainfall. Needs expert assessment.",
            )

    # Rule 6: Moderate Stress Detection (-2.0 <= Z-Score < -1.5)
    if z_score < -1.5:
        flags.append("CROP_STRESS_MODERATE")

        if ndvi >= target_ndvi_range[0]:
            # Currently healthy but trending down
            return UnderwritingResult(
                decision=DecisionType.APPROVED,
                confidence=0.70,
                flags=flags + ["MONITOR_RECOMMENDED"],
                explanation="Farm currently healthy but Z-Score indicates below-average performance. Approved with monitoring advisory.",
            )
        else:
            # Below target and trending down
            return UnderwritingResult(
                decision=DecisionType.REVIEW,
                confidence=0.45,
                flags=flags + ["BELOW_TARGET_AND_DECLINING"],
                requires_llm_review=True,
                explanation="Farm below target NDVI with negative trend. Needs risk assessment.",
            )

    # Rule 7: Healthy Farm - Clear Approval
    if ndvi >= target_ndvi_range[0] and z_score > -0.5:
        confidence = 0.90

        # Bonus confidence for above-average performance
        if z_score > 0.5:
            confidence = 0.95
            flags.append("ABOVE_AVERAGE_PERFORMANCE")
        if ndvi >= target_ndvi_range[1]:
            flags.append("EXCELLENT_VEGETATION")

        # Check for potential flood risk if rainfall very high
        if rainfall_30d > 300:
            flags.append("FLOOD_RISK_MONITOR")

        # Check for irrigation advantage
        if rainfall_30d < 30 and ndvi > target_ndvi_range[0]:
            flags.append("IRRIGATION_LIKELY")

        return UnderwritingResult(
            decision=DecisionType.APPROVED,
            confidence=confidence,
            flags=flags + ["GOOD_HISTORY"],
            explanation=f"Farm shows healthy vegetation (NDVI={ndvi:.2f}) and stable/positive trend (Z={z_score:.2f}). Good credit candidate.",
        )

    if ndvi < target_ndvi_range[0] and ndvi >= 0.10:
        if z_score > -1.5:
            # Within normal seasonal variation - approve
            confidence = 0.70 if z_score > 0 else 0.60 if z_score > -0.5 else 0.50
            return UnderwritingResult(
                decision=DecisionType.APPROVED,
                confidence=confidence,
                flags=flags + ["DRY_SEASON_ADJUSTMENT", "BELOW_TARGET_BUT_NORMAL"],
                explanation=f"NDVI {ndvi:.2f} below target but Z-Score {z_score:.2f} within normal seasonal range. Approved.",
            )
        else:
            # Z-Score < -1.5 but not catastrophic - borderline
            return UnderwritingResult(
                decision=DecisionType.REVIEW,
                confidence=0.40,
                flags=flags + ["UNDERPERFORMING"],
                requires_llm_review=True,
                explanation="Farm below seasonal expectations. Needs assessment.",
            )

    # Edge case that doesn't fit rules
    return UnderwritingResult(
        decision=DecisionType.REVIEW,
        confidence=0.35,
        flags=flags + ["EDGE_CASE"],
        requires_llm_review=True,
        explanation="Farm metrics don't clearly indicate approval or rejection. Expert review needed.",
    )
