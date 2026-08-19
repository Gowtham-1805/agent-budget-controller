import type { Metadata } from "next";
import { AppHeader } from "../components/AppHeader";
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
        <div className="page">
          <AppHeader />
          {children}
        </div>
      </body>
    </html>
  );
}
