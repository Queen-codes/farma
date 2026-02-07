"""Initialize FARMA database schema from application models.

This script is a thin CLI wrapper around `app.aegis.db.connection.init_db`.
Use it during local setup or after dropping DB tables.
"""

import asyncio
import sys
import os

# Add the project root to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.aegis.db.connection import init_db

async def main() -> None:
    """Initialize database tables and print outcome to stdout.

    Returns:
        None.

    Raises:
        None: Exceptions are caught and printed as failure messages.
    """
    print("Initializing Database...")
    try:
        await init_db()
        print("✅ Database initialized successfully!")
    except Exception as e:
        print(f"❌ Error initializing database: {e}")

if __name__ == "__main__":
    asyncio.run(main())
