-- =====================================================
--  REVERSE AUCTION PLATFORM - FULL SCHEMA (Item-based)
--  Compatible with Neon PostgreSQL + Streamlit Frontend
-- =====================================================

SET search_path TO public;

-- =====================================================
--  1️⃣ USERS TABLE
-- =====================================================
-- Stores both buyers (can create auctions) and suppliers (can bid)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT CHECK (role IN ('buyer', 'supplier')) NOT NULL,
    company_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
--  2️⃣ AUCTIONS TABLE
-- =====================================================
-- Master table for each auction event
CREATE TABLE IF NOT EXISTS auctions (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    currency TEXT DEFAULT 'INR',
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    status TEXT DEFAULT 'scheduled',  -- scheduled | live | closed
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
--  3️⃣ AUCTION ITEMS TABLE
-- =====================================================
-- Each auction can have multiple items / line entries
CREATE TABLE IF NOT EXISTS auction_items (
    id SERIAL PRIMARY KEY,
    auction_id INT REFERENCES auctions(id) ON DELETE CASCADE,
    item_name TEXT NOT NULL,
    description TEXT,
    quantity NUMERIC(12,2) NOT NULL,
    uom TEXT DEFAULT 'Nos',
    base_price NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
--  4️⃣ BIDS TABLE
-- =====================================================
-- Each supplier bids on individual auction items
CREATE TABLE IF NOT EXISTS bids (
    id SERIAL PRIMARY KEY,
    auction_id INT REFERENCES auctions(id) ON DELETE CASCADE,
    item_id INT REFERENCES auction_items(id) ON DELETE CASCADE,
    bidder_id INT REFERENCES users(id) ON DELETE CASCADE,
    bid_amount NUMERIC(12,2) NOT NULL,
    bid_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (auction_id, item_id, bidder_id, bid_time)
);

-- =====================================================
--  5️⃣ AUDIT LOG (optional)
-- =====================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    action TEXT,
    target_type TEXT,
    target_id INT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details JSONB
);

-- =====================================================
--  6️⃣ VIEWS
-- =====================================================
-- Lowest bid per item
CREATE OR REPLACE VIEW v_lowest_bids_per_item AS
SELECT
    b.item_id,
    MIN(b.bid_amount) AS lowest_bid
FROM bids b
GROUP BY b.item_id;

-- Aggregated auction summary (lowest total cost per supplier)
CREATE OR REPLACE VIEW v_auction_summary AS
SELECT
    a.id AS auction_id,
    a.title,
    u.company_name AS buyer,
    COUNT(DISTINCT ai.id) AS total_items,
    COUNT(DISTINCT b.bidder_id) AS total_bidders
FROM auctions a
LEFT JOIN auction_items ai ON a.id = ai.auction_id
LEFT JOIN bids b ON a.id = b.auction_id
LEFT JOIN users u ON a.created_by = u.id
GROUP BY a.id, a.title, u.company_name;

-- =====================================================
--  7️⃣ SEED DATA (optional demo)
-- =====================================================

-- Insert buyer and suppliers
INSERT INTO users (name, email, password, role, company_name)
VALUES
('Procurement Team', 'buyer@example.com', 'buyer123', 'buyer', 'Host Company'),
('Vendor A', 'vendorA@example.com', 'vendor123', 'supplier', 'Vendor A Pvt Ltd'),
('Vendor B', 'vendorB@example.com', 'vendor123', 'supplier', 'Vendor B Pvt Ltd')
ON CONFLICT (email) DO NOTHING;

-- Insert sample auction
INSERT INTO auctions (title, description, currency, status, created_by)
VALUES
('Solar Power Project - Cable Procurement',
 'Reverse auction for solar DC cable supply',
 'INR', 'live',
 (SELECT id FROM users WHERE email='buyer@example.com'))
ON CONFLICT DO NOTHING;

-- Insert sample auction items
INSERT INTO auction_items (auction_id, item_name, description, quantity, uom, base_price)
VALUES
(1, 'Solar DC Cable 4 sq.mm', 'Tinned copper conductor cable 1.1kV', 1000, 'm', 95.00),
(1, 'Solar DC Cable 6 sq.mm', 'UV resistant, multi-strand copper cable', 800, 'm', 115.00),
(1, 'Lug Connector', 'Copper cable lugs crimp type', 500, 'Nos', 25.00)
ON CONFLICT DO NOTHING;

-- Insert sample bids
INSERT INTO bids (auction_id, item_id, bidder_id, bid_amount)
VALUES
(1, 1, (SELECT id FROM users WHERE email='vendorA@example.com'), 93.00),
(1, 1, (SELECT id FROM users WHERE email='vendorB@example.com'), 90.50),
(1, 2, (SELECT id FROM users WHERE email='vendorA@example.com'), 110.00),
(1, 2, (SELECT id FROM users WHERE email='vendorB@example.com'), 108.75),
(1, 3, (SELECT id FROM users WHERE email='vendorA@example.com'), 23.50),
(1, 3, (SELECT id FROM users WHERE email='vendorB@example.com'), 24.00)
ON CONFLICT DO NOTHING;

-- =====================================================
--  8️⃣ VALIDATION CHECKS
-- =====================================================
-- SELECT * FROM users;
-- SELECT * FROM auctions;
-- SELECT * FROM auction_items;
-- SELECT * FROM bids;
-- SELECT * FROM v_lowest_bids_per_item;
-- SELECT * FROM v_auction_summary;
