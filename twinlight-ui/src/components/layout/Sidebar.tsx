import { NavLink } from "react-router-dom";
import { LayoutDashboard, Network, Activity, Settings, PlusCircle, List, Cpu, Route, Radio, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/cn";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/topology", label: "Topology", icon: Network, end: false },
  { to: "/monitoring", label: "Monitoring", icon: Activity, end: false },
  { to: "/services", label: "Services", icon: List, end: true },
  { to: "/services/new", label: "Add Service", icon: PlusCircle, end: false },
  { to: "/equipment", label: "Equipment", icon: Cpu, end: true },
  { to: "/path", label: "Path", icon: Route, end: true },
  { to: "/spectrum", label: "Spectrum", icon: Radio, end: true },
  { to: "/spectrum/grid", label: "Spectrum grid", icon: LayoutGrid, end: true },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
] as const;

export default function Sidebar() {
  return (
    <aside className="flex w-60 shrink-0 flex-col bg-slate-900 text-slate-300">
      {/* Brand */}
      <div className="flex h-14 items-center gap-2.5 px-4 border-b border-slate-700">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-blue-600">
          <Network className="h-4 w-4 text-white" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-white leading-tight">T-API Digital Twin</p>
          <p className="text-xs text-slate-400 truncate">Network Visualizer</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-3 space-y-1">
        {navItems.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-blue-600 text-white"
                  : "text-slate-300 hover:bg-slate-800 hover:text-white"
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-slate-700">
        <p className="text-xs text-slate-500">TwinLight</p>
        <p className="text-xs text-slate-600">v0.1.0</p>
      </div>
    </aside>
  );
}
