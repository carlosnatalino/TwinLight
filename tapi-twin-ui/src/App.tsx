import { createBrowserRouter, RouterProvider, Outlet } from "react-router-dom";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";
import ConnectionBanner from "@/components/common/ConnectionBanner";
import DashboardPage from "@/pages/DashboardPage";
import TopologyPage from "@/pages/TopologyPage";
import MonitoringPage from "@/pages/MonitoringPage";
import DeviceDetailPage from "@/pages/DeviceDetailPage";
import LinkDetailPage from "@/pages/LinkDetailPage";
import SettingsPage from "@/pages/SettingsPage";
import AddServicePage from "@/pages/AddServicePage";
import ServicesPage from "@/pages/ServicesPage";
import EquipmentPage from "@/pages/EquipmentPage";
import PathComputationPage from "@/pages/PathComputationPage";
import SpectrumPage from "@/pages/SpectrumPage";
import SpectrumGridPage from "@/pages/SpectrumGridPage";
import { useConnectionCheck } from "@/hooks/useConnectionCheck";

function RootLayout() {
  useConnectionCheck();
  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <ConnectionBanner />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "topology", element: <TopologyPage /> },
      { path: "topology/:topoId/node/:nodeId", element: <DeviceDetailPage /> },
      { path: "topology/:topoId/link/:linkId", element: <LinkDetailPage /> },
      { path: "monitoring", element: <MonitoringPage /> },
      { path: "services", element: <ServicesPage /> },
      { path: "services/new", element: <AddServicePage /> },
      { path: "equipment", element: <EquipmentPage /> },
      { path: "path", element: <PathComputationPage /> },
      { path: "spectrum", element: <SpectrumPage /> },
      { path: "spectrum/grid", element: <SpectrumGridPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
