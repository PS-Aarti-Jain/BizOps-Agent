import logging
from pathlib import Path
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2


def run_migrations(db_path=None):
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        version = cursor.fetchone()["user_version"]

        if version < 1:
            logger.info("Initializing base schema version 1...")
            schema_file = Path(__file__).parent / "schema.sql"
            with open(schema_file, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            cursor.executescript(schema_sql)
            cursor.execute("PRAGMA user_version = 2;")
            conn.commit()
            logger.info("Schema initialized.")
            return

        if version < 2:
            logger.info("Upgrading schema to version 2 (Indian business columns)...")
            # Safe column additions
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN selling_price REAL NOT NULL DEFAULT 0.0;")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN unit_of_measure TEXT NOT NULL DEFAULT 'Units';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN hsn_code TEXT NOT NULL DEFAULT '8471';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN gst_rate REAL NOT NULL DEFAULT 18.0;")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN phone TEXT DEFAULT '+91-98200-11223';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN city TEXT DEFAULT 'Mumbai';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN state TEXT DEFAULT 'Maharashtra';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN pincode TEXT DEFAULT '400001';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN gstin TEXT DEFAULT '27AAAAA0000A1Z5';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN payment_terms TEXT DEFAULT 'Net 30';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE customers ADD COLUMN category TEXT DEFAULT 'Wholesale';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE suppliers ADD COLUMN city TEXT DEFAULT 'Mumbai';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE suppliers ADD COLUMN state TEXT DEFAULT 'Maharashtra';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE suppliers ADD COLUMN gstin TEXT DEFAULT '27AABCM1234F1Z8';")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE suppliers ADD COLUMN payment_terms TEXT DEFAULT 'Net 30';")
            except Exception:
                pass

            cursor.execute("PRAGMA user_version = 2;")
            conn.commit()
            logger.info("Schema successfully upgraded to version 2.")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migrations()
