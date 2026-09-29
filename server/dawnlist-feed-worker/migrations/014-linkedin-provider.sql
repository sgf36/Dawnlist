-- LinkedIn Job Library as a provider (disabled by default, priority behind TheirStack).
-- Enable with: UPDATE providers SET enabled = 1 WHERE name = 'linkedin';
INSERT INTO providers (name, enabled, priority, note) VALUES
    ('linkedin', 0, 20, 'Job Library API (Ad Library). Paid/sponsored posts only. Token expires ~60 days.')
ON CONFLICT(name) DO NOTHING;
