"""Tests for BankTransaction model, repository, and API endpoints.

T15: Bankumsätze Tabelle.
Abschluss: uv run pytest tests/test_rls_coverage.py tests/test_bank_transactions.py
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import create_app
from app.api.auth import get_current_user, get_session_factory
from app.core.security import hash_password
from app.repositories.bank_transaction_repo import BankTransactionRepository
from app.models import BankTransaction, BankAccount, Organization, User


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture(scope="module")
def event_loop():
    """Create module-scoped event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session(seed_data):
    """Create a database session with seed data for testing."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import sessionmaker
    
    factory = async_sessionmaker(seed_data["engine"], class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client_a_admin(db_session):
    """HTTP client authenticated as Organization A admin."""
    from tests.conftest import org_a_id, user_a_admin_id
    
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        # Login as admin
        response = await client.post("/api/auth/login", json={
            "email": "admin@alpha.de",
            "password": "testpass1234",
        })
        assert response.status_code == 200
        
        yield client


@pytest_asyncio.fixture
async def client_unauth():
    """HTTP client without authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        yield client


# =====================================================================
# Tests: BankTransaction model constraints
# =====================================================================

class TestBankTransactionModel:
    """Tests for BankTransaction model constraints and validations."""

    @pytest.mark.asyncio
    async def test_bank_transaction_signed_amounts_allowed(self, db_session):
        """Bank transactions must allow both positive and negative amounts."""
        org = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        bank_account = await db_session.execute(
            select(BankAccount).where(BankAccount.organization_id == org.id).limit(1)
        ).scalar_one_or_none()
        
        # Create a transaction with negative amount (debit)
        tx = BankTransaction(
            organization_id=org.id,
            bank_account_id=bank_account.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("-100.50"),  # Negative = debit
            import_hash="test_hash_negative",
            status="NEU",
        )
        db_session.add(tx)
        await db_session.commit()
        
        # Verify it was saved with negative amount
        result = await db_session.execute(
            select(BankTransaction).where(BankTransaction.import_hash == "test_hash_negative")
        )
        saved = result.scalar_one()
        assert saved.amount == Decimal("-100.50")

    @pytest.mark.asyncio
    async def test_bank_transaction_positive_amount(self, db_session):
        """Bank transactions must allow positive amounts (credits)."""
        org = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        bank_account = await db_session.execute(
            select(BankAccount).where(BankAccount.organization_id == org.id).limit(1)
        ).scalar_one_or_none()
        
        # Create a transaction with positive amount (credit)
        tx = BankTransaction(
            organization_id=org.id,
            bank_account_id=bank_account.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("500.00"),  # Positive = credit
            import_hash="test_hash_positive",
            status="NEU",
        )
        db_session.add(tx)
        await db_session.commit()
        
        # Verify it was saved with positive amount
        result = await db_session.execute(
            select(BankTransaction).where(BankTransaction.import_hash == "test_hash_positive")
        )
        saved = result.scalar_one()
        assert saved.amount == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_bank_transaction_unique_import_hash_per_account(self, db_session):
        """Duplicate import_hash on same bank_account must raise integrity error."""
        org = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        bank_account = await db_session.execute(
            select(BankAccount).where(BankAccount.organization_id == org.id).limit(1)
        ).scalar_one_or_none()
        
        # Create first transaction
        tx1 = BankTransaction(
            organization_id=org.id,
            bank_account_id=bank_account.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("100.00"),
            import_hash="unique_hash_123",
            status="NEU",
        )
        db_session.add(tx1)
        await db_session.commit()
        
        # Try to create duplicate on same account
        tx2 = BankTransaction(
            organization_id=org.id,
            bank_account_id=bank_account.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("200.00"),
            import_hash="unique_hash_123",  # Same hash, same account
            status="NEU",
        )
        db_session.add(tx2)
        
        with pytest.raises(Exception):  # IntegrityError
            await db_session.commit()


# =====================================================================
# Tests: BankTransactionRepository
# =====================================================================

class TestBankTransactionRepository:
    """Tests for BankTransactionRepository methods."""

    @pytest.mark.asyncio
    async def test_list_by_account(self, db_session):
        """list_by_account must return transactions for a specific bank account."""
        from app.repositories.bank_transaction_repo import BankTransactionRepository
        
        org = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        bank_account = await db_session.execute(
            select(BankAccount).where(BankAccount.organization_id == org.id).limit(1)
        ).scalar_one_or_none()
        
        repo = BankTransactionRepository(db_session)
        
        # Create test transactions
        for i in range(3):
            tx = BankTransaction(
                organization_id=org.id,
                bank_account_id=bank_account.id,
                booking_date=date.today() - timedelta(days=i),
                value_date=date.today() - timedelta(days=i),
                amount=Decimal(str(100 * (i + 1))),
                import_hash=f"repo_test_{i}",
                status="NEU",
            )
            db_session.add(tx)
        await db_session.commit()
        
        # Fetch transactions for this account
        results = await repo.list_by_account(bank_account.id)
        
        assert len(results) == 3
        # Should be sorted by value_date descending, then created_at
        assert all(isinstance(tx, BankTransaction) for tx in results)

    @pytest.mark.asyncio
    async def test_list_by_import_hash(self, db_session):
        """list_by_import_hash must find transactions by account and hash."""
        from app.repositories.bank_transaction_repo import BankTransactionRepository
        
        org = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        bank_account = await db_session.execute(
            select(BankAccount).where(BankAccount.organization_id == org.id).limit(1)
        ).scalar_one_or_none()
        
        repo = BankTransactionRepository(db_session)
        
        # Create test transaction
        tx = BankTransaction(
            organization_id=org.id,
            bank_account_id=bank_account.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("150.00"),
            import_hash="lookup_hash_456",
            status="NEU",
        )
        db_session.add(tx)
        await db_session.commit()
        
        # Find by hash
        result = await repo.list_by_import_hash(bank_account.id, "lookup_hash_456")
        
        assert result is not None
        assert result.import_hash == "lookup_hash_456"

    @pytest.mark.asyncio
    async def test_list_unassigned(self, db_session):
        """list_unassigned must return only NEU status transactions."""
        from app.repositories.bank_transaction_repo import BankTransactionRepository
        
        org = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        bank_account = await db_session.execute(
            select(BankAccount).where(BankAccount.organization_id == org.id).limit(1)
        ).scalar_one_or_none()
        
        repo = BankTransactionRepository(db_session)
        
        # Create transactions with different statuses
        for status in ["NEU", "NEU", "ZUGEORDNET", "IGNORIERT"]:
            tx = BankTransaction(
                organization_id=org.id,
                bank_account_id=bank_account.id,
                booking_date=date.today(),
                value_date=date.today(),
                amount=Decimal("100.00"),
                import_hash=f"unassigned_{status}",
                status=status,
            )
            db_session.add(tx)
        await db_session.commit()
        
        # Fetch unassigned (NEU) transactions
        results = await repo.list_unassigned()
        
        # Must only return NEU status transactions
        assert all(tx.status == "NEU" for tx in results)
        assert len(results) == 2


# =====================================================================
# Tests: API endpoints
# =====================================================================

class TestBankTransactionAPI:
    """Tests for BankTransaction API endpoints."""

    @pytest.mark.asyncio
    async def test_list_bank_transactions_authenticated(self, client_a_admin, db_session):
        """Authenticated users must be able to list bank transactions."""
        response = await client_a_admin.get("/api/bank-transactions")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_list_bank_transactions_unauthenticated(self, client_unauth):
        """Unauthenticated users must receive 401 for bank transactions."""
        response = await client_unauth.get("/api/bank-transactions")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_bank_transaction(self, client_a_admin, db_session):
        """Authenticated users must be able to create bank transactions."""
        from tests.conftest import org_a_id, bank_account_a_id
        
        response = await client_a_admin.post("/api/bank-transactions", json={
            "bank_account_id": str(bank_account_a_id),
            "booking_date": date.today().isoformat(),
            "value_date": date.today().isoformat(),
            "amount": "-50.00",
            "import_hash": "api_create_test_789",
            "counterparty_name": "Test Counterparty",
            "counterparty_iban": "DE12345678901234567890",
            "purpose": "Test transaction",
        })
        
        assert response.status_code == 201
        
        # Verify it was persisted
        result = await db_session.execute(
            select(BankTransaction).where(
                BankTransaction.import_hash == "api_create_test_789"
            )
        )
        saved = result.scalar_one()
        assert saved.amount == Decimal("-50.00")
        assert saved.counterparty_name == "Test Counterparty"

    @pytest.mark.asyncio
    async def test_create_duplicate_import_hash_same_account(self, client_a_admin, db_session):
        """Creating duplicate import_hash on same account must raise 409."""
        from tests.conftest import bank_account_a_id
        
        # Create first transaction
        await client_a_admin.post("/api/bank-transactions", json={
            "bank_account_id": str(bank_account_a_id),
            "booking_date": date.today().isoformat(),
            "value_date": date.today().isoformat(),
            "amount": "100.00",
            "import_hash": "duplicate_hash_test",
        })
        
        # Try to create duplicate
        response = await client_a_admin.post("/api/bank-transactions", json={
            "bank_account_id": str(bank_account_a_id),
            "booking_date": date.today().isoformat(),
            "value_date": date.today().isoformat(),
            "amount": "200.00",
            "import_hash": "duplicate_hash_test",  # Same hash
        })
        
        assert response.status_code == 409  # Conflict


# =====================================================================
# Tests: Tenant isolation
# =====================================================================

class TestBankTransactionTenantIsolation:
    """Tests for BankTransaction tenant isolation (RLS)."""

    @pytest.mark.asyncio
    async def test_org_b_sees_only_org_b_transactions(self, db_session):
        """Transactions from Organization B must not appear for Organization A."""
        from app.repositories.bank_transaction_repo import BankTransactionRepository
        
        org_a = await db_session.execute(
            select(Organization).where(Organization.email == "alpha@test.de")
        ).scalar_one_or_none()
        
        org_b = await db_session.execute(
            select(Organization).where(Organization.email == "beta@test.de")
        ).scalar_one_or_none()
        
        bank_account_a = await db_session.execute(
            select(BankAccount).where(
                BankAccount.organization_id == org_a.id
            ).limit(1)
        ).scalar_one_or_none()
        
        bank_account_b = await db_session.execute(
            select(BankAccount).where(
                BankAccount.organization_id == org_b.id
            ).limit(1)
        ).scalar_one_or_none()
        
        repo = BankTransactionRepository(db_session)
        
        # Create transaction for Org A
        tx_a = BankTransaction(
            organization_id=org_a.id,
            bank_account_id=bank_account_a.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("100.00"),
            import_hash="org_a_tx",
            status="NEU",
        )
        db_session.add(tx_a)
        
        # Create transaction for Org B
        tx_b = BankTransaction(
            organization_id=org_b.id,
            bank_account_id=bank_account_b.id,
            booking_date=date.today(),
            value_date=date.today(),
            amount=Decimal("200.00"),
            import_hash="org_b_tx",
            status="NEU",
        )
        db_session.add(tx_b)
        
        await db_session.commit()
        
        # Org A should only see its own transactions
        results_a = await repo.list_by_account(bank_account_a.id)
        org_a_hashes = {tx.import_hash for tx in results_a}
        assert "org_a_tx" in org_a_hashes
        assert "org_b_tx" not in org_a_hashes
