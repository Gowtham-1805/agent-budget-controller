"use client";

/**
 * Live budget state over WebSocket, with an authoritative refetch on reconnect.
 *
 * Two properties matter here and both are about not lying to the operator.
 *
 * **Duplicate and out-of-order events are discarded by version.** The transport
 * gives at-least-once delivery with no ordering guarantee, so an event whose
 * version is not newer than what we hold is dropped. Without this a late-
 * arriving stale event would visibly rewind a budget on screen.
 *
 * **A reconnect refetches rather than resumes.** Events that arrived while the
 * socket was down are simply gone; replaying from where we left off would leave
 * the display permanently behind reality. The backend is asked for current
 * state instead, which is the only thing that is actually authoritative.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { BudgetState, BudgetUpdateEvent } from "./types";

export type ConnectionState = "connecting" | "live" | "polling" | "offline";

interface Options {
  websocketUrl?: string;
  /** Used on first load, on reconnect, and as the fallback when no socket. */
  refetch: () => Promise<BudgetState[]>;
  pollIntervalMs?: number;
}

export function useLiveBudgets({
  websocketUrl,
  refetch,
  pollIntervalMs = 5000,
}: Options) {
  const [budgets, setBudgets] = useState<BudgetState[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  /** Highest version seen per scope, for discarding stale events. */
  const versions = useRef(new Map<string, number>());
  const socketRef = useRef<WebSocket | null>(null);

  const loadAuthoritative = useCallback(async () => {
    try {
      const fresh = await refetch();
      setBudgets(fresh);
      setLastUpdated(new Date());
      // The refetch supersedes anything the event stream told us, so the
      // version map is cleared rather than merged.
      versions.current.clear();
      return true;
    } catch {
      setConnection("offline");
      return false;
    }
  }, [refetch]);

  const applyEvent = useCallback((event: BudgetUpdateEvent) => {
    const key = `${event.scope_type}:${event.scope_id}`;
    const seen = versions.current.get(key) ?? -1;
    if (event.version <= seen) {
      // A duplicate or a straggler. Applying it would rewind the display.
      return;
    }
    versions.current.set(key, event.version);

    setBudgets((current) =>
      current.map((budget) =>
        budget.scope_type === event.scope_type &&
        budget.scope_id === event.scope_id
          ? {
              ...budget,
              committed_usd: event.committed_usd,
              reserved_usd: event.reserved_usd,
              utilization_percent: event.utilization_percent,
            }
          : budget,
      ),
    );
    setLastUpdated(new Date());
  }, []);

  useEffect(() => {
    void loadAuthoritative();
  }, [loadAuthoritative]);

  useEffect(() => {
    if (!websocketUrl) {
      // No socket configured: fall back to polling. Less immediate, but the
      // numbers stay correct, which matters more.
      setConnection("polling");
      const timer = setInterval(() => void loadAuthoritative(), pollIntervalMs);
      return () => clearInterval(timer);
    }

    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    const connect = () => {
      if (closed) return;
      setConnection("connecting");
      const socket = new WebSocket(websocketUrl);
      socketRef.current = socket;

      socket.onopen = () => {
        attempt = 0;
        setConnection("live");
        // Anything that happened while we were disconnected was missed, so
        // resync from the backend rather than assuming continuity.
        void loadAuthoritative();
      };

      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as BudgetUpdateEvent;
          if (event.type === "budget.state.updated") applyEvent(event);
        } catch {
          // A malformed frame is not worth tearing the connection down for.
        }
      };

      socket.onclose = () => {
        if (closed) return;
        setConnection("polling");
        // Capped exponential backoff, so a gateway restart does not turn into
        // a reconnect storm from every open dashboard.
        const delay = Math.min(30_000, 1000 * 2 ** attempt++);
        retry = setTimeout(connect, delay);
        void loadAuthoritative();
      };

      socket.onerror = () => socket.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socketRef.current?.close();
    };
  }, [websocketUrl, applyEvent, loadAuthoritative, pollIntervalMs]);

  return { budgets, connection, lastUpdated, refresh: loadAuthoritative };
}
