"""Worker role executes the bounded signup cleanup (issue #634)."""

from alembic import op

revision = "0187"
down_revision = "0186"
branch_labels = None
depends_on = None


def upgrade():
    # 0144 fenced the signup definers to app_rw: the worker role parses uploaded
    # documents and must not touch request rows. The hosted declaration worker
    # now additionally deletes never-confirmed sign-ups on its own fixed cadence
    # so the retention promise holds without sign-up traffic. The function takes
    # no input and deletes only signup_request rows whose expiry is more than
    # seven days past, so the 0144 boundary moves for exactly this one function
    # and the grant states the authorization independent of role inheritance.
    op.execute("GRANT EXECUTE ON FUNCTION public.cleanup_signup_requests() TO app_worker")


def downgrade():
    op.execute("REVOKE EXECUTE ON FUNCTION public.cleanup_signup_requests() FROM app_worker")
