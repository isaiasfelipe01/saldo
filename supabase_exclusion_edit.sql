-- Marca lançamentos cujo "fora do resultado" foi ajustado manualmente, para a
-- sincronização com a Pluggy não desfazer a sua escolha. Rode uma vez no SQL Editor do Supabase.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_edited_exclusion BOOLEAN DEFAULT FALSE NOT NULL;
