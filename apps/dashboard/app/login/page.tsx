import { redirect } from "next/navigation";
import { LoginForm } from "@/components/LoginForm";
import { getSession } from "@/lib/session";
import { safeNext } from "@/lib/redirect";
import { DatabaseIcon, LayersIcon, ShieldIcon } from "@/components/Icons";

export const dynamic = "force-dynamic";

/**
 * The three points below describe what the gateway actually enforces -- they
 * are the product's real guarantees, not marketing copy, and should be edited
 * only alongside the behaviour they claim. See docs/ARCHITECTURE.md.
 */
const GUARANTEES = [
  {
    Icon: LayersIcon,
    title: "Reserved before invoked",
    body: "Worst-case cost is held atomically across every applicable budget before a provider is ever called.",
  },
  {
    Icon: ShieldIcon,
    title: "A hard cap, not a dashboard",
    body: "An exhausted budget returns 429 and the request stops here. Nothing downstream gets to spend.",
  },
  {
    Icon: DatabaseIcon,
    title: "Append-only ledger",
    body: "Every estimate and every actual is written once, priced against a pinned catalog, and never edited.",
  },
];

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
    <div className="auth-layout">
      {/* Decorative/contextual only -- hidden below 940px, and carries no
          information the form itself needs. */}
      <aside className="auth-brand" aria-hidden="true">
        <div className="auth-brand-mark">
          <div className="auth-brand-mark-badge">
            <ShieldIcon size={17} />
          </div>
          <div>
            <div className="auth-brand-name">AgentGuard</div>
            <div className="auth-brand-suffix">Budget Controller</div>
          </div>
        </div>

        <div>
          <h2 className="auth-brand-headline">
            Autonomous agents, on a real budget.
          </h2>
          <div className="auth-brand-points">
            {GUARANTEES.map(({ Icon, title, body }) => (
              <div key={title} className="auth-brand-point">
                <Icon size={16} className="auth-brand-point-icon" />
                <div>
                  <div className="auth-brand-point-title">{title}</div>
                  <div className="auth-brand-point-body">{body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="auth-brand-invariant">
          <strong>committed + reserved &le; limit</strong>
          <br />
          enforced per scope, under concurrency.
        </div>
      </aside>

      <main className="auth-panel">
        <div className="auth-card">
          <div className="auth-compact-mark">
            <div className="auth-compact-mark-badge">
              <ShieldIcon size={19} />
            </div>
            <div>
              <div style={{ fontSize: 14.5, fontWeight: 650 }}>AgentGuard</div>
              <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                Budget Controller
              </div>
            </div>
          </div>

          <h1 className="auth-title">Sign in</h1>
          <p className="auth-subtitle">
            Use your operator account to reach the control plane.
          </p>

          <LoginForm next={params.next ?? null} />
        </div>
      </main>
    </div>
  );
}
