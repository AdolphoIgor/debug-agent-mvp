CREATE TABLE IF NOT EXISTS clients (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id VARCHAR(64) PRIMARY KEY,
    client_id VARCHAR(64) REFERENCES clients(id) ON DELETE CASCADE,
    repo_path VARCHAR(512) NOT NULL,
    primary_language VARCHAR(32) NOT NULL,
    default_branch VARCHAR(64) DEFAULT 'main',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticket_tracking (
    ticket_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    current_status VARCHAR(32) NOT NULL CHECK (current_status IN ('PENDING', 'IN_PROGRESS', 'FAILED', 'RESOLVED')),
    assigned_branch VARCHAR(255) NOT NULL,
    sanitized_title TEXT NOT NULL,
    sanitized_description TEXT NOT NULL,
    resolved_ticket_ref VARCHAR(64) REFERENCES ticket_tracking(ticket_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticket_semantic_vectors (
    ticket_id VARCHAR(64) PRIMARY KEY REFERENCES ticket_tracking(ticket_id) ON DELETE CASCADE,
    embedding_id VARCHAR(64) NOT NULL,
    indexed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS code_symbols (
    id VARCHAR(255) PRIMARY KEY,
    project_id VARCHAR(64) REFERENCES projects(id) ON DELETE CASCADE,
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
    project_id VARCHAR(64) REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (caller_symbol_id, callee_symbol_id)
);

CREATE TABLE IF NOT EXISTS execution_telemetry_logs (
    id BIGSERIAL PRIMARY KEY,
    ticket_id VARCHAR(64) REFERENCES ticket_tracking(ticket_id) ON DELETE CASCADE,
    node_name VARCHAR(64) NOT NULL,
    stagnation_count INT NOT NULL,
    consultant_cycle_count INT NOT NULL,
    action_taken VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ticket_tracking_status ON ticket_tracking(current_status);
CREATE INDEX IF NOT EXISTS idx_ticket_tracking_project ON ticket_tracking(project_id);
CREATE INDEX IF NOT EXISTS idx_code_deps_callee ON code_dependencies(callee_symbol_id, project_id);
CREATE INDEX IF NOT EXISTS idx_code_symbols_lookup ON code_symbols(project_id, file_path);