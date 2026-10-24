import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Shell } from "./components/Shell";
import { Spinner } from "./components/ui";
import { can, type Area } from "./permissions";
import { AccountPage } from "./pages/AccountPage";
import { AnalysesPage } from "./pages/AnalysesPage";
import { AnalysisPage } from "./pages/AnalysisPage";
import { ChatPage } from "./pages/ChatPage";
import { CompareListPage, ComparisonPage } from "./pages/ComparePage";
import { DashboardPage } from "./pages/DashboardPage";
import { GitConnectionsPage } from "./pages/GitConnectionsPage";
import { LoginPage } from "./pages/LoginPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { RepositoryPage } from "./pages/RepositoryPage";
import { AuditLogPage } from "./pages/settings/AuditLogPage";
import { DatabasePage } from "./pages/settings/DatabasePage";
import { LlmPage } from "./pages/settings/LlmPage";
import { RolesPage } from "./pages/settings/RolesPage";
import { SettingsLayout } from "./pages/settings/SettingsLayout";
import { UsersPage } from "./pages/settings/UsersPage";

function RequireAuth() {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-6"><Spinner label="Loading…" /></div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

function RequireArea({ areas }: { areas: Area[] }) {
  const { user } = useAuth();
  if (!areas.every((area) => can(user, area, "read"))) return <Navigate to="/" replace />;
  return <Outlet />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Shell />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/git-connections" element={<GitConnectionsPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route element={<RequireArea areas={["repositories"]} />}>
            <Route path="/repositories" element={<RepositoriesPage />} />
            <Route path="/repositories/:repoId" element={<RepositoryPage />} />
            <Route path="/analyses" element={<AnalysesPage />} />
            <Route path="/analyses/:analysisId" element={<AnalysisPage />} />
          </Route>
          <Route element={<RequireArea areas={["repositories", "insights"]} />}>
            <Route path="/compare" element={<CompareListPage />} />
            <Route path="/compare/:comparisonId" element={<ComparisonPage />} />
            <Route path="/chat" element={<ChatPage />} />
          </Route>
          <Route element={<RequireArea areas={["settings"]} />}>
            <Route path="/settings" element={<SettingsLayout />}>
              <Route index element={<Navigate to="/settings/llm" replace />} />
              <Route path="llm" element={<LlmPage />} />
              <Route path="users" element={<UsersPage />} />
              <Route path="roles" element={<RolesPage />} />
              <Route path="database" element={<DatabasePage />} />
              <Route path="audit" element={<AuditLogPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
