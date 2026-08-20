"use client";

import { createContext, useContext } from "react";
import type { SessionIdentity } from "./types";

const SessionContext = createContext<SessionIdentity | null>(null);

export function SessionProvider({
  session,
  children,
}: {
  session: SessionIdentity;
  children: React.ReactNode;
}) {
  return (
    <SessionContext.Provider value={session}>
      {children}
    </SessionContext.Provider>
  );
}

/**
 * Only ever rendered inside `app/(app)/layout.tsx`, which redirects to
 * `/login` before mounting anything if no session was resolved server-side --
 * so a null context here means this hook was used outside that subtree, not
 * that the user is logged out.
 */
export function useSession(): SessionIdentity {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error("useSession() called outside <SessionProvider>");
  }
  return session;
}
