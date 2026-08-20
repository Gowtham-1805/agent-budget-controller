import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentGuard — Autonomous AI Spend Governance",
  description:
    "Enterprise infrastructure spending firewall for autonomous AI agents — financial authorization before inference dispatch.",
};

/**
 * Deliberately bare: the operator shell (sidebar, top header) lives in
 * `app/(app)/layout.tsx`, not here, because `/login` renders through this
 * same root layout and must not be wrapped in a shell whose nav links point
 * at pages a logged-out visitor cannot see.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
