-- Optional: clear the trade book in stock_trader_prod after seeding from a dev dump,
-- so production starts with a clean, real-money record. Keeps all OHLCV / sign /
-- benchmark data intact.
--
-- Run:  sudo -u postgres psql -d stock_trader_prod -f scripts/reset_prod_book.sql
--
-- This empties positions, reviewed candidates and memos, then creates a single
-- "Production" account. Adjust the account name/initial capital as you like.

BEGIN;

TRUNCATE TABLE positions, reviewed_candidates, memos RESTART IDENTITY CASCADE;

-- Wipe the copied dev accounts and create one fresh prod account.
-- (positions/reviewed_candidates FK -> accounts with ON DELETE SET NULL, already truncated.)
-- budget = lot-sizing budget (¥200,000 per the live-trading plan; edit to taste).
TRUNCATE TABLE accounts RESTART IDENTITY CASCADE;
INSERT INTO accounts (name, description, initial_cash, budget, archived, created_at)
VALUES ('Production', 'Live real-money account', 200000, 200000, false, now());

COMMIT;
