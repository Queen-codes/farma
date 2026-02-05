"""Database Models - PostgreSQL schema for raw intelligence data.

These models store raw data from the google search

"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from .connection import Base


class AegisScan(Base):
    """A single AEGIS data collection scan run."""

    __tablename__ = "aegis_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # running, completed, failed, skipped

    # Summary counts: aggregation
    states_scanned: Mapped[int] = mapped_column(Integer, default=0)
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    total_fatalities: Mapped[int] = mapped_column(Integer, default=0)

    # Deterministic synthesis rollup JSON (persisted output of synthesis stage)
    rollup_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    rollup_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationship to state results
    state_results: Mapped[List["StateIntelligence"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )


class StateIntelligence(Base):
    """Raw intelligence data collected for a single state in a scan."""

    __tablename__ = "aegis_state_intelligence"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("aegis_scans.id"), index=True)
    state_name: Mapped[str] = mapped_column(String(50), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # raw data which is stored as JSONB for flexibility of storing output of ai tools

    # Conflict tool raw output
    conflict_raw: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Displacement tool raw output
    displacement_raw: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Food security tool raw output
    food_security_raw: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Economic tool raw output
    economic_raw: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Deterministic synthesis assessment JSON (persisted output of synthesis stage)
    assessment_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    synthesized_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synthesis_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # facts extracted for querying  from the data,

    # From conflict data
    conflict_events_count: Mapped[int] = mapped_column(Integer, default=0)

    # From displacement data
    idp_estimate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    idp_trend: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )  # increasing, stable, decreasing, unknown

    # From food security data
    food_insecurity_level: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )  # minimal, stressed, crisis, emergency, famine, unknown
    ipc_phase: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5

    # From economic data
    markets_operational: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )  # fully, partially, closed, unknown

    # Relationship
    scan: Mapped["AegisScan"] = relationship(back_populates="state_results")
    conflict_events: Mapped[List["ConflictEvent"]] = relationship(
        back_populates="state_intel", cascade="all, delete-orphan"
    )


class ConflictEvent(Base):
    """Individual conflict events - factual data for audit and detailed queries."""

    __tablename__ = "aegis_conflict_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    state_intel_id: Mapped[int] = mapped_column(
        ForeignKey("aegis_state_intelligence.id"), index=True
    )

    # Event details
    event_date: Mapped[str] = mapped_column(String(20))
    location: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(50), index=True)
    lga: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    latitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    event_type: Mapped[str] = mapped_column(String(50))
    actors: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Casualties
    fatalities: Mapped[int] = mapped_column(Integer, default=0)
    injuries: Mapped[int] = mapped_column(Integer, default=0)
    abducted: Mapped[int] = mapped_column(Integer, default=0)

    # Description and source
    summary: Mapped[str] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Metadata
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationship
    state_intel: Mapped["StateIntelligence"] = relationship(
        back_populates="conflict_events"
    )


class LGARiskScore(Base):
    __tablename__ = "aegis_lga_risk_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("aegis_scans.id"), index=True)
    lga: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(50), index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    fatalities: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AegisReport(Base):
    """A generated PDF report tied to a scan."""

    __tablename__ = "aegis_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("aegis_scans.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    states: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    include_infographics: Mapped[bool] = mapped_column(default=True)
    include_annexes: Mapped[bool] = mapped_column(default=True)

    pdf_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    gcs_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="running")  # running/completed/failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
