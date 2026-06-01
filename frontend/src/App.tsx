/**
 * Root application component.
 *
 * - Wraps everything in AuthProvider.
 * - If not authenticated, shows the Login page.
 * - If authenticated, shows the AppShell with routed content.
 */

import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { MandatePage } from "./pages/MandatePage";
import { UsersPage } from "./pages/UsersPage";
import { PropertiesPage } from "./pages/PropertiesPage";
import { PropertyDetailPage } from "./pages/PropertyDetailPage";
import { UnitDetailPage } from "./pages/UnitDetailPage";
import { PersonsPage } from "./pages/PersonsPage";
import { PersonDetailPage } from "./pages/PersonDetailPage";

function AppRoutes() {
  const { loading, session } = useAuth();

  if (loading) {
    return (
      <div style={{ padding: "2rem", textAlign: "center" }}>Lade...</div>
    );
  }

  if (!session) {
    return <LoginPage />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/mandate" element={<MandatePage />} />
        <Route path="/objekte" element={<PropertiesPage />} />
        <Route path="/objekte/:propertyId" element={<PropertyDetailPage />} />
        <Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} />
        <Route path="/personen" element={<PersonsPage />} />
        <Route path="/personen/:personId" element={<PersonDetailPage />} />
        <Route path="/users" element={<UsersPage />} />
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
