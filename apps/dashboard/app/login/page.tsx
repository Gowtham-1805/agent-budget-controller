import { redirect } from "next/navigation";
import { LoginForm } from "@/components/LoginForm";
import { getSession } from "@/lib/session";
import { safeNext } from "@/lib/redirect";

export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  // An already-authenticated visitor is sent straight through. This check is
  // done here, server-side against a validated session -- not in
  // middleware.ts, which only knows whether a cookie is present, not whether
  // it is still good.
  const session = await getSession();
  const params = await searchParams;
  if (session) {
    redirect(safeNext(params.next ?? null));
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "var(--bg-app)",
        padding: 16,
      }}
    >
      <div className="card" style={{ width: "100%", maxWidth: 380 }}>
        <LoginForm next={params.next ?? null} />
      </div>
    </div>
  );
}
