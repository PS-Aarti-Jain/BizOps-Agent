import json
import logging
import os
from pathlib import Path
import sys
from typing import Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SEED_DIR
from database.connection import get_db_connection
from database.migrations import run_migrations

logger = logging.getLogger(__name__)


def seed_database(db_path: Optional[str] = None, force: bool = False):
    """
    Seeds the SQLite database with realistic Indian small-business operational data
    for Shree Ganesh Traders (Maharashtra, India).
    """
    run_migrations(db_path)
    conn = get_db_connection(db_path)

    try:
        cursor = conn.cursor()

        if force:
            logger.info("Force re-seeding: clearing existing data...")
            cursor.execute("DELETE FROM purchase_order_items;")
            cursor.execute("DELETE FROM purchase_orders;")
            cursor.execute("DELETE FROM invoices;")
            cursor.execute("DELETE FROM order_items;")
            cursor.execute("DELETE FROM orders;")
            cursor.execute("DELETE FROM supplier_products;")
            cursor.execute("DELETE FROM suppliers;")
            cursor.execute("DELETE FROM inventory;")
            cursor.execute("DELETE FROM customers;")
            cursor.execute("DELETE FROM products;")
            conn.commit()
        else:
            cursor.execute("SELECT COUNT(*) as count FROM products;")
            row = cursor.fetchone()
            if row and row["count"] > 0:
                # Check if it has Indian data
                cursor.execute("SELECT name FROM customers WHERE customer_id = 'CUST-501';")
                c1 = cursor.fetchone()
                if c1 and "Om Sai" in c1["name"]:
                    logger.info("Database already seeded with Indian dataset. Skipping.")
                    return
                else:
                    logger.info("Upgrading database to Indian small-business dataset...")
                    return seed_database(db_path, force=True)

        logger.info("Seeding realistic Indian business dataset (Shree Ganesh Traders)...")

        # 1. Products
        with open(SEED_DIR / "products.json", "r", encoding="utf-8") as f:
            products = json.load(f)
            for p in products:
                cursor.execute(
                    """
                    INSERT INTO products (
                        product_id, sku, name, category, unit_cost, selling_price,
                        unit_of_measure, hsn_code, gst_rate, reorder_threshold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                    (
                        p["product_id"],
                        p["sku"],
                        p["name"],
                        p["category"],
                        p["unit_cost"],
                        p.get("selling_price", p["unit_cost"] * 1.3),
                        p.get("unit_of_measure", "Piece"),
                        p.get("hsn_code", "8471"),
                        p.get("gst_rate", 18.0),
                        p["reorder_threshold"],
                    ),
                )

        # 2. Inventory
        with open(SEED_DIR / "inventory.json", "r", encoding="utf-8") as f:
            inventory = json.load(f)
            for i in inventory:
                cursor.execute(
                    """
                    INSERT INTO inventory (inventory_id, product_id, on_hand_qty, reserved_qty, allocated_qty, location)
                    VALUES (?, ?, ?, ?, ?, ?);
                """,
                    (
                        i["inventory_id"],
                        i["product_id"],
                        i["on_hand_qty"],
                        i["reserved_qty"],
                        i["allocated_qty"],
                        i["location"],
                    ),
                )

        # 3. Customers
        with open(SEED_DIR / "customers.json", "r", encoding="utf-8") as f:
            customers = json.load(f)
            for c in customers:
                cursor.execute(
                    """
                    INSERT INTO customers (
                        customer_id, name, email, phone, city, state, pincode,
                        gstin, credit_limit, payment_terms, category, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                    (
                        c["customer_id"],
                        c["name"],
                        c["email"],
                        c.get("phone", "+91-98200-11223"),
                        c.get("city", "Mumbai"),
                        c.get("state", "Maharashtra"),
                        c.get("pincode", "400001"),
                        c.get("gstin", "27AAAAA0000A1Z5"),
                        c["credit_limit"],
                        c.get("payment_terms", "Net 30"),
                        c.get("category", "Wholesale"),
                        c["status"],
                    ),
                )

        # 4. Orders & Order Items
        with open(SEED_DIR / "orders.json", "r", encoding="utf-8") as f:
            orders = json.load(f)
            for o in orders:
                cursor.execute(
                    """
                    INSERT INTO orders (order_id, customer_id, order_date, status, total_amount, shipping_address)
                    VALUES (?, ?, ?, ?, ?, ?);
                """,
                    (
                        o["order_id"],
                        o["customer_id"],
                        o["order_date"],
                        o["status"],
                        o["total_amount"],
                        o["shipping_address"],
                    ),
                )
                for item in o.get("items", []):
                    cursor.execute(
                        """
                        INSERT INTO order_items (item_id, order_id, product_id, quantity, unit_price)
                        VALUES (?, ?, ?, ?, ?);
                    """,
                        (
                            item["item_id"],
                            o["order_id"],
                            item["product_id"],
                            item["quantity"],
                            item["unit_price"],
                        ),
                    )

        # 5. Invoices
        with open(SEED_DIR / "invoices.json", "r", encoding="utf-8") as f:
            invoices = json.load(f)
            for inv in invoices:
                cursor.execute(
                    """
                    INSERT INTO invoices (invoice_id, order_id, customer_id, invoice_date, due_date, total_amount, paid_amount, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                    (
                        inv["invoice_id"],
                        inv["order_id"],
                        inv["customer_id"],
                        inv["invoice_date"],
                        inv["due_date"],
                        inv["total_amount"],
                        inv["paid_amount"],
                        inv["status"],
                    ),
                )

        # 6. Suppliers & Supplier Products
        with open(SEED_DIR / "suppliers.json", "r", encoding="utf-8") as f:
            suppliers = json.load(f)
            for s in suppliers:
                cursor.execute(
                    """
                    INSERT INTO suppliers (
                        supplier_id, name, contact_email, city, state, gstin, payment_terms, rating, reliability_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                    (
                        s["supplier_id"],
                        s["name"],
                        s["contact_email"],
                        s.get("city", "Mumbai"),
                        s.get("state", "Maharashtra"),
                        s.get("gstin", "27AABCM1010A1Z5"),
                        s.get("payment_terms", "Net 30"),
                        s["rating"],
                        s["reliability_score"],
                    ),
                )
                for sp in s.get("products", []):
                    cursor.execute(
                        """
                        INSERT INTO supplier_products (id, supplier_id, product_id, unit_price, available_qty, lead_time_days, min_order_qty)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                        (
                            sp["id"],
                            s["supplier_id"],
                            sp["product_id"],
                            sp["unit_price"],
                            sp["available_qty"],
                            sp["lead_time_days"],
                            sp["min_order_qty"],
                        ),
                    )

        # 7. Purchase Orders & PO Items (Historical Purchase Spend)
        po_file = SEED_DIR / "purchase_orders.json"
        if po_file.exists():
            with open(po_file, "r", encoding="utf-8") as f:
                pos = json.load(f)
                for po in pos:
                    cursor.execute(
                        """
                        INSERT INTO purchase_orders (
                            po_id, supplier_id, status, total_amount, created_by, idempotency_key, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                        (
                            po["po_id"],
                            po["supplier_id"],
                            po["status"],
                            po["total_amount"],
                            po["created_by"],
                            po["idempotency_key"],
                            po.get("created_at", "2026-09-01 10:00:00"),
                        ),
                    )
                    for poi in po.get("items", []):
                        cursor.execute(
                            """
                            INSERT INTO purchase_order_items (item_id, po_id, product_id, quantity, unit_price, subtotal)
                            VALUES (?, ?, ?, ?, ?, ?);
                        """,
                            (
                                poi["item_id"],
                                po["po_id"],
                                poi["product_id"],
                                poi["quantity"],
                                poi["unit_price"],
                                poi["subtotal"],
                            ),
                        )

        conn.commit()
        logger.info("Database successfully seeded with realistic Indian business data!")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_database(force=True)
