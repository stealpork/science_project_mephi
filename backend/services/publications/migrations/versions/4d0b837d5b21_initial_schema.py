import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "4d0b837d5b21"
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
        "paper_audit",
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id"),
    )
    op.create_index(op.f("ix_paper_audit_created_at"), "paper_audit", ["created_at"], unique=False)
    op.create_index(op.f("ix_paper_audit_paper_id"), "paper_audit", ["paper_id"], unique=False)
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("consumer", sa.String(length=100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "consumer"),
    )
    op.create_table(
        "research_papers",
        sa.Column("creator_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("area_id", sa.String(length=36), nullable=True),
        sa.Column("university_id", sa.String(length=36), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("external_links", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("file_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("moderation_status", sa.String(length=20), nullable=False),
        sa.Column("views_count", sa.Integer(), nullable=False),
        sa.Column("saves_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_papers_area_id"), "research_papers", ["area_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_papers_created_at"), "research_papers", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_research_papers_creator_id"), "research_papers", ["creator_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_papers_owner_id"), "research_papers", ["owner_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_papers_project_id"), "research_papers", ["project_id"], unique=False
    )
    op.create_index(op.f("ix_research_papers_status"), "research_papers", ["status"], unique=False)
    op.create_index(
        op.f("ix_research_papers_university_id"), "research_papers", ["university_id"], unique=False
    )
    op.create_index(op.f("ix_research_papers_year"), "research_papers", ["year"], unique=False)
    op.create_table(
        "paper_authors",
        sa.Column("paper_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("affiliation", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("access_granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_id"],
            ["research_papers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_paper_authors_created_at"), "paper_authors", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_paper_authors_paper_id"), "paper_authors", ["paper_id"], unique=False)
    op.create_index(op.f("ix_paper_authors_user_id"), "paper_authors", ["user_id"], unique=False)
    op.create_index(
        "one_account_per_paper",
        "paper_authors",
        ["paper_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade():

    op.drop_index(
        "one_account_per_paper",
        table_name="paper_authors",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_paper_authors_user_id"), table_name="paper_authors")
    op.drop_index(op.f("ix_paper_authors_paper_id"), table_name="paper_authors")
    op.drop_index(op.f("ix_paper_authors_created_at"), table_name="paper_authors")
    op.drop_table("paper_authors")
    op.drop_index(op.f("ix_research_papers_year"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_university_id"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_status"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_project_id"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_owner_id"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_creator_id"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_created_at"), table_name="research_papers")
    op.drop_index(op.f("ix_research_papers_area_id"), table_name="research_papers")
    op.drop_table("research_papers")
    op.drop_table("processed_events")
    op.drop_index(op.f("ix_paper_audit_paper_id"), table_name="paper_audit")
    op.drop_index(op.f("ix_paper_audit_created_at"), table_name="paper_audit")
    op.drop_table("paper_audit")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
