-- Enable WAL mode and foreign key constraints
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Products Table
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_cost REAL NOT NULL CHECK(unit_cost >= 0),
    selling_price REAL NOT NULL DEFAULT 0.0 CHECK(selling_price >= 0),
    unit_of_measure TEXT NOT NULL DEFAULT 'Units',
    hsn_code TEXT NOT NULL DEFAULT '8471',
    gst_rate REAL NOT NULL DEFAULT 18.0,
    reorder_threshold INTEGER NOT NULL CHECK(reorder_threshold >= 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Inventory Table
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    on_hand_qty INTEGER NOT NULL CHECK(on_hand_qty >= 0),
    reserved_qty INTEGER NOT NULL DEFAULT 0 CHECK(reserved_qty >= 0),
    allocated_qty INTEGER NOT NULL DEFAULT 0 CHECK(allocated_qty >= 0),
    location TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Customers Table
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT DEFAULT '+91-98200-11223',
    city TEXT DEFAULT 'Mumbai',
    state TEXT DEFAULT 'Maharashtra',
    pincode TEXT DEFAULT '400001',
    gstin TEXT DEFAULT '27AAAAA0000A1Z5',
    credit_limit REAL NOT NULL CHECK(credit_limit >= 0),
    payment_terms TEXT DEFAULT 'Net 30',
    category TEXT DEFAULT 'Wholesale',
    status TEXT NOT NULL CHECK(status IN ('Active', 'Suspended', 'Overdue')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Orders Table
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Pending', 'Processing', 'Shipped', 'Delivered', 'Cancelled')),
    total_amount REAL NOT NULL CHECK(total_amount >= 0),
    shipping_address TEXT NOT NULL
);

-- Order Items Table
CREATE TABLE IF NOT EXISTS order_items (
    item_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0)
);

-- Invoices Table
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    total_amount REAL NOT NULL CHECK(total_amount >= 0),
    paid_amount REAL NOT NULL DEFAULT 0 CHECK(paid_amount >= 0),
    status TEXT NOT NULL CHECK(status IN ('Unpaid', 'Partial', 'Paid', 'Overdue'))
);

-- Suppliers Table
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    city TEXT DEFAULT 'Mumbai',
    state TEXT DEFAULT 'Maharashtra',
    gstin TEXT DEFAULT '27AABCM1234F1Z8',
    payment_terms TEXT DEFAULT 'Net 30',
    rating REAL CHECK(rating BETWEEN 0 AND 5),
    reliability_score REAL CHECK(reliability_score BETWEEN 0 AND 1)
);

-- Supplier Products Table
CREATE TABLE IF NOT EXISTS supplier_products (
    id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    available_qty INTEGER NOT NULL CHECK(available_qty >= 0),
    lead_time_days INTEGER NOT NULL CHECK(lead_time_days >= 0),
    min_order_qty INTEGER NOT NULL DEFAULT 1 CHECK(min_order_qty > 0)
);

-- Purchase Orders Header Table
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    status TEXT NOT NULL CHECK(status IN ('Created', 'Approved', 'Fulfilled', 'Cancelled')),
    total_amount REAL NOT NULL CHECK(total_amount >= 0),
    created_by TEXT NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Purchase Order Items Table
CREATE TABLE IF NOT EXISTS purchase_order_items (
    item_id TEXT PRIMARY KEY,
    po_id TEXT NOT NULL REFERENCES purchase_orders(po_id) ON DELETE CASCADE,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    subtotal REAL NOT NULL CHECK(subtotal >= 0)
);

-- Append-Only Audit Log Table
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    session_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_role TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    confirmation_status TEXT NOT NULL,
    user_confirmed_at TEXT,
    execution_status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL
);

-- Database Triggers to Enforce Append-Only Audit Log
CREATE TRIGGER IF NOT EXISTS prevent_audit_log_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log entries are immutable and cannot be updated.');
END;

CREATE TRIGGER IF NOT EXISTS prevent_audit_log_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log entries are immutable and cannot be deleted.');
END;

