import { type ReactNode } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CreditCard,
  LayoutDashboard,
  Settings,
  Shield,
} from "lucide-react";

const navigation = [
  { name: "Overview", href: "/", icon: LayoutDashboard },
  { name: "Alerts", href: "/alerts", icon: AlertTriangle },
  { name: "Executions", href: "/executions", icon: Activity },
  { name: "Integrations", href: "/integrations", icon: Shield },
  { name: "Monitors", href: "/monitors", icon: BarChart3 },
  { name: "Credentials", href: "/credentials", icon: CreditCard },
];

export default function DashboardLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <div className="flex h-screen bg-gray-100">
      {/* Sidebar */}
      <nav className="flex w-64 flex-col bg-white shadow">
        <div className="flex h-16 items-center px-6 border-b">
          <h1 className="text-xl font-bold text-gray-900">WRP</h1>
        </div>
        <ul className="flex-1 space-y-1 p-4">
          {navigation.map((item) => (
            <li key={item.name}>
              <Link
                href={item.href}
                className="flex items-center gap-3 rounded-lg px-3 py-2 text-gray-700 hover:bg-gray-100"
              >
                <item.icon className="h-5 w-5" />
                {item.name}
              </Link>
            </li>
          ))}
        </ul>
        <div className="border-t p-4">
          <Link
            href="/settings"
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-gray-700 hover:bg-gray-100"
          >
            <Settings className="h-5 w-5" />
            Settings
          </Link>
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-8">{children}</main>
    </div>
  );
}