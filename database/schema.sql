CREATE TABLE IF NOT EXISTS code_symbols (
    id VARCHAR(255) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL DEFAULT 'default_project',
    file_path VARCHAR(512) NOT NULL,
    symbol_name VARCHAR(255) NOT NULL,
    symbol_type VARCHAR(64) NOT NULL,
    scope_path VARCHAR(512) NOT NULL DEFAULT '',
    signature_hash VARCHAR(64) NOT NULL DEFAULT '0000000000000000',
    start_line INT NOT NULL,
    end_line INT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_symbols_canonical 
ON code_symbols(project_id, file_path, scope_path, symbol_name, signature_hash);

CREATE TABLE IF NOT EXISTS code_dependencies (
    caller_symbol_id VARCHAR(255) REFERENCES code_symbols(id) ON DELETE CASCADE,
    callee_symbol_id VARCHAR(255) REFERENCES code_symbols(id) ON DELETE CASCADE,
    project_id VARCHAR(64) NOT NULL DEFAULT 'default_project',
    PRIMARY KEY (caller_symbol_id, callee_symbol_id)
);

CREATE INDEX IF NOT EXISTS idx_code_deps_callee ON code_dependencies(callee_symbol_id, project_id);
CREATE INDEX IF NOT EXISTS idx_code_symbols_lookup ON code_symbols(project_id, file_path);