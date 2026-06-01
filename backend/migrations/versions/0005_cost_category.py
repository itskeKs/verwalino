"""M2: cost_category -- global, versioned standard chart of accounts.

Global table (no organization_id, no RLS) -- decision E-1 confirmed.
Seed all SDD section 7 categories with plan_version = 1.
app_rw gets SELECT only (read-only; writes happen through migrations).

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---- Controlled vocabularies (String + CHECK, no DB enums) ----

COST_GROUPS = ("UMLAGEFAEHIG", "GEMISCHT_PRUEFEN", "NICHT_UMLAGEFAEHIG")

TENANT_APPORTIONABILITY = (
    "UMLAGEFAEHIG",
    "UMLAGEFAEHIG_EXTERN_PRUEFEN",
    "GEMISCHT_PRUEFEN",
    "TEILWEISE_PRUEFEN",
    "MANUELL_PRUEFEN",
    "NICHT_UMLAGEFAEHIG",
)

DISTRIBUTION_KEYS = (
    "MEA",
    "WOHNFLAECHE",
    "BEHEIZTE_FLAECHE",
    "EINHEIT",
    "PERSONENZAHL",
    "VERBRAUCH",
    "EXTERNE_ABRECHNUNG",
    "MANUELLER_BETRAG",
)


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# ---- SDD section 7 seed data (plan_version = 1) ----
# Decision E-7: Owner default = MEA, Tenant default = WOHNFLAECHE where SDD is ambiguous.
SEED_CATEGORIES = [
    # --- 7.1 Umlagefaehige Betriebskosten ---
    {
        "code": "GRUNDSTEUER",
        "label": "Grundsteuer / oeffentliche Lasten",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 10,
    },
    {
        "code": "KALTWASSER",
        "label": "Kaltwasser",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "VERBRAUCH",
        "tenant_distribution_key_default": "VERBRAUCH",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 20,
    },
    {
        "code": "ENTWAESSERUNG",
        "label": "Entwaesserung / Abwasser",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "VERBRAUCH",
        "tenant_distribution_key_default": "VERBRAUCH",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 30,
    },
    {
        "code": "HEIZUNG_ZENTRAL",
        "label": "Heizung zentral",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG_EXTERN_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "tenant_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "reserve_relevant": False,
        "legal_hint": "Externe Heizkostenabrechnung beachten (HeizkostenV)",
        "sort_order": 40,
    },
    {
        "code": "WARMWASSER_ZENTRAL",
        "label": "Warmwasser zentral",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG_EXTERN_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "tenant_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "reserve_relevant": False,
        "legal_hint": "Externe Heizkostenabrechnung beachten (HeizkostenV)",
        "sort_order": 50,
    },
    {
        "code": "MUELLBESEITIGUNG",
        "label": "Muellbeseitigung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "EINHEIT",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 60,
    },
    {
        "code": "STRASSENREINIGUNG",
        "label": "Strassenreinigung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 70,
    },
    {
        "code": "WINTERDIENST",
        "label": "Winterdienst",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 80,
    },
    {
        "code": "GEBAEUDEREINIGUNG",
        "label": "Gebaeudereinigung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 90,
    },
    {
        "code": "GARTENPFLEGE",
        "label": "Gartenpflege",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 100,
    },
    {
        "code": "ALLGEMEINSTROM",
        "label": "Allgemeinstrom / Beleuchtung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 110,
    },
    {
        "code": "SCHORNSTEINREINIGUNG",
        "label": "Schornsteinreinigung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "EINHEIT",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 120,
    },
    {
        "code": "GEBAEUDEVERSICHERUNG",
        "label": "Gebaeudeversicherung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 130,
    },
    {
        "code": "HAFTPFLICHTVERSICHERUNG",
        "label": "Haftpflichtversicherung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 140,
    },
    {
        "code": "AUFZUG",
        "label": "Aufzug Betrieb/Wartung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "EINHEIT",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 150,
    },
    {
        "code": "WASCHRAUM",
        "label": "Waschraum / Gemeinschaftswascheinrichtung",
        "cost_group": "UMLAGEFAEHIG",
        "tenant_apportionability_default": "UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "EINHEIT",
        "reserve_relevant": False,
        "legal_hint": None,
        "sort_order": 160,
    },
    # --- 7.2 Gemischte/pruefpflichtige Kosten ---
    {
        "code": "HAUSMEISTER",
        "label": "Hausmeister / Hauswart",
        "cost_group": "GEMISCHT_PRUEFEN",
        "tenant_apportionability_default": "GEMISCHT_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": "Verwaltungs-, Reparatur- und Instandhaltungsanteile herausrechnen",
        "sort_order": 200,
    },
    {
        "code": "SONSTIGE_BK",
        "label": "Sonstige Betriebskosten",
        "cost_group": "GEMISCHT_PRUEFEN",
        "tenant_apportionability_default": "MANUELL_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": "WOHNFLAECHE",
        "reserve_relevant": False,
        "legal_hint": "Nur bei konkreter mietvertraglicher Grundlage und laufenden Kosten",
        "sort_order": 210,
    },
    {
        "code": "KABEL_BREITBAND",
        "label": "Kabel / Breitband / Gemeinschaftsantenne",
        "cost_group": "GEMISCHT_PRUEFEN",
        "tenant_apportionability_default": "MANUELL_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "EINHEIT",
        "tenant_distribution_key_default": "EINHEIT",
        "reserve_relevant": False,
        "legal_hint": "Seit 2024 eingeschraenkte Umlagefaehigkeit",
        "sort_order": 220,
    },
    {
        "code": "MESSDIENST",
        "label": "Messdienst / Abrechnungsdienst",
        "cost_group": "GEMISCHT_PRUEFEN",
        "tenant_apportionability_default": "UMLAGEFAEHIG_EXTERN_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "tenant_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "reserve_relevant": False,
        "legal_hint": "Keine Doppelabrechnung mit Heiz-/Wasserkosten",
        "sort_order": 230,
    },
    {
        "code": "CO2_KOSTEN",
        "label": "CO2-Kosten",
        "cost_group": "GEMISCHT_PRUEFEN",
        "tenant_apportionability_default": "TEILWEISE_PRUEFEN",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "tenant_distribution_key_default": "EXTERNE_ABRECHNUNG",
        "reserve_relevant": False,
        "legal_hint": "Nur Mieteranteil umlagefaehig (10-Stufen-Modell CO2KostAufG)",
        "sort_order": 240,
    },
    # --- 7.3 Nicht umlagefaehige Eigentuemer-/WEG-Kosten ---
    {
        "code": "WEG_VERWALTERKOSTEN",
        "label": "WEG-Verwalterkosten",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Verwaltungskosten",
        "sort_order": 300,
    },
    {
        "code": "BUCHHALTUNG",
        "label": "Buchhaltung / Jahresabrechnung",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Verwaltungskosten",
        "sort_order": 310,
    },
    {
        "code": "EIGENTUEMERVERSAMMLUNG",
        "label": "Eigentuemerversammlung",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Verwaltungskosten",
        "sort_order": 320,
    },
    {
        "code": "BANKGEBUEHREN",
        "label": "Bankgebuehren WEG-Konto",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Verwaltungskosten / Admin-Kosten",
        "sort_order": 330,
    },
    {
        "code": "RECHTSBERATUNG",
        "label": "Rechtsberatung / Prozesskosten",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Regelmaessig Eigentuemer-/WEG-Kosten",
        "sort_order": 340,
    },
    {
        "code": "INSTANDHALTUNG",
        "label": "Instandhaltung",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Keine Betriebskosten",
        "sort_order": 350,
    },
    {
        "code": "INSTANDSETZUNG",
        "label": "Instandsetzung / Reparaturen",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Keine Betriebskosten",
        "sort_order": 360,
    },
    {
        "code": "ERHALTUNGSRUECKLAGE",
        "label": "Erhaltungsruecklage / Instandhaltungsruecklage",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": True,
        "legal_hint": "Getrennt ausweisen",
        "sort_order": 370,
    },
    {
        "code": "SONDERUMLAGE",
        "label": "Sonderumlage",
        "cost_group": "NICHT_UMLAGEFAEHIG",
        "tenant_apportionability_default": "NICHT_UMLAGEFAEHIG",
        "owner_billing_relevant_default": True,
        "owner_distribution_key_default": "MEA",
        "tenant_distribution_key_default": None,
        "reserve_relevant": False,
        "legal_hint": "Zweck pruefen, Default owner-only",
        "sort_order": 380,
    },
]


def upgrade() -> None:
    # --- cost_category (global, no organization_id) ---
    op.create_table(
        "cost_category",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("plan_version", sa.SmallInteger(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("cost_group", sa.String(20), nullable=False),
        sa.Column("tenant_apportionability_default", sa.String(30), nullable=False),
        sa.Column("owner_billing_relevant_default", sa.Boolean(), nullable=False),
        sa.Column("owner_distribution_key_default", sa.String(20), nullable=True),
        sa.Column("tenant_distribution_key_default", sa.String(20), nullable=True),
        sa.Column("reserve_relevant", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("legal_hint", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("plan_version", "code", name="uq_cost_category_plan_version_code"),
        sa.CheckConstraint(
            f"cost_group IN ({_in_check(COST_GROUPS)})",
            name="ck_cost_category_cost_group",
        ),
        sa.CheckConstraint(
            f"tenant_apportionability_default IN ({_in_check(TENANT_APPORTIONABILITY)})",
            name="ck_cost_category_tenant_apportionability",
        ),
        sa.CheckConstraint(
            f"owner_distribution_key_default IS NULL OR "
            f"owner_distribution_key_default IN ({_in_check(DISTRIBUTION_KEYS)})",
            name="ck_cost_category_owner_dist_key",
        ),
        sa.CheckConstraint(
            f"tenant_distribution_key_default IS NULL OR "
            f"tenant_distribution_key_default IN ({_in_check(DISTRIBUTION_KEYS)})",
            name="ck_cost_category_tenant_dist_key",
        ),
    )

    # --- Grant: read-only for app_rw (global system data) ---
    op.execute("GRANT SELECT ON cost_category TO app_rw")

    # --- Seed all SDD section 7 categories (plan_version = 1) ---
    for cat in SEED_CATEGORIES:
        cols = [
            "plan_version", "code", "label", "cost_group",
            "tenant_apportionability_default", "owner_billing_relevant_default",
            "owner_distribution_key_default", "tenant_distribution_key_default",
            "reserve_relevant", "legal_hint", "sort_order",
        ]
        vals = [
            "1",
            f"'{cat['code']}'",
            f"'{cat['label']}'",
            f"'{cat['cost_group']}'",
            f"'{cat['tenant_apportionability_default']}'",
            str(cat["owner_billing_relevant_default"]).lower(),
            f"'{cat['owner_distribution_key_default']}'" if cat["owner_distribution_key_default"] else "NULL",
            f"'{cat['tenant_distribution_key_default']}'" if cat["tenant_distribution_key_default"] else "NULL",
            str(cat["reserve_relevant"]).lower(),
            f"'{cat['legal_hint']}'" if cat["legal_hint"] else "NULL",
            str(cat["sort_order"]),
        ]
        op.execute(
            f"INSERT INTO cost_category ({', '.join(cols)}) "
            f"VALUES ({', '.join(vals)})"
        )


def downgrade() -> None:
    op.drop_table("cost_category")
