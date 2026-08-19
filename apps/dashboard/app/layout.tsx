import type { Metadata } from "next";
import { AppShell } from "../components/AppShell";
import { TopHeader } from "../components/TopHeader";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentGuard — Autonomous AI Spend Governance",
  description:
    "Enterprise infrastructure spending firewall for autonomous AI agents — financial authorization before inference dispatch.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <AppShell>
            <div className="main-viewport">
              <TopHeader />
              <div className="page-content-wrapper">
                {children}
              </div>
            </div>
          </AppShell>
        </div>
      </body>
    </html>
  );
}
