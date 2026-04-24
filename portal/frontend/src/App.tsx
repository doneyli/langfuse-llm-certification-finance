import { Navigate, Route, Routes } from "react-router-dom";

import AppShell from "./components/AppShell";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Breakdown from "./pages/Breakdown";
import RunDetail from "./pages/RunDetail";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/history/:dataset/:sub" element={<History />} />
        <Route path="/breakdown/:dataset/:sub/:runName" element={<Breakdown />} />
        <Route path="/run/:dataset/:sub/:runName" element={<RunDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
