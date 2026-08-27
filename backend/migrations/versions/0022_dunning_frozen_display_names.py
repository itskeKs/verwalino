"""Snapshot-Namens-Einfrieren: dunning_notice.frozen_display_names + Trigger (ADR-0012).

Teil des Snapshot-Namens-Einfrierens (ADR-0012, Abschnitt „Einfrieren beim
Ausstellen/Finalisieren"). settlement_run/economic_plan frieren die Anzeige-Namen
in ihren bereits vorhandenen, vom Trigger geschützten ``snapshot_payload`` ein --
dafür ist KEINE Migration nötig. dunning_notice hat keinen ``snapshot_payload``
(nur die Liste ``included_items``), deshalb bekommt es hier eine eigene
eingefrorene Spalte ``frozen_display_names`` (JSONB).

Zwei Schritte:
  1. ADD COLUMN frozen_display_names JSONB NULL. Nullable = Altbestand
     (bereits ausgestellte Mahnungen) bleibt NULL und rendert weiter live --
     genau das gewünschte Fallback-Verhalten. Bricht keine Bestandsdaten.
  2. CREATE OR REPLACE des Unveränderbarkeits-Triggers aus 0019: der Trigger
     arbeitet mit einer DENYLIST benannter Spalten; eine NEU hinzugefügte Spalte
     wäre andernfalls auf einer ausgestellten Mahnung veränderbar. Deshalb wird
     ``frozen_display_names`` in die Denylist aufgenommen. Geschrieben wird die
     Spalte ausschließlich beim Ausstellen (OLD.status='draft', der Trigger
     greift dort noch nicht); danach ist sie unveränderbar (GoBD/ADR-0003).

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Neue, nullable eingefrorene Anzeige-Namen-Spalte
    # -----------------------------------------------------------------------
    op.add_column(
        "dunning_notice",
        sa.Column("frozen_display_names", JSONB, nullable=True),
    )

    # -----------------------------------------------------------------------
    # 2. Trigger-Denylist um frozen_display_names erweitern (ADR-0003/0012)
    # -----------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_dunning_notice_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD-style immutability for dunning_notice (ADR-0003 pattern).
    --
    -- FREEZE CONDITION: OLD.status = 'issued'
    --
    -- Content-bearing columns (frozen once issued):
    --   organization_id, tenant_profile_id, owner_profile_id, level,
    --   issue_date, due_date, principal_amount, fee_amount, total_amount,
    --   included_items, frozen_display_names (ADR-0012), status, issued_at,
    --   issued_by, note
    --
    -- Allowed changes on an issued notice:
    --   updated_at (technical timestamp only)
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'issued' THEN
                RAISE EXCEPTION 'Immutability: DELETE of issued dunning_notice is forbidden (id=%)', OLD.id;
            END IF;
            RETURN OLD;
        END IF;

        -- UPDATE path
        IF OLD.status = 'issued' THEN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change organization_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_profile_id IS DISTINCT FROM OLD.tenant_profile_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change tenant_profile_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.owner_profile_id IS DISTINCT FROM OLD.owner_profile_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change owner_profile_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.level IS DISTINCT FROM OLD.level THEN
                RAISE EXCEPTION 'Immutability: Cannot change level on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issue_date IS DISTINCT FROM OLD.issue_date THEN
                RAISE EXCEPTION 'Immutability: Cannot change issue_date on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.due_date IS DISTINCT FROM OLD.due_date THEN
                RAISE EXCEPTION 'Immutability: Cannot change due_date on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.principal_amount IS DISTINCT FROM OLD.principal_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change principal_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.fee_amount IS DISTINCT FROM OLD.fee_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change fee_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.total_amount IS DISTINCT FROM OLD.total_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change total_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.included_items IS DISTINCT FROM OLD.included_items THEN
                RAISE EXCEPTION 'Immutability: Cannot change included_items on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.frozen_display_names IS DISTINCT FROM OLD.frozen_display_names THEN
                RAISE EXCEPTION 'Immutability: Cannot change frozen_display_names on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'Immutability: Cannot change status on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issued_at IS DISTINCT FROM OLD.issued_at THEN
                RAISE EXCEPTION 'Immutability: Cannot change issued_at on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issued_by IS DISTINCT FROM OLD.issued_by THEN
                RAISE EXCEPTION 'Immutability: Cannot change issued_by on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.note IS DISTINCT FROM OLD.note THEN
                RAISE EXCEPTION 'Immutability: Cannot change note on issued dunning_notice (id=%)', OLD.id;
            END IF;
            -- updated_at: technical timestamp, always allowed
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Trigger auf den Stand von 0019 zurücksetzen (ohne frozen_display_names-Check)...
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_dunning_notice_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'issued' THEN
                RAISE EXCEPTION 'Immutability: DELETE of issued dunning_notice is forbidden (id=%)', OLD.id;
            END IF;
            RETURN OLD;
        END IF;

        IF OLD.status = 'issued' THEN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change organization_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_profile_id IS DISTINCT FROM OLD.tenant_profile_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change tenant_profile_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.owner_profile_id IS DISTINCT FROM OLD.owner_profile_id THEN
                RAISE EXCEPTION 'Immutability: Cannot change owner_profile_id on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.level IS DISTINCT FROM OLD.level THEN
                RAISE EXCEPTION 'Immutability: Cannot change level on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issue_date IS DISTINCT FROM OLD.issue_date THEN
                RAISE EXCEPTION 'Immutability: Cannot change issue_date on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.due_date IS DISTINCT FROM OLD.due_date THEN
                RAISE EXCEPTION 'Immutability: Cannot change due_date on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.principal_amount IS DISTINCT FROM OLD.principal_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change principal_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.fee_amount IS DISTINCT FROM OLD.fee_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change fee_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.total_amount IS DISTINCT FROM OLD.total_amount THEN
                RAISE EXCEPTION 'Immutability: Cannot change total_amount on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.included_items IS DISTINCT FROM OLD.included_items THEN
                RAISE EXCEPTION 'Immutability: Cannot change included_items on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'Immutability: Cannot change status on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issued_at IS DISTINCT FROM OLD.issued_at THEN
                RAISE EXCEPTION 'Immutability: Cannot change issued_at on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.issued_by IS DISTINCT FROM OLD.issued_by THEN
                RAISE EXCEPTION 'Immutability: Cannot change issued_by on issued dunning_notice (id=%)', OLD.id;
            END IF;
            IF NEW.note IS DISTINCT FROM OLD.note THEN
                RAISE EXCEPTION 'Immutability: Cannot change note on issued dunning_notice (id=%)', OLD.id;
            END IF;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    # ...dann die Spalte entfernen.
    op.drop_column("dunning_notice", "frozen_display_names")
