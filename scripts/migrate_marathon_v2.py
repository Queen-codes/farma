"""Add Marathon v2 columns to existing aegis_marathon_days table.

Run: python scripts/migrate_marathon_v2.py

Safe to run multiple times — uses IF NOT EXISTS / catches duplicates.
"""
import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.aegis.db.connection import engine


ALTER_STATEMENTS = [
    "ALTER TABLE aegis_marathon_days ADD COLUMN IF NOT EXISTS actions_taken JSONB",
    "ALTER TABLE aegis_marathon_days ADD COLUMN IF NOT EXISTS simulation_triggered VARCHAR(50)",
    "ALTER TABLE aegis_marathon_days ADD COLUMN IF NOT EXISTS report_triggered VARCHAR(50)",
]


async def main() -> None:
    """Apply Marathon v2 additive columns to `aegis_marathon_days`.

    Returns:
        None.

    Raises:
        Exception: Propagates unexpected DB/engine failures not handled inside
        per-statement loop.
    """
    print("Migrating aegis_marathon_days for Marathon v2...")
    async with engine.begin() as conn:
        for stmt in ALTER_STATEMENTS:
            try:
                await conn.execute(__import__("sqlalchemy").text(stmt))
                col = stmt.split("ADD COLUMN")[-1].strip().split()[2]
                print(f"  + {col}")
            except Exception as e:
                if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                    print(f"  (already exists, skipping)")
                else:
                    print(f"  ERROR: {e}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
