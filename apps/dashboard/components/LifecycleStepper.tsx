"use client";

import React from "react";
import type { PlaygroundLifecycleStep } from "../lib/types";
import { CheckCircleIcon, XIcon, ShieldIcon, ActivityIcon } from "./Icons";

interface LifecycleStepperProps {
  steps: PlaygroundLifecycleStep[];
}

export function LifecycleStepper({ steps }: LifecycleStepperProps) {
  if (!steps || steps.length === 0) return null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <ShieldIcon size={16} className="text-primary" />
          <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--text-primary)" }}>
            10-Stage Financial Authorization &amp; Settlement Trace
          </span>
        </div>
        <span className="badge badge-neutral" style={{ fontSize: 11 }}>
          Pre-Inference Firewall
        </span>
      </div>

      <div style={{ position: "relative", paddingLeft: 12 }}>
        {/* Continuous Left Vertical Guide Line */}
        <div
          style={{
            position: "absolute",
            top: 14,
            bottom: 14,
            left: 21,
            width: 2,
            backgroundColor: "var(--border-app)",
            zIndex: 0,
          }}
        />

        <div style={{ display: "flex", flexDirection: "column", gap: 8, position: "relative", zIndex: 1 }}>
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
                  padding: "10px 14px",
                  backgroundColor: isBlocked
                    ? "var(--danger-soft)"
                    : isCompleted
                    ? "#ffffff"
                    : "var(--bg-app)",
                  border: isBlocked
                    ? "1px solid var(--danger-border)"
                    : "1px solid var(--border-app)",
                  borderRadius: "var(--radius-lg)",
                  fontSize: 12.5,
                  boxShadow: "var(--shadow-sm)",
                  transition: "all 0.15s ease",
                }}
              >
                {/* Node circle */}
                <div
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: "9999px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 10,
                    fontWeight: 700,
                    flexShrink: 0,
                    marginTop: 1,
                    backgroundColor: isBlocked
                      ? "var(--danger)"
                      : isCompleted
                      ? "var(--ok)"
                      : isRunning
                      ? "var(--brand-blue)"
                      : "var(--bg-muted)",
                    color: isBlocked || isCompleted || isRunning ? "#ffffff" : "var(--text-muted)",
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
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: 13,
                        color: isBlocked ? "var(--danger)" : "var(--text-primary)",
                      }}
                    >
                      Stage {step.step_number}: {step.name}
                    </div>
                    <span
                      className={`badge ${
                        isBlocked
                          ? "badge-danger"
                          : isCompleted
                          ? "badge-ok"
                          : isRunning
                          ? "badge-cyan"
                          : "badge-neutral"
                      }`}
                      style={{ fontSize: 10.5, textTransform: "uppercase" }}
                    >
                      {step.status}
                    </span>
                  </div>

                  <div style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 2 }}>
                    {step.description}
                  </div>

                  {step.details && Object.keys(step.details).length > 0 && (
                    <div
                      style={{
                        marginTop: 6,
                        padding: "5px 8px",
                        backgroundColor: "var(--bg-app)",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--border-app)",
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
                          <strong style={{ color: "var(--text-primary)" }}>{String(v)}</strong>
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
