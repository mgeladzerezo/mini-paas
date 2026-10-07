"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.String(length=100), nullable=False),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_table(
        "apps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("repo_full_name", sa.String(length=200), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("health_path", sa.String(length=200), nullable=False),
        sa.Column("min_replicas", sa.Integer(), nullable=False),
        sa.Column("max_replicas", sa.Integer(), nullable=False),
        sa.Column("current_release_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_apps_tenant_name"),
    )
    op.create_index(op.f("ix_apps_repo_full_name"), "apps", ["repo_full_name"], unique=False)
    op.create_index(op.f("ix_apps_tenant_id"), "apps", ["tenant_id"], unique=False)
    op.create_table(
        "github_installations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id"),
    )
    op.create_index(
        op.f("ix_github_installations_tenant_id"),
        "github_installations",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("verification_token", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dns_status", sa.String(length=20), nullable=False),
        sa.Column("tls_status", sa.String(length=20), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "hostname", name="uq_domains_app_hostname"),
    )
    op.create_index(op.f("ix_domains_app_id"), "domains", ["app_id"], unique=False)
    op.create_index(op.f("ix_domains_hostname"), "domains", ["hostname"], unique=False)
    op.create_table(
        "env_vars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "key", name="uq_env_vars_app_key"),
    )
    op.create_index(op.f("ix_env_vars_app_id"), "env_vars", ["app_id"], unique=False)
    op.create_table(
        "releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("commit_message", sa.String(length=500), nullable=False),
        sa.Column("triggered_by", sa.String(length=254), nullable=False),
        sa.Column("image", sa.String(length=500), nullable=True),
        sa.Column("env_encrypted", sa.Text(), nullable=False),
        sa.Column("build_ref", sa.String(length=200), nullable=True),
        sa.Column("logs", sa.Text(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("source_release_id", sa.Integer(), nullable=True),
        sa.Column("healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deploy_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "version", name="uq_releases_app_version"),
    )
    op.create_index(op.f("ix_releases_app_id"), "releases", ["app_id"], unique=False)
    op.create_index(op.f("ix_releases_status"), "releases", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_releases_status"), table_name="releases")
    op.drop_index(op.f("ix_releases_app_id"), table_name="releases")
    op.drop_table("releases")
    op.drop_index(op.f("ix_env_vars_app_id"), table_name="env_vars")
    op.drop_table("env_vars")
    op.drop_index(op.f("ix_domains_hostname"), table_name="domains")
    op.drop_index(op.f("ix_domains_app_id"), table_name="domains")
    op.drop_table("domains")
    op.drop_table("users")
    op.drop_index(op.f("ix_github_installations_tenant_id"), table_name="github_installations")
    op.drop_table("github_installations")
    op.drop_index(op.f("ix_apps_tenant_id"), table_name="apps")
    op.drop_index(op.f("ix_apps_repo_full_name"), table_name="apps")
    op.drop_table("apps")
    op.drop_table("webhook_deliveries")
    op.drop_table("tenants")
