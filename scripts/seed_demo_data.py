"""Demo Data Seeder for FARMA.

Seeds sample farmer profiles for demo and QA walkthroughs.
Run with: python scripts/seed_demo_data.py
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is importable when running this file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Return current UTC timestamp as naive datetime for DB fields."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def seed_demo_farmers() -> None:
    """Insert deterministic demo `FarmerProfile` rows when missing.

    Returns:
        None.

    Raises:
        Exception: Propagates DB/session failures.

    Side Effects:
        Initializes DB schema (via `init_db`) and writes profile rows.
    """
    from app.aegis.db.connection import async_session, init_db
    from app.aegis.db.models import FarmerProfile
    
    logger.info("[Seed] Initializing database...")
    await init_db()
    
    # Demo farmers with diverse profiles
    demo_farmers = [
        {
            "phone": "+2348012345001",
            "primary_crops": ["maize", "rice"],
            "last_known_location": "near Maiduguri Central Market",
            "state": "Borno",
            "lga": "Maiduguri",
            "total_loans_requested": 3,
            "total_loans_approved": 2,
            "total_loans_rejected": 1,
            "last_loan_amount": 75000.0,
            "last_loan_decision": "APPROVED",
            "total_disease_reports": 1,
            "last_disease_reported": "Maize Streak Virus",
            "language_preference": "hausa",
            "context_summary": "Returning farmer from Maiduguri, Borno. Grows maize and rice. 2/3 loans approved. Previously reported Maize Streak Virus.",
        },
        {
            "phone": "+2348012345002", 
            "primary_crops": ["cassava", "yam"],
            "last_known_location": "Yola town center",
            "state": "Adamawa",
            "lga": "Yola South",
            "total_loans_requested": 1,
            "total_loans_approved": 1,
            "total_loans_rejected": 0,
            "last_loan_amount": 50000.0,
            "last_loan_decision": "APPROVED",
            "total_disease_reports": 0,
            "language_preference": "english",
            "context_summary": "Farmer from Yola South, Adamawa. Grows cassava and yam. 1 loan approved. First-time loan recipient.",
        },
        {
            "phone": "+2348012345003",
            "primary_crops": ["sorghum", "millet", "groundnut"],
            "last_known_location": "near Damaturu market",
            "state": "Yobe",
            "lga": "Damaturu",
            "total_loans_requested": 5,
            "total_loans_approved": 3,
            "total_loans_rejected": 2,
            "last_loan_amount": 120000.0,
            "last_loan_decision": "HELD",
            "total_disease_reports": 2,
            "last_disease_reported": "Sorghum Anthracnose",
            "language_preference": "hausa",
            "context_summary": "Experienced farmer from Damaturu, Yobe. Grows sorghum, millet, groundnut. 3/5 loans approved. Multiple disease reports. Loan currently held for review.",
        },
        {
            "phone": "+2347098765001",
            "primary_crops": ["tomato"],
            "last_known_location": "Kaduna central",
            "state": "Kaduna",
            "lga": "Kaduna North",
            "total_loans_requested": 0,
            "total_loans_approved": 0,
            "total_loans_rejected": 0,
            "total_disease_reports": 3,
            "last_disease_reported": "Tomato Yellow Leaf Curl",
            "language_preference": "pidgin",
            "context_summary": "Tomato farmer from Kaduna North. Has not requested loans but reported 3 diseases. Prefers Pidgin.",
        },
    ]
    
    async with async_session() as session:
        for farmer_data in demo_farmers:
            # Check if already exists
            from sqlalchemy import select
            result = await session.execute(
                select(FarmerProfile).where(FarmerProfile.phone == farmer_data["phone"])
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                logger.info(
                    "[Seed] Farmer %s already exists, skipping", farmer_data["phone"]
                )
                continue
            
            # Create profile
            farmer = FarmerProfile(
                phone=farmer_data["phone"],
                first_seen_at=_utcnow_naive() - timedelta(days=30),
                last_seen_at=_utcnow_naive() - timedelta(days=1),
                primary_crops=farmer_data.get("primary_crops"),
                last_known_location=farmer_data.get("last_known_location"),
                state=farmer_data.get("state"),
                lga=farmer_data.get("lga"),
                total_loans_requested=farmer_data.get("total_loans_requested", 0),
                total_loans_approved=farmer_data.get("total_loans_approved", 0),
                total_loans_rejected=farmer_data.get("total_loans_rejected", 0),
                last_loan_amount=farmer_data.get("last_loan_amount"),
                last_loan_decision=farmer_data.get("last_loan_decision"),
                total_disease_reports=farmer_data.get("total_disease_reports", 0),
                last_disease_reported=farmer_data.get("last_disease_reported"),
                context_summary=farmer_data.get("context_summary"),
                interaction_count=farmer_data.get("total_loans_requested", 0) + farmer_data.get("total_disease_reports", 0),
                language_preference=farmer_data.get("language_preference"),
            )
            session.add(farmer)
            logger.info(
                "[Seed] Created farmer: %s (%s)",
                farmer_data["phone"],
                farmer_data.get("state"),
            )
        
        await session.commit()
        logger.info("[Seed] Committed %s demo farmers", len(demo_farmers))


async def main() -> None:
    """Run demo farmer seeding and print concise summary logs.

    Returns:
        None.
    """
    logger.info("=" * 50)
    logger.info("FARMA Demo Data Seeder")
    logger.info("=" * 50)
    
    await seed_demo_farmers()
    
    logger.info("\n[Seed] Demo data seeding complete!")
    logger.info("Test numbers:")
    logger.info("  +2348012345001 - Borno maize farmer (2 loans approved)")
    logger.info("  +2348012345002 - Adamawa cassava farmer (1 loan)")
    logger.info("  +2348012345003 - Yobe sorghum farmer (experienced)")
    logger.info("  +2347098765001 - Kaduna tomato farmer (disease focus)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
