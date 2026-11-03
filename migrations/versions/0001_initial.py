"""Initial durable service schema. Immutable migration snapshot."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE heartbeats (
	worker VARCHAR(128) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (worker)
)
    """)
    op.execute("""
CREATE TABLE sessions (
	id VARCHAR(36) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(128), 
	request_hash VARCHAR(64) NOT NULL, 
	metadata JSONB NOT NULL, 
	closed BOOLEAN NOT NULL, 
	active_job VARCHAR(36), 
	next_seq INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (owner, idempotency_key)
)
    """)
    op.execute("""
CREATE INDEX ix_sessions_owner ON sessions (owner)
    """)
    op.execute("""
CREATE TABLE jobs (
	id VARCHAR(36) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	operation VARCHAR(80) NOT NULL, 
	idempotency_key VARCHAR(128), 
	request_hash VARCHAR(64) NOT NULL, 
	correlation_id VARCHAR(128) NOT NULL, 
	session_id VARCHAR(36), 
	state VARCHAR(16) NOT NULL, 
	snapshot JSONB NOT NULL, 
	metadata JSONB NOT NULL, 
	result JSONB, 
	error VARCHAR(64), 
	worker VARCHAR(128), 
	token VARCHAR(36), 
	lease_until TIMESTAMP WITH TIME ZONE, 
	cancel_requested BOOLEAN NOT NULL, 
	attempts INTEGER NOT NULL, 
	event_count INTEGER NOT NULL, 
	consumer_max_running INTEGER NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (owner, operation, idempotency_key), 
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)
    """)
    op.execute("""
CREATE INDEX ix_jobs_owner ON jobs (owner)
    """)
    op.execute("""
CREATE INDEX ix_jobs_queue ON jobs (state, available_at, created_at)
    """)
    op.execute("""
CREATE TABLE artifacts (
	id VARCHAR(36) NOT NULL, 
	job_id VARCHAR(36) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	media_type VARCHAR(128) NOT NULL, 
	size INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	data BYTEA NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
    """)
    op.execute("""
CREATE INDEX ix_artifacts_job_id ON artifacts (job_id)
    """)
    op.execute("""
CREATE TABLE events (
	id BIGSERIAL NOT NULL, 
	job_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	data JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
    """)
    op.execute("""
CREATE INDEX ix_events_job_id ON events (job_id)
    """)
    op.execute("""
CREATE TABLE messages (
	id BIGSERIAL NOT NULL, 
	session_id VARCHAR(36) NOT NULL, 
	job_id VARCHAR(36), 
	seq INTEGER NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content JSONB NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (session_id, seq), 
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL
)
    """)
    op.execute("""
CREATE INDEX ix_messages_job_id ON messages (job_id)
    """)
    op.execute("""
CREATE INDEX ix_messages_session_id ON messages (session_id)
    """)
    op.execute("""
CREATE TABLE outbox (
	id VARCHAR(36) NOT NULL, 
	job_id VARCHAR(36) NOT NULL, 
	url VARCHAR(2048) NOT NULL, 
	payload JSONB NOT NULL, 
	attempts INTEGER NOT NULL, 
	delivered BOOLEAN NOT NULL, 
	exhausted BOOLEAN NOT NULL, 
	token VARCHAR(36), 
	lease_until TIMESTAMP WITH TIME ZONE, 
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (job_id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
    """)


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("messages")
    op.drop_table("events")
    op.drop_table("artifacts")
    op.drop_table("jobs")
    op.drop_table("sessions")
    op.drop_table("heartbeats")
