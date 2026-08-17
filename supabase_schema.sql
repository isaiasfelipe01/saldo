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

-- 7. Create Budgets Table (Metas de Gastos)
CREATE TABLE IF NOT EXISTS budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    amount BIGINT NOT NULL, -- limit in cents (e.g. 50000 = R$ 500.00)
    period VARCHAR(7) NOT NULL, -- format: YYYY-MM
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- Ensures a user can only have one budget per category per month
    CONSTRAINT unique_category_period_user UNIQUE(category_id, period, user_id)
);

-- Index for performance
CREATE INDEX IF NOT EXISTS idx_budgets_user_period ON budgets(user_id, period);
