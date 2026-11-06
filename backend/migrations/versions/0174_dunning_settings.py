"""Add organization dunning policy and published German statutory base rates."""
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "0174"
down_revision = "0173"
branch_labels = None
depends_on = None

# Deutsche Bundesbank, Basiszinssatz nach § 247 BGB (both half-years per row):
# https://www.bundesbank.de/de/bundesbank/organisation/agb-und-regelungen/basiszinssatz-607820
# January 2002: Bundesbank press release, 28 December 2001, page 2.
_RATES = (
    ("2.57", "2.47"),    # 2002
    ("1.97", "1.22"),
    ("1.14", "1.13"),
    ("1.21", "1.17"),
    ("1.37", "1.95"),
    ("2.70", "3.19"),
    ("3.32", "3.19"),
    ("1.62", "0.12"),
    ("0.12", "0.12"),
    ("0.12", "0.37"),
    ("0.12", "0.12"),
    ("-0.13", "-0.38"),
    ("-0.63", "-0.73"),
    ("-0.83", "-0.83"),
    ("-0.83", "-0.88"),
    ("-0.88", "-0.88"),
    ("-0.88", "-0.88"),
    ("-0.88", "-0.88"),
    ("-0.88", "-0.88"),
    ("-0.88", "-0.88"),
    ("-0.88", "-0.88"),  # 2022
    ("1.62", "3.12"),
    ("3.62", "3.37"),
    ("2.27", "1.27"),
    ("1.27", "1.52"),    # 2026
)


def upgrade():
    op.execute("""
      CREATE TABLE public.dunning_settings (
        organization_id uuid PRIMARY KEY REFERENCES public.organization(id) ON DELETE RESTRICT,
        fee_level_1 numeric(14,2) NOT NULL CHECK (fee_level_1 >= 0 AND fee_level_1 <> 'NaN'),
        fee_level_2 numeric(14,2) NOT NULL CHECK (fee_level_2 >= 0 AND fee_level_2 <> 'NaN'),
        fee_level_3 numeric(14,2) NOT NULL CHECK (fee_level_3 >= 0 AND fee_level_3 <> 'NaN'),
        default_interest_enabled boolean NOT NULL
      );
      ALTER TABLE public.dunning_settings ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.dunning_settings FORCE ROW LEVEL SECURITY;
      CREATE POLICY dunning_settings_read ON public.dunning_settings FOR SELECT TO app_rw
        USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid);
      CREATE POLICY dunning_settings_admin ON public.dunning_settings FOR ALL TO app_rw
        USING (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN')
        WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid
          AND public.scope_actor_role(organization_id) = 'OWNER_ADMIN');
      REVOKE ALL ON public.dunning_settings FROM PUBLIC, app_worker, app_review;
      GRANT SELECT, INSERT, UPDATE, DELETE ON public.dunning_settings TO app_rw;
      CREATE TRIGGER a_organization_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON public.dunning_settings
        FOR EACH ROW EXECUTE FUNCTION public.organization_lifecycle_guard();

      CREATE TABLE public.base_interest_rate (
        valid_from date PRIMARY KEY CHECK (extract(day FROM valid_from) = 1
          AND extract(month FROM valid_from) IN (1, 7)),
        rate_percent numeric(5,2) NOT NULL CHECK (rate_percent <> 'NaN')
      );
      REVOKE ALL ON public.base_interest_rate FROM PUBLIC, app_rw, app_worker, app_review;
      GRANT SELECT ON public.base_interest_rate TO app_rw;
    """)
    rate_table = sa.table("base_interest_rate", sa.column("valid_from", sa.Date),
                          sa.column("rate_percent", sa.Numeric(5, 2)))
    op.bulk_insert(rate_table, [
        {"valid_from": date(year, month, 1), "rate_percent": Decimal(rate)}
        for year, rates in enumerate(_RATES, start=2002)
        for month, rate in zip((1, 7), rates, strict=True)
    ])


def downgrade():
    op.execute("LOCK TABLE public.dunning_settings, public.dunning_notice IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.dunning_settings) THEN
          RAISE EXCEPTION 'Downgrade would discard saved dunning settings';
        END IF;
        IF EXISTS (SELECT 1 FROM public.dunning_notice WHERE interest_snapshot ? 'premium') THEN
          RAISE EXCEPTION 'Previous releases cannot render retained segmented dunning interest';
        END IF;
      END $$;
    """)
    op.drop_table("dunning_settings")
    op.drop_table("base_interest_rate")
