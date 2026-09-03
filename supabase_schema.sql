-- SQL script to initialize Supabase database for MeuFinanças

-- 1. Create Users Table (recommended for scalability and foreign key reference)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Create Categories Table
CREATE TABLE IF NOT EXISTS categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    icon VARCHAR(50) NOT NULL, -- emoji or icon name (e.g., "🍔")
    type INTEGER NOT NULL, -- 0 = Expense (Despesa), 1 = Income (Receita)
    is_default BOOLEAN DEFAULT FALSE NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- Ensures a user cannot have categories with duplicate names
    CONSTRAINT unique_user_category_name UNIQUE (user_id, name)
);

-- 3. Create Transactions Table
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    amount BIGINT NOT NULL, -- always positive, in cents (e.g. 1000 = R$ 10.00)
    type INTEGER NOT NULL, -- 0 = Expense (Despesa), 1 = Income (Receita)
    category_id UUID NOT NULL REFERENCES categories(id),
    description TEXT,
    date TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- 4. Create Indexes for Performance
CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category_id ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_categories_user_id ON categories(user_id);

-- 5. Seed Default User (Fixed UUID for local/development use)
INSERT INTO users (id, email)
VALUES ('00000000-0000-0000-0000-000000000001', 'fixeduser@meufinancas.com')
ON CONFLICT (id) DO NOTHING;

-- 6. Seed Default Categories for the Fixed User
-- Despesas (type = 0)
INSERT INTO categories (name, icon, type, is_default, user_id) VALUES
('Alimentação', '🍔', 0, true, '00000000-0000-0000-0000-000000000001'),
('Transporte', '🚌', 0, true, '00000000-0000-0000-0000-000000000001'),
('Moradia', '🏠', 0, true, '00000000-0000-0000-0000-000000000001'),
('Saúde', '🏥', 0, true, '00000000-0000-0000-0000-000000000001'),
('Lazer', '🎮', 0, true, '00000000-0000-0000-0000-000000000001'),
('Educação', '📚', 0, true, '00000000-0000-0000-0000-000000000001'),
('Vestuário', '👕', 0, true, '00000000-0000-0000-0000-000000000001'),
('Supermercado', '🛒', 0, true, '00000000-0000-0000-0000-000000000001'),
('Farmácia', '💊', 0, true, '00000000-0000-0000-0000-000000000001'),
('Outros', '📦', 0, true, '00000000-0000-0000-0000-000000000001')
ON CONFLICT (user_id, name) DO NOTHING;

-- Receitas (type = 1)
INSERT INTO categories (name, icon, type, is_default, user_id) VALUES
('Salário', '💰', 1, true, '00000000-0000-0000-0000-000000000001'),
('Freelance', '💻', 1, true, '00000000-0000-0000-0000-000000000001'),
('Investimentos', '📈', 1, true, '00000000-0000-0000-0000-000000000001'),
('Presentes', '🎁', 1, true, '00000000-0000-0000-0000-000000000001'),
('Outros', '📦', 1, true, '00000000-0000-0000-0000-000000000001')
ON CONFLICT (user_id, name) DO NOTHING;

-- 7. Create Budgets Table (Metas de Gastos por Grupo)
CREATE TABLE IF NOT EXISTS budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    amount BIGINT NOT NULL, -- limit in cents (e.g. 50000 = R$ 500.00)
    period VARCHAR(7) NOT NULL, -- format: YYYY-MM
    category_ids UUID[] NOT NULL, -- list of category IDs covered by this budget group
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- Ensures a user can only have one budget with the same name in a given month
    CONSTRAINT unique_name_period_user UNIQUE(name, period, user_id)
);

-- Index for performance
CREATE INDEX IF NOT EXISTS idx_budgets_user_period ON budgets(user_id, period);

-- 8. Credit cards (manual rows are preserved; Pluggy rows use source='pluggy')
CREATE TABLE IF NOT EXISTS credit_cards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    "limit" BIGINT NOT NULL,
    closing_day INTEGER NOT NULL,
    due_day INTEGER NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_provision BOOLEAN DEFAULT FALSE NOT NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20) DEFAULT 'dinheiro' NOT NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS card_id UUID REFERENCES credit_cards(id) ON DELETE SET NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS invoice_period VARCHAR(7);

-- 9. Open Finance connection and account read models
CREATE TABLE IF NOT EXISTS pluggy_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id UUID NOT NULL UNIQUE,
    connector_name VARCHAR(160) NOT NULL,
    connector_image_url TEXT,
    status VARCHAR(60) NOT NULL DEFAULT 'UNKNOWN',
    execution_status VARCHAR(100),
    last_successful_update_at TIMESTAMP WITH TIME ZONE,
    next_auto_sync_at TIMESTAMP WITH TIME ZONE,
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS financial_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id UUID NOT NULL,
    pluggy_account_id UUID NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    type VARCHAR(30) NOT NULL,
    subtype VARCHAR(60) NOT NULL,
    balance BIGINT NOT NULL DEFAULT 0,
    currency_code VARCHAR(8) NOT NULL DEFAULT 'BRL',
    number VARCHAR(100),
    institution_name VARCHAR(160) NOT NULL,
    institution_image_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    CONSTRAINT financial_accounts_connection_fk
        FOREIGN KEY (item_id) REFERENCES pluggy_connections(item_id) ON DELETE CASCADE
);

ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'manual' NOT NULL;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS pluggy_item_id UUID;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS pluggy_account_id UUID;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS brand VARCHAR(50);
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS available_limit BIGINT;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL;

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'manual' NOT NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS pluggy_item_id UUID;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS pluggy_account_id UUID;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS pluggy_transaction_id UUID;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS pluggy_status VARCHAR(50);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS raw_category VARCHAR(160);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS merchant_name VARCHAR(255);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_edited BOOLEAN DEFAULT FALSE NOT NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_cards_pluggy_account
    ON credit_cards(pluggy_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_pluggy_id
    ON transactions(pluggy_transaction_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_connections_user ON pluggy_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_financial_accounts_user ON financial_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_pluggy_account ON transactions(pluggy_account_id);

-- 10. Durable idempotency log for webhook retries
CREATE TABLE IF NOT EXISTS pluggy_webhook_events (
    event_id UUID PRIMARY KEY,
    event_name VARCHAR(100) NOT NULL,
    item_id UUID,
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    last_error TEXT,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE
);
