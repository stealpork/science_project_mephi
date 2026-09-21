import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a50b5bc96692"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("consumer", sa.String(length=100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "consumer"),
    )
    op.create_table(
        "security_audit",
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id"),
    )
    op.create_index(
        op.f("ix_security_audit_created_at"), "security_audit", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_security_audit_target_id"), "security_audit", ["target_id"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("mfa_secret", sa.Text(), nullable=True),
        sa.Column("mfa_pending_secret", sa.Text(), nullable=True),
        sa.Column("recovery_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("deletion_id", sa.String(length=36), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_created_at"), "users", ["created_at"], unique=False)
    op.create_index(op.f("ix_users_status"), "users", ["status"], unique=False)
    op.create_table(
        "account_deletions",
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("completed_services", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_account_deletions_created_at"), "account_deletions", ["created_at"], unique=False
    )
    op.create_table(
        "security_tokens",
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_security_tokens_created_at"), "security_tokens", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_security_tokens_user_id"), "security_tokens", ["user_id"], unique=False
    )
    op.create_table(
        "sessions",
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("refresh_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_verified", sa.Boolean(), nullable=False),
        sa.Column("user_agent", sa.String(length=300), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sessions_created_at"), "sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)


def downgrade():

    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_created_at"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_security_tokens_user_id"), table_name="security_tokens")
    op.drop_index(op.f("ix_security_tokens_created_at"), table_name="security_tokens")
    op.drop_table("security_tokens")
    op.drop_index(op.f("ix_account_deletions_created_at"), table_name="account_deletions")
    op.drop_table("account_deletions")
    op.drop_index(op.f("ix_users_status"), table_name="users")
    op.drop_index(op.f("ix_users_created_at"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_security_audit_target_id"), table_name="security_audit")
    op.drop_index(op.f("ix_security_audit_created_at"), table_name="security_audit")
    op.drop_table("security_audit")
    op.drop_table("processed_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
