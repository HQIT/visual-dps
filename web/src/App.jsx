import { Routes, Route } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import PageTitle from './components/PageTitle';
import AppLayout from './components/AppLayout';
import RequireAuth from './components/RequireAuth';
import DashboardPage from './pages/DashboardPage';
import MonitorPage from './pages/MonitorPage';
import AnnotatePage from './pages/AnnotatePage';
import LoginPage from './pages/LoginPage';
import SettingsPage from './pages/SettingsPage';
import MatrixPage from './pages/MatrixPage';
import TopologyPage from './pages/TopologyPage';
import BenchPage from './pages/BenchPage';
import BenchReplayPage from './pages/BenchReplayPage';

export default function App() {
  return (
    <AuthProvider>
      <PageTitle />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route path="/" element={<DashboardPage />} />
          <Route path="/matrix" element={<MatrixPage />} />
          <Route path="/topology" element={<TopologyPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/bench" element={<BenchPage />} />
          <Route path="/bench/:runId" element={<BenchReplayPage />} />
        <Route path="/annotate" element={<AnnotatePage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      </Routes>
    </AuthProvider>
  );
}
