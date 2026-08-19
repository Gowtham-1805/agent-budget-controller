"use client";

import React from "react";
import { usd } from "../lib/api";

interface Slice {
  label: string;
  amount: number;
  color: string;
  tokens: number;
}

const DEFAULT_SLICES: Slice[] = [
  { label: "Claude 3.5 Sonnet", amount: 0.084, color: "#f97316", tokens: 14200 },
  { label: "GPT-4o (OpenAI)", amount: 0.052, color: "#0891b2", tokens: 9100 },
  { label: "Bedrock Nova Lite", amount: 0.028, color: "#16a34a", tokens: 28500 },
  { label: "Deterministic Test", amount: 0.014, color: "#4f46e5", tokens: 4000 },
];

export function SpendDonutChart({ slices = DEFAULT_SLICES }: { slices?: Slice[] }) {
  const totalAmount = slices.reduce((acc, s) => acc + s.amount, 0);

  // SVG Geometry for Donut Ring
  const size = 160;
  const strokeWidth = 20;
  const radius = (size - strokeWidth) / 2;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;

  let cumulativeAngle = 0;

  return (
    <div className="shadcn-card" style={{ height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
      {/* Title */}
      <div>
        <div className="card-title">Spend by Model &amp; Provider</div>
        <div className="card-subtitle">Distribution of inference cost by model class</div>
      </div>

      {/* Donut Ring with Center Summary */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", position: "relative", margin: "14px 0" }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          {slices.map((slice, i) => {
            const fraction = totalAmount > 0 ? slice.amount / totalAmount : 0;
            const strokeDasharray = `${fraction * circumference} ${circumference}`;
            const strokeDashoffset = -cumulativeAngle;
            cumulativeAngle += fraction * circumference;

            return (
              <circle
                key={i}
                cx={center}
                cy={center}
                r={radius}
                fill="transparent"
                stroke={slice.color}
                strokeWidth={strokeWidth}
                strokeDasharray={strokeDasharray}
                strokeDashoffset={strokeDashoffset}
                strokeLinecap="round"
                transform={`rotate(-90 ${center} ${center})`}
                style={{ transition: "stroke-dasharray 0.3s ease" }}
              />
            );
          })}
        </svg>

        {/* Center Label */}
        <div
          style={{
            position: "absolute",
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div className="money" style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)" }}>
            {usd(totalAmount, 4)}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Total Spend
          </div>
        </div>
      </div>

      {/* Legend Breakdown List */}
      <div style={{ display: "flex", flexDirection: "column", gap: 7, borderTop: "1px solid var(--border-app)", paddingTop: 12 }}>
        {slices.map((slice, i) => {
          const pct = totalAmount > 0 ? ((slice.amount / totalAmount) * 100).toFixed(0) : "0";
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, backgroundColor: slice.color }} />
                <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>{slice.label}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="money" style={{ color: "var(--text-primary)", fontWeight: 600 }}>{usd(slice.amount, 4)}</span>
                <span style={{ fontSize: 11, color: "var(--text-muted)", width: 28, textAlign: "right" }}>{pct}%</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
