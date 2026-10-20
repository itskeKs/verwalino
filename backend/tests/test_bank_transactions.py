"""Tests for BankTransaction model, repository, and RLS.

T15: Bankumsätze Tabelle.
Abschluss: uv run pytest tests/test_rls_coverage.py tests/test_bank_transactions.py

Focus: Model constraints, Repository methods, RLS tenant isolation.
No API endpoint tests (not part of T15 scope).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

# =====================================================================
# Tests: BankTransaction model constraints
# =====================================================================

class TestBankTransactionModel:
    """Tests for BankTransaction model constraints and validations."""

    @pytest.mark.asyncio
    async def test_bank_transaction_signed_amounts_allowed(self, migrator_session, seed_data):
        """Bank transactions must allow both positive and negative amounts."""
        from app.models import BankTransaction

        # Check/create property and bank account for Org A
        result = await migrator_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_id, bank_account_id = row[1], row[0]
        else:
            property_id = uuid.uuid4()
            bank_account_id = uuid.uuid4()
            await migrator_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property', 'WEG')"
                ),
                {"pid": str(property_id), "oid": str(seed_data["org_a_id"])},
            )
            await migrator_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro')"
                ),
                {"bid": str(bank_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_id)},
            )
            await migrator_session.flush()

        # Create a transaction with negative amount (debit)
        tx = BankTransaction(
            organization_id=seed_data["org_a_id"],
            bank_account_id=bank_account_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("-100.50"),  # Negative = debit
            import_hash="test_hash_negative",
            status="NEU",
        )
        migrator_session.add(tx)
        await migrator_session.flush()

        # Verify it was saved with negative amount
        result = await migrator_session.execute(
            text("SELECT amount FROM bank_transaction WHERE import_hash = 'test_hash_negative'")
        )
        saved_amount = result.scalar()
        assert Decimal(saved_amount) == Decimal("-100.50")

    @pytest.mark.asyncio
    async def test_bank_transaction_positive_amount(self, migrator_session, seed_data):
        """Bank transactions must allow positive amounts (credits)."""
        from app.models import BankTransaction

        # Check/create property and bank account for Org A
        result = await migrator_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_id, bank_account_id = row[1], row[0]
        else:
            property_id = uuid.uuid4()
            bank_account_id = uuid.uuid4()
            await migrator_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property', 'WEG')"
                ),
                {"pid": str(property_id), "oid": str(seed_data["org_a_id"])},
            )
            await migrator_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro')"
                ),
                {"bid": str(bank_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_id)},
            )
            await migrator_session.flush()

        # Create a transaction with positive amount (credit)
        tx = BankTransaction(
            organization_id=seed_data["org_a_id"],
            bank_account_id=bank_account_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("500.00"),  # Positive = credit
            import_hash="test_hash_positive",
            status="NEU",
        )
        migrator_session.add(tx)
        await migrator_session.flush()

        # Verify it was saved with positive amount
        result = await migrator_session.execute(
            text("SELECT amount FROM bank_transaction WHERE import_hash = 'test_hash_positive'")
        )
        saved_amount = result.scalar()
        assert Decimal(saved_amount) == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_bank_transaction_unique_import_hash_per_account(
        self, migrator_session, seed_data
    ):
        """Duplicate import_hash on same bank_account must raise integrity error."""
        from app.models import BankTransaction

        # Check/create property and bank account for Org A
        result = await migrator_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_id, bank_account_id = row[1], row[0]
        else:
            property_id = uuid.uuid4()
            bank_account_id = uuid.uuid4()
            await migrator_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property', 'WEG')"
                ),
                {"pid": str(property_id), "oid": str(seed_data["org_a_id"])},
            )
            await migrator_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro')"
                ),
                {"bid": str(bank_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_id)},
            )
            await migrator_session.flush()

        # Create first transaction
        tx1 = BankTransaction(
            organization_id=seed_data["org_a_id"],
            bank_account_id=bank_account_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("100.00"),
            import_hash="unique_hash_123",
            status="NEU",
        )
        migrator_session.add(tx1)
        await migrator_session.flush()

        # Try to create duplicate on same account
        tx2 = BankTransaction(
            organization_id=seed_data["org_a_id"],
            bank_account_id=bank_account_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("200.00"),
            import_hash="unique_hash_123",  # Same hash, same account
            status="NEU",
        )
        migrator_session.add(tx2)

        with pytest.raises(IntegrityError):
            await migrator_session.flush()


# =====================================================================
# Tests: BankTransactionRepository
# =====================================================================

class TestBankTransactionRepository:
    """Tests for BankTransactionRepository methods."""

    @pytest.mark.asyncio
    async def test_list_by_account(self, app_rw_session, seed_data):
        """list_by_account must return transactions for a specific bank account."""
        from app.models import BankTransaction
        from app.repositories.bank_transaction_repo import BankTransactionRepository

        # Set tenant context for RLS
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text("SELECT set_config('app.current_user_id', :actor, true)"),
            {"actor": str(seed_data["user_a_admin_id"])},
        )

        # Check/create property and bank account for Org A
        result = await app_rw_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_id, bank_account_id = row[1], row[0]
        else:
            property_id = uuid.uuid4()
            bank_account_id = uuid.uuid4()
            await app_rw_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property', 'WEG')"
                ),
                {"pid": str(property_id), "oid": str(seed_data["org_a_id"])},
            )
            await app_rw_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro')"
                ),
                {"bid": str(bank_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_id)},
            )
            await app_rw_session.flush()

        repo = BankTransactionRepository(app_rw_session, seed_data["org_a_id"])

        # Create test transactions on a fresh account
        fresh_account_id = uuid.uuid4()
        fresh_property_id = uuid.uuid4()
        await app_rw_session.execute(
            text(
                "INSERT INTO property (id, organization_id, name, management_type) "
                "VALUES (:pid, :oid, 'Test Property Repo', 'WEG')"
            ),
            {"pid": str(fresh_property_id), "oid": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro Repo')"
            ),
            {"bid": str(fresh_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(fresh_property_id)},
        )
        await app_rw_session.flush()

        for i in range(3):
            tx = BankTransaction(
                organization_id=seed_data["org_a_id"],
                bank_account_id=fresh_account_id,
                booking_date=date.today() - timedelta(days=i),
                value_date=date.today() - timedelta(days=i),
                amount=Decimal(str(100 * (i + 1))),
                import_hash=f"repo_test_{i}",
                status="NEU",
            )
            app_rw_session.add(tx)
        await app_rw_session.flush()

        # Fetch transactions for this account
        results = await repo.list_by_account(fresh_account_id)

        assert len(results) == 3
        assert [tx.value_date for tx in results] == sorted(tx.value_date for tx in results)
        assert [tx.amount for tx in results] == [Decimal("300.00"), Decimal("200.00"), Decimal("100.00")]
        assert all(isinstance(tx, BankTransaction) for tx in results)

    @pytest.mark.asyncio
    async def test_list_by_import_hash(self, app_rw_session, seed_data):
        """list_by_import_hash must find transactions by account and hash."""
        from app.models import BankTransaction
        from app.repositories.bank_transaction_repo import BankTransactionRepository

        # Set tenant context for RLS
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text("SELECT set_config('app.current_user_id', :actor, true)"),
            {"actor": str(seed_data["user_a_admin_id"])},
        )

        # Check/create property and bank account for Org A
        result = await app_rw_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_id, bank_account_id = row[1], row[0]
        else:
            property_id = uuid.uuid4()
            bank_account_id = uuid.uuid4()
            await app_rw_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property', 'WEG')"
                ),
                {"pid": str(property_id), "oid": str(seed_data["org_a_id"])},
            )
            await app_rw_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro')"
                ),
                {"bid": str(bank_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_id)},
            )
            await app_rw_session.flush()

        repo = BankTransactionRepository(app_rw_session, seed_data["org_a_id"])

        # Create test transaction
        tx = BankTransaction(
            organization_id=seed_data["org_a_id"],
            bank_account_id=bank_account_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("150.00"),
            import_hash="lookup_hash_456",
            status="NEU",
        )
        app_rw_session.add(tx)
        await app_rw_session.flush()

        # Find by hash
        result = await repo.list_by_import_hash(bank_account_id, "lookup_hash_456")

        assert result is not None
        assert result.import_hash == "lookup_hash_456"

    @pytest.mark.asyncio
    async def test_list_unassigned(self, app_rw_session, seed_data):
        """list_unassigned must return only NEU status transactions."""
        from app.models import BankTransaction
        from app.repositories.bank_transaction_repo import BankTransactionRepository

        # Set tenant context for RLS
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text("SELECT set_config('app.current_user_id', :actor, true)"),
            {"actor": str(seed_data["user_a_admin_id"])},
        )

        # Check/create property and bank account for Org A
        result = await app_rw_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_id, bank_account_id = row[1], row[0]
        else:
            property_id = uuid.uuid4()
            bank_account_id = uuid.uuid4()
            await app_rw_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property', 'WEG')"
                ),
                {"pid": str(property_id), "oid": str(seed_data["org_a_id"])},
            )
            await app_rw_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro')"
                ),
                {"bid": str(bank_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_id)},
            )
            await app_rw_session.flush()

        repo = BankTransactionRepository(app_rw_session, seed_data["org_a_id"])

        # Create transactions with different statuses on a FRESH account to avoid contamination from previous tests
        fresh_property_id = uuid.uuid4()
        fresh_account_id = uuid.uuid4()
        await app_rw_session.execute(
            text(
                "INSERT INTO property (id, organization_id, name, management_type) "
                "VALUES (:pid, :oid, 'Test Property Unassigned', 'WEG')"
            ),
            {"pid": str(fresh_property_id), "oid": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro Unassigned')"
            ),
            {"bid": str(fresh_account_id), "oid": str(seed_data["org_a_id"]), "pid": str(fresh_property_id)},
        )
        await app_rw_session.flush()

        existing_neu_ids = set((await app_rw_session.execute(
            text("SELECT id FROM bank_transaction WHERE status = 'NEU'")
        )).scalars())
        expected_new_ids = set()
        # Create transactions with different statuses (unique hashes!)
        for i, status in enumerate(["NEU", "NEU", "ZUGEORDNET", "IGNORIERT"]):
            transaction_id = uuid.uuid4()
            if status == "NEU":
                expected_new_ids.add(transaction_id)
            tx = BankTransaction(
                id=transaction_id,
                organization_id=seed_data["org_a_id"],
                bank_account_id=fresh_account_id,
                booking_date=date.today(),
                value_date=date.today(),
                amount=Decimal("100.00"),
                import_hash=f"unassigned_{status}_{i}",  # Unique per entry
                status=status,
            )
            app_rw_session.add(tx)
        await app_rw_session.flush()

        # Fetch unassigned (NEU) transactions
        results = await repo.list_unassigned()

        # Must only return NEU status transactions
        assert all(tx.status == "NEU" for tx in results)
        
        assert {tx.id for tx in results} == existing_neu_ids | expected_new_ids
        assert len(results) == len(existing_neu_ids) + 2


# =====================================================================
# Tests: Tenant isolation (RLS)
# =====================================================================

class TestBankTransactionTenantIsolation:
    """Tests for BankTransaction tenant isolation (RLS)."""

    @pytest.mark.asyncio
    async def test_org_b_sees_only_org_b_transactions(self, app_rw_session, seed_data):
        """Transactions from Organization B must not appear for Organization A."""
        from app.models import BankTransaction
        from app.repositories.bank_transaction_repo import BankTransactionRepository

        # Set tenant context for Org A
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text("SELECT set_config('app.current_user_id', :actor, true)"),
            {"actor": str(seed_data["user_a_admin_id"])},
        )

        # Check/create property and bank account for Org A
        result = await app_rw_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_a_id"])},
        )
        row = result.first()

        if row:
            property_a_id, bank_account_a_id = row[1], row[0]
        else:
            property_a_id = uuid.uuid4()
            bank_account_a_id = uuid.uuid4()
            await app_rw_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property A', 'WEG')"
                ),
                {"pid": str(property_a_id), "oid": str(seed_data["org_a_id"])},
            )
            await app_rw_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro A')"
                ),
                {"bid": str(bank_account_a_id), "oid": str(seed_data["org_a_id"]), "pid": str(property_a_id)},
            )
            await app_rw_session.flush()

        # Create an Org A transaction
        repo_a = BankTransactionRepository(app_rw_session, seed_data["org_a_id"])
        tx_a = BankTransaction(
            organization_id=seed_data["org_a_id"],
            bank_account_id=bank_account_a_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("100.00"),
            import_hash="isolation_test_a",
            status="NEU",
        )
        app_rw_session.add(tx_a)
        # Flush tx_a under Org A context BEFORE switching to B
        await app_rw_session.flush()

        # Now switch tenant context to Org B
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_b_id"])},
        )
        await app_rw_session.execute(
            text("SELECT set_config('app.current_user_id', :actor, true)"),
            {"actor": str(seed_data["user_b_admin_id"])},
        )

        # Check/create property and bank account for Org B
        result = await app_rw_session.execute(
            text(
                "SELECT ba.id, p.id "
                "FROM bank_account ba "
                "JOIN property p ON p.id = ba.property_id "
                "WHERE ba.organization_id = :oid "
                "LIMIT 1"
            ),
            {"oid": str(seed_data["org_b_id"])},
        )
        row = result.first()

        if row:
            property_b_id, bank_account_b_id = row[1], row[0]
        else:
            property_b_id = uuid.uuid4()
            bank_account_b_id = uuid.uuid4()
            await app_rw_session.execute(
                text(
                    "INSERT INTO property (id, organization_id, name, management_type) "
                    "VALUES (:pid, :oid, 'Test Property B', 'WEG')"
                ),
                {"pid": str(property_b_id), "oid": str(seed_data["org_b_id"])},
            )
            await app_rw_session.execute(
                text(
                    "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                    "VALUES (:bid, :oid, :pid, 'LAUFEND', 'Test Giro B')"
                ),
                {"bid": str(bank_account_b_id), "oid": str(seed_data["org_b_id"]), "pid": str(property_b_id)},
            )
            await app_rw_session.flush()

        # Create an Org B transaction
        repo_b = BankTransactionRepository(app_rw_session, seed_data["org_b_id"])
        tx_b = BankTransaction(
            organization_id=seed_data["org_b_id"],
            bank_account_id=bank_account_b_id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("200.00"),
            import_hash="isolation_test_b",
            status="NEU",
        )
        app_rw_session.add(tx_b)

        # Flush both transactions
        await app_rw_session.flush()

        # Now query as Org A - should NOT see Org B transactions
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        await app_rw_session.execute(
            text("SELECT set_config('app.current_user_id', :actor, true)"),
            {"actor": str(seed_data["user_a_admin_id"])},
        )

        repo_a_final = BankTransactionRepository(app_rw_session, seed_data["org_a_id"])
        results_a = await repo_a_final.list_by_account(bank_account_a_id)

        # Should only see Org A transactions, not Org B's
        org_b_transactions = [
            tx for tx in results_a
            if tx.import_hash == "isolation_test_b"
        ]
        assert len(org_b_transactions) == 0, (
            f"Org A should not see Org B transactions, but found "
            f"{len(org_b_transactions)}"
        )

        # And should see its own
        assert len(results_a) >= 1


async def _fresh_account(session, seed_data, tenant="a"):
    org_id = seed_data[f"org_{tenant}_id"]
    await session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)})
    await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {"actor": str(seed_data[f"user_{tenant}_admin_id"])})
    property_id, account_id = uuid.uuid4(), uuid.uuid4()
    await session.execute(text(
        "INSERT INTO property (id, organization_id, name, management_type) "
        "VALUES (:id, :org, 'T15 synthetisch', 'WEG')"
    ), {"id": property_id, "org": org_id})
    await session.execute(text(
        "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
        "VALUES (:id, :org, :property, 'LAUFEND', 'T15 synthetisch')"
    ), {"id": account_id, "org": org_id, "property": property_id})
    return property_id, account_id


def _transaction(org_id, account_id, import_hash="same-statement-line"):
    from app.models import BankTransaction
    return BankTransaction(
        organization_id=org_id, bank_account_id=account_id,
        booking_date=date(2026, 1, 2), value_date=date(2026, 1, 3),
        amount=Decimal("123.45"), import_hash=import_hash, status="NEU",
    )


@pytest.mark.asyncio
async def test_transaction_rejects_foreign_organization_account(migrator_session, seed_data):
    _, account_b = await _fresh_account(migrator_session, seed_data, "b")
    with pytest.raises(IntegrityError, match="fk_bank_transaction_account_org"):
        async with migrator_session.begin_nested():
            migrator_session.add(_transaction(seed_data["org_a_id"], account_b))
            await migrator_session.flush()


@pytest.mark.asyncio
async def test_payment_link_requires_same_account(migrator_session, seed_data):
    from app.models import OwnerProfile, Payment, Person
    property_a, account_a = await _fresh_account(migrator_session, seed_data)
    _, account_other = await _fresh_account(migrator_session, seed_data)
    tx = _transaction(seed_data["org_a_id"], account_other)
    person = Person(organization_id=seed_data["org_a_id"], last_name="T15 synthetisch")
    migrator_session.add_all([tx, person])
    await migrator_session.flush()
    owner = OwnerProfile(organization_id=seed_data["org_a_id"], person_id=person.id)
    migrator_session.add(owner)
    await migrator_session.flush()
    payment = Payment(
        organization_id=seed_data["org_a_id"], bank_account_id=account_a,
        property_id=property_a, direction="IN", amount=Decimal("123.45"),
        value_date=date(2026, 1, 3), payment_type="HAUSGELD_VORSCHUSS",
        owner_profile_id=owner.id, status="draft",
    )
    migrator_session.add(payment)
    await migrator_session.flush()
    assert payment.bank_transaction_id is None
    with pytest.raises(IntegrityError, match="fk_payment_bank_transaction_coherence"):
        async with migrator_session.begin_nested():
            payment.bank_transaction_id = tx.id
            await migrator_session.flush()
    await migrator_session.refresh(payment)
    valid_tx = _transaction(seed_data["org_a_id"], account_a)
    migrator_session.add(valid_tx)
    await migrator_session.flush()
    payment.bank_transaction_id = valid_tx.id
    await migrator_session.flush()
    await migrator_session.refresh(payment)
    assert payment.bank_transaction_id == valid_tx.id
    payment.status = "booked"
    await migrator_session.flush()
    with pytest.raises(DBAPIError, match="Cannot change bank_transaction_id on booked payment"):
        async with migrator_session.begin_nested():
            payment.bank_transaction_id = None
            await migrator_session.flush()


@pytest.mark.asyncio
async def test_runtime_rls_without_repository_filter(app_rw_session, seed_data):
    session = app_rw_session
    tx_ids = {}
    accounts = {}
    for tenant in ("a", "b"):
        _, account = await _fresh_account(session, seed_data, tenant)
        accounts[tenant] = account
        tx = _transaction(seed_data[f"org_{tenant}_id"], account)
        session.add(tx)
        await session.flush()
        tx_ids[tenant] = tx.id
    for tenant, other in (("a", "b"), ("b", "a")):
        await session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(seed_data[f"org_{tenant}_id"])})
        await session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {"actor": str(seed_data[f"user_{tenant}_admin_id"])})
        rows = (await session.execute(text("SELECT id, organization_id FROM bank_transaction"))).all()
        assert tx_ids[tenant] in {row.id for row in rows}
        assert tx_ids[other] not in {row.id for row in rows}
        assert all(row.organization_id == seed_data[f"org_{tenant}_id"] for row in rows)
        updated = await session.execute(text("UPDATE bank_transaction SET status='IGNORIERT' WHERE id=:id RETURNING id"), {"id": tx_ids[other]})
        assert updated.all() == []
        deleted = await session.execute(text("DELETE FROM bank_transaction WHERE id=:id RETURNING id"), {"id": tx_ids[other]})
        assert deleted.all() == []
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin_nested():
                session.add(_transaction(seed_data[f"org_{other}_id"], accounts[other], uuid.uuid4().hex))
                await session.flush()


@pytest.mark.asyncio
async def test_bank_transaction_rejects_unknown_status(app_rw_session, seed_data):
    _, account = await _fresh_account(app_rw_session, seed_data)
    tx = _transaction(seed_data["org_a_id"], account)
    tx.status = "UNBEKANNT"
    with pytest.raises(IntegrityError, match="ck_bank_transaction_status"):
        async with app_rw_session.begin_nested():
            app_rw_session.add(tx)
            await app_rw_session.flush()
