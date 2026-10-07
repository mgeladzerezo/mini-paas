import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { AppPage } from "./pages/AppPage";
import { AppsPage } from "./pages/AppsPage";
import { GithubCallbackPage } from "./pages/GithubCallbackPage";
import { LoginPage } from "./pages/LoginPage";
import { NewAppPage } from "./pages/NewAppPage";

export function App() {
  const { isAuthenticated } = useAuth();

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={isAuthenticated ? <Layout /> : <Navigate to="/login" replace />}>
        <Route index element={<AppsPage />} />
        <Route path="apps/new" element={<NewAppPage />} />
        <Route path="apps/:appId" element={<AppPage />} />
        <Route path="github/callback" element={<GithubCallbackPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
