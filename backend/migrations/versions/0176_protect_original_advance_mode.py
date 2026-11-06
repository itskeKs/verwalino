"""Protect original advance meaning even when the writer cannot read the books."""

from alembic import op

revision = "0176"
down_revision = "0175"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("LOCK TABLE public.lease_agreement IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("LOCK TABLE public.audit_event IN SHARE MODE NOWAIT")
    op.execute("""CREATE INDEX ix_audit_event_booked_payment ON public.audit_event (organization_id, entity_id)
        WHERE entity_type='payment' AND action='payment.booked'""")
    op.execute("""
        CREATE FUNCTION public.protect_original_advance_mode() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp SET row_security=off AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.payment
                        WHERE organization_id=OLD.organization_id AND lease_agreement_id=OLD.id AND status='booked')
                OR EXISTS (SELECT 1 FROM public.settlement_run
                        WHERE organization_id=OLD.organization_id AND lease_agreement_id=OLD.id AND status='finalized')
            THEN
                RAISE EXCEPTION 'Original advance mode is frozen'
                    USING ERRCODE='23514', CONSTRAINT='original_advance_mode_frozen';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.protect_original_advance_mode() FROM PUBLIC, app_rw, app_worker, app_review")
    op.execute("""
        CREATE TRIGGER protect_original_advance_mode BEFORE UPDATE OF advance_mode ON public.lease_agreement
            FOR EACH ROW WHEN (OLD.advance_mode IS DISTINCT FROM NEW.advance_mode)
            EXECUTE FUNCTION public.protect_original_advance_mode();
    """)


def downgrade():
    op.execute("LOCK TABLE public.lease_agreement IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("LOCK TABLE public.audit_event IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("DROP TRIGGER protect_original_advance_mode ON public.lease_agreement")
    op.execute("DROP FUNCTION public.protect_original_advance_mode()")
    op.execute("DROP INDEX public.ix_audit_event_booked_payment")
