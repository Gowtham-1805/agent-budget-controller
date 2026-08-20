import { redirect } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { TopHeader } from "@/components/TopHeader";
import { getSession } from "@/lib/session";
import { SessionProvider } from "@/lib/session-context";

/**
 * The authoritative auth check for every operator-facing page. `middleware.ts`
 * only checked whether a session cookie was *present*; this validates it
 * server-side against the gateway and redirects if it is missing, forged,
 * expired, or revoked -- a logged-out visitor never sees so much as a flash
 * of the shell.
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }

  return (
    <SessionProvider session={session}>
      <div className="app-shell">
        <AppShell>
          <div className="main-viewport">
            <TopHeader />
            <div className="page-content-wrapper">{children}</div>
          </div>
        </AppShell>
      </div>
    </SessionProvider>
  );
}
