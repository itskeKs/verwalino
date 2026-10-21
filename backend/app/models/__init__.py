from app.models.base import Base
from app.models.organization import Organization
from app.models.user import User
from app.models.membership import Membership
from app.models.management_mandate import ManagementMandate
from app.models.audit_event import AuditEvent

# M1 Stammdaten
from app.models.property import Property
from app.models.unit import Unit
from app.models.person import Person
from app.models.owner_profile import OwnerProfile
from app.models.tenant_profile import TenantProfile
from app.models.vendor import Vendor
from app.models.ownership_history import OwnershipHistory
from app.models.lease_agreement import LeaseAgreement
from app.models.lease_rent_change import LeaseRentChange
from app.models.bank_account import BankAccount
from app.models.bank_transaction import BankTransaction
from app.models.meter import Meter, MeterReading

# M2 Kosten & GoBD-Belegfluss
from app.models.cost_category import CostCategory
from app.models.cost_entry import CostEntry
from app.models.invoice_document import InvoiceDocument
from app.models.document_ref import DocumentRef

# M3 Verteilungs-/Abrechnungs-Engine (Betriebskostenabrechnung Mieter)
from app.models.settlement_run import SettlementRun
from app.models.settlement_line import SettlementLine

# M4 WEG-Abrechnung (Wirtschaftsplan eigene Entitaet; Jahresabrechnung +
# Vermoegensbericht als settlement_run run_types)
from app.models.economic_plan import EconomicPlan
from app.models.economic_plan_line import EconomicPlanLine

# M6 Zahlungen, Mietkonto & offene Posten
from app.models.payment import Payment

# M7 Mahnwesen
from app.models.dunning_notice import DunningNotice

# M8 Eigentümerversammlung & Beschluss-Sammlung
from app.models.owners_meeting import OwnersMeeting
from app.models.agenda_item import AgendaItem
from app.models.resolution import Resolution
from app.models.resolution_register_entry import ResolutionRegisterEntry

# Mandate documents (Verträge & Unterlagen am Mandat)
from app.models.mandate_document import MandateDocument
from app.models.attachment import Attachment
from app.models.task import Task
from app.models.letter import LetterTemplate, LetterDocument
from app.models.sepa import SepaCreditor, SepaMandate

# Declaration import legal source and review graph
from app.models.property_document import PropertyDocument
from app.models.declaration_import import (
    DeclarationImport,
    DeclarationImportDocument,
    DeclarationExtractionRun,
    DeclarationImportPass,
)
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.models.property_legal_reference import PropertyLegalReference
from app.models.property_assignment import PropertyAssignment
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_rule import PropertyRule, PropertyRuleRelation, PropertyRuleSource
from app.models.approved_fact_source import ApprovedFactSource
from app.models.property_knowledge_query_run import PropertyKnowledgeQueryRun

__all__ = [
    "Base",
    "Organization",
    "User",
    "Membership",
    "ManagementMandate",
    "AuditEvent",
    # M1
    "Property",
    "Unit",
    "Person",
    "MasterImportBatch",
    "OwnerProfile",
    "TenantProfile",
    "Vendor",
    "OwnershipHistory",
    "LeaseAgreement",
    "LeaseRentChange",
    "BankAccount",
    "Meter",
    "MeterReading",
    # M2
    "CostCategory",
    "CostEntry",
    "InvoiceDocument",
    "DocumentRef",
    # M3
    "SettlementRun",
    "SettlementLine",
    # M4
    "EconomicPlan",
    "EconomicPlanLine",
    # M6
    "Payment",
    # M7
    "DunningNotice",
    # M8
    "OwnersMeeting",
    "AgendaItem",
    "Resolution",
    "ResolutionRegisterEntry",
    "MandateDocument",
    "Attachment",
    "Task",
    "LetterTemplate",
    "LetterDocument",
    "SepaCreditor",
    "SepaMandate",
    "PropertyDocument",
    "DeclarationImport",
    "DeclarationImportDocument",
    "DeclarationExtractionRun",
    "DeclarationImportPass",
    "DeclarationCandidate",
    "DeclarationCandidateEvidence",
    "DeclarationCandidateIssue",
    "PropertyLegalReference",
    "PropertyAssignment",
    "PropertyDocumentRelation",
    "PropertyRule",
    "PropertyRuleRelation",
    "PropertyRuleSource",
    "ApprovedFactSource",
    "PropertyKnowledgeQueryRun",
]

from app.models.operation_receipt import OperationReceipt
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt
from app.models.coverage_declaration import CoverageDeclaration

from app.models.accounting_scope import AccountingScope, AccountingScopeUnit, AccountingScopeBinding, ScopeMigrationException
from app.models.scope_grant import ScopeGrant

from app.models.dunning_scope_context import DunningScopeContext

from app.models.scope_payment_reconciliation import ScopePaymentReconciliation, ScopePaymentReconciliationItem

from app.models.property_data_domain import PropertyDataDomain, PropertyDataGrant
from app.models.person_data_domain import PersonDataDomain, PersonDataGrant

from app.models.property_source_admission import PropertySourceAdmission
from app.models.property_source_admission_review import PropertySourceAdmissionReview

from app.models.property_source_classification import PropertySourceClassification

from app.models.property_creation_receipt import PropertyCreationReceipt
from app.models.master_import_batch import MasterImportBatch

from app.models.intake_original import IntakeOriginal

from app.models.special_levy import SpecialLevy, SpecialLevyAllocation
