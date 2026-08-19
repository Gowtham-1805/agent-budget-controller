"use client";

import type { PlaygroundLifecycleStep } from "../lib/types";
import { CheckCircleIcon, XIcon } from "./Icons";

interface LifecycleStepperProps {
  steps: PlaygroundLifecycleStep[];
}

export function LifecycleStepper({ steps }: LifecycleStepperProps) {
  if (!steps || steps.length === 0) return null;

  return (
    <div>
      <div className="section-header" style={{ marginBottom: 14 }}>
        <span className="section-title">10-Stage Authorization &amp; Settlement Trace</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Pre-Inference Gateways &bull; Provider Dispatch &bull; Settlement
        </span>
      </div>

      <div style={{ position: "relative", paddingLeft: 8 }}>
        {/* Continuous Left Vertical Guide Line */}
        <div
          style={{
            position: "absolute",
            top: 14,
            bottom: 14,
            left: 17,
            width: 1,
            background: "var(--border)",
            zIndex: 0,
          }}
        />

        <div style={{ display: "flex", flexDirection: "column", gap: 10, position: "relative", zIndex: 1 }}>
          {steps.map((step) => {
            const isCompleted = step.status === "completed";
            const isBlocked = step.status === "blocked";
            const isRunning = step.status === "running";

            return (
              <div
                key={step.step_number}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 12,
                  padding: "8px 12px",
                  background: isBlocked
                    ? "var(--danger-subtle)"
                    : isCompleted
                    ? "var(--surface-raised)"
                    : "var(--surface-inset)",
                  border: isBlocked
                    ? "1px solid var(--danger-border)"
                    : "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-md)",
                  fontSize: 12.5,
                }}
              >
                {/* Node circle */}
                <div
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 10,
                    fontWeight: 600,
                    flexShrink: 0,
                    marginTop: 1,
                    background: isBlocked
                      ? "var(--danger)"
                      : isCompleted
                      ? "var(--ok)"
                      : isRunning
                      ? "var(--info)"
                      : "var(--surface)",
                    color: isBlocked || isCompleted || isRunning ? "#ffffff" : "var(--text-muted)",
                    border: isCompleted || isBlocked || isRunning ? "none" : "1px solid var(--border)",
                  }}
                >
                  {isCompleted ? (
                    <CheckCircleIcon size={12} />
                  ) : isBlocked ? (
                    <XIcon size={12} />
                  ) : (
                    step.step_number
                  )}
                </div>

                {/* Step content */}
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <div style={{ fontWeight: 600, color: isBlocked ? "var(--danger)" : "var(--text-primary)" }}>
                      Stage {step.step_number}: {step.name}
                    </div>
                    <span
                      className={`badge ${
                        isBlocked
                          ? "danger"
                          : isCompleted
                          ? "ok"
                          : isRunning
                          ? "info"
                          : "muted"
                      }`}
                      style={{ fontSize: 10, padding: "1px 5px" }}
                    >
                      {step.status.toUpperCase()}
                    </span>
                  </div>

                  <div style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 2 }}>
                    {step.description}
                  </div>

                  {step.details && Object.keys(step.details).length > 0 && (
                    <div
                      style={{
                        marginTop: 6,
                        padding: "4px 8px",
                        background: "var(--surface-inset)",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--border-subtle)",
                        display: "flex",
                        flexWrap: "wrap",
                        gap: 10,
                        fontSize: 11,
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {Object.entries(step.details).map(([k, v]) => (
                        <span key={k}>
                          <span style={{ color: "var(--text-muted)" }}>{k}:</span>{" "}
                          <span style={{ color: "var(--text-primary)" }}>{String(v)}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
