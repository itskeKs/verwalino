/**
 * Root application component.
 *
 * - Wraps everything in AuthProvider.
 * - If not authenticated, shows the Login page.
 * - If authenticated, shows the AppShell with routed content.
 */

import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { useAuth } from "./auth/useAuth";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { MandatePage } from "./pages/MandatePage";
import { MandateQuarantinePage } from "./pages/MandateQuarantinePage";
import { AccessPreparationPage } from "./pages/AccessPreparationPage";
import { SourceClassificationsPage } from "./pages/SourceClassificationsPage";
import { SourceAdmissionReviewsPage } from "./pages/SourceAdmissionReviewsPage";
import { PersonDataDomainsPage } from "./pages/PersonDataDomainsPage";
import { PropertyDataDomainsPage } from "./pages/PropertyDataDomainsPage";
import { AccountingScopesPage } from "./pages/AccountingScopesPage";
import { UsersPage } from "./pages/UsersPage";
import { PropertiesPage } from "./pages/PropertiesPage";
import { PropertyDetailPage } from "./pages/PropertyDetailPage";
import { UnitDetailPage } from "./pages/UnitDetailPage";
import { PersonsPage } from "./pages/PersonsPage";
import { PersonDetailPage } from "./pages/PersonDetailPage";
import { CostEntriesPage } from "./pages/CostEntriesPage";
import { CostEntryDetailPage } from "./pages/CostEntryDetailPage";
import { InvoiceDocumentsPage } from "./pages/InvoiceDocumentsPage";
import { InvoiceDocumentDetailPage } from "./pages/InvoiceDocumentDetailPage";
import { SettlementsPage } from "./pages/SettlementsPage";
import { SettlementDetailPage } from "./pages/SettlementDetailPage";
import { EconomicPlansPage } from "./pages/EconomicPlansPage";
import { EconomicPlanDetailPage } from "./pages/EconomicPlanDetailPage";
import { WegSettlementsPage } from "./pages/WegSettlementsPage";
import { WegSettlementDetailPage } from "./pages/WegSettlementDetailPage";
import { AssetReportsPage } from "./pages/AssetReportsPage";
import { AssetReportDetailPage } from "./pages/AssetReportDetailPage";
import { BankAccountsPage } from "./pages/BankAccountsPage";
import { PaymentsPage } from "./pages/PaymentsPage";
import { PaymentDetailPage } from "./pages/PaymentDetailPage";
import { MietkontoPage } from "./pages/MietkontoPage";
import { OpenItemsPage } from "./pages/OpenItemsPage";
import { DunningNoticesPage } from "./pages/DunningNoticesPage";
import { DunningNoticeDetailPage } from "./pages/DunningNoticeDetailPage";
import { MeetingsPage } from "./pages/MeetingsPage";
import { MeetingDetailPage } from "./pages/MeetingDetailPage";
import { ResolutionRegisterPage } from "./pages/ResolutionRegisterPage";
import { FristenmonitorPage } from "./pages/FristenmonitorPage";
import { MandateDetailPage } from "./pages/MandateDetailPage";
import { DeclarationImportPage } from "./pages/DeclarationImportPage";
import { DeclarationReviewPage } from "./pages/DeclarationReviewPage";
import { PropertyKnowledgePage } from "./pages/PropertyKnowledgePage";

export function AppRoutes() {
  const { loading, session } = useAuth();

  if (loading) {
    return (
      <div style={{ padding: "2rem", textAlign: "center" }}>Lade...</div>
    );
  }

  if (!session) {
    return <LoginPage />;
  }

  const canWriteDeclarations = session.role === "OWNER_ADMIN" || session.role === "CLERK";

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/mandate" element={<MandatePage />} />
        <Route path="/mandate/:mandateId" element={<MandateDetailPage />} />
        <Route path="/objekte" element={<PropertiesPage />} />
        <Route path="/teilungserklaerung/import" element={canWriteDeclarations ? <DeclarationImportPage /> : <Navigate to="/objekte" replace />} />
        <Route path="/teilungserklaerung/import/:importId" element={canWriteDeclarations ? <DeclarationImportPage /> : <Navigate to="/objekte" replace />} />
        <Route path="/teilungserklaerung/import/:importId/review" element={canWriteDeclarations ? <DeclarationReviewPage /> : <Navigate to="/objekte" replace />} />
        <Route path="/objekte/:propertyId" element={<PropertyDetailPage />} />
        <Route path="/objekte/:propertyId/wissen" element={<PropertyKnowledgePage />} />
        <Route path="/objekte/:propertyId/teilungserklaerung/import" element={canWriteDeclarations ? <DeclarationImportPage /> : <Navigate to="/objekte" replace />} />
        <Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} />
        <Route path="/personen" element={<PersonsPage />} />
        <Route path="/personen/:personId" element={<PersonDetailPage />} />
        <Route path="/kosten" element={<CostEntriesPage />} />
        <Route path="/kosten/:entryId" element={<CostEntryDetailPage />} />
        <Route path="/belege" element={<InvoiceDocumentsPage />} />
        <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
        <Route path="/abrechnungen" element={<SettlementsPage />} />
        <Route path="/abrechnungen/:runId" element={<SettlementDetailPage />} />
        <Route path="/wirtschaftsplaene" element={<EconomicPlansPage />} />
        <Route path="/wirtschaftsplaene/:planId" element={<EconomicPlanDetailPage />} />
        <Route path="/weg-abrechnungen" element={<WegSettlementsPage />} />
        <Route path="/weg-abrechnungen/:runId" element={<WegSettlementDetailPage />} />
        <Route path="/vermoegensberichte" element={<AssetReportsPage />} />
        <Route path="/vermoegensberichte/:runId" element={<AssetReportDetailPage />} />
        <Route path="/bankkonten" element={<BankAccountsPage />} />
        <Route path="/zahlungen" element={<PaymentsPage />} />
        <Route path="/zahlungen/:paymentId" element={<PaymentDetailPage />} />
        <Route path="/mietkonto" element={<MietkontoPage />} />
        <Route path="/offene-posten" element={<OpenItemsPage />} />
        <Route path="/mahnungen" element={<DunningNoticesPage />} />
        <Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} />
        <Route path="/versammlungen" element={<MeetingsPage />} />
        <Route path="/versammlungen/:meetingId" element={<MeetingDetailPage />} />
        <Route path="/beschluss-sammlung" element={<ResolutionRegisterPage />} />
        <Route path="/fristen" element={<FristenmonitorPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/mandatsquarantaene" element={<MandateQuarantinePage />} />
        <Route path="/freigaben-vorbereiten" element={<AccessPreparationPage />} />
        <Route path="/altquellen" element={<SourceClassificationsPage />} />
        <Route path="/altquellen/:reviewId" element={<SourceClassificationsPage />} />
        <Route path="/quellenfreigaben" element={<SourceAdmissionReviewsPage />} />
        <Route path="/quellenfreigaben/:reviewId" element={<SourceAdmissionReviewsPage />} />
        <Route path="/kontaktdaten-rechte" element={<PersonDataDomainsPage />} />
        <Route path="/kontaktdaten-rechte/:domainId" element={<PersonDataDomainsPage />} />
        <Route path="/gebaeudedaten-rechte" element={<PropertyDataDomainsPage />} />
        <Route path="/gebaeudedaten-rechte/:domainId" element={<PropertyDataDomainsPage />} />
        <Route path="/berechtigungen" element={<AccountingScopesPage />} />
        <Route path="/berechtigungen/:scopeId" element={<AccountingScopesPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
