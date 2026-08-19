"use client";

import React, { useState } from "react";

interface DataPoint {
  label: string;
  spend: number;
  limit: number;
}

const SAMPLE_TRENDS: Record<string, DataPoint[]> = {
  Monthly: [
    { label: "Aug 01", spend: 0.04, limit: 0.20 },
    { label: "Aug 05", spend: 0.06, limit: 0.20 },
    { label: "Aug 10", spend: 0.09, limit: 0.20 },
    { label: "Aug 15", spend: 0.12, limit: 0.20 },
    { label: "Aug 18", spend: 0.164, limit: 0.20 },
    { label: "Aug 20", spend: 0.178, limit: 0.20 },
    { label: "Aug 25", spend: 0.19, limit: 0.20 },
  ],
  Weekly: [
    { label: "Mon", spend: 0.02, limit: 0.08 },
    { label: "Tue", spend: 0.035, limit: 0.08 },
    { label: "Wed", spend: 0.048, limit: 0.08 },
    { label: "Thu", spend: 0.055, limit: 0.08 },
    { label: "Fri", spend: 0.068, limit: 0.08 },
    { label: "Sat", spend: 0.072, limit: 0.08 },
    { label: "Sun", spend: 0.076, limit: 0.08 },
  ],
  Daily: [
    { label: "00:00", spend: 0.002, limit: 0.01 },
    { label: "04:00", spend: 0.003, limit: 0.01 },
    { label: "08:00", spend: 0.005, limit: 0.01 },
    { label: "12:00", spend: 0.007, limit: 0.01 },
    { label: "16:00", spend: 0.0085, limit: 0.01 },
    { label: "20:00", spend: 0.0092, limit: 0.01 },
    { label: "23:59", spend: 0.0098, limit: 0.01 },
  ],
};

export function SpendTrendChart() {
  const [timeframe, setTimeframe] = useState<"Monthly" | "Weekly" | "Daily">("Monthly");
  const data = SAMPLE_TRENDS[timeframe] ?? SAMPLE_TRENDS.Monthly ?? [];

  const chartWidth = 600;
  const chartHeight = 220;
  const paddingX = 40;
  const paddingY = 24;

  const maxVal = Math.max(...data.map((d) => d.limit)) * 1.15;
  const minVal = 0;

  const getX = (index: number) => paddingX + (index / (data.length - 1)) * (chartWidth - paddingX * 2);
  const getY = (val: number) => chartHeight - paddingY - (val / maxVal) * (chartHeight - paddingY * 2);

  const spendPoints = data.map((d, i) => `${getX(i)},${getY(d.spend)}`);
  const spendLine = `M ${spendPoints.join(" L ")}`;
  const spendArea = `M ${getX(0)},${chartHeight - paddingY} L ${spendPoints.join(" L ")} L ${getX(data.length - 1)},${chartHeight - paddingY} Z`;

  const limitPoints = data.map((d, i) => `${getX(i)},${getY(d.limit)}`);
  const limitLine = `M ${limitPoints.join(" L ")}`;

  return (
    <div className="shadcn-card" style={{ height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
      {/* Header with Title and Timeframe Filters */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div>
          <div className="card-title">Spend &amp; Budget Runway Trends</div>
          <div className="card-subtitle">Continuous spend trajectory compared against hard budget ceilings</div>
        </div>

        {/* Timeframe Toggle Buttons */}
        <div style={{ display: "flex", backgroundColor: "var(--bg-muted)", borderRadius: "var(--radius-md)", padding: 2 }}>
          {(["Daily", "Weekly", "Monthly"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTimeframe(t)}
              style={{
                border: "none",
                background: timeframe === t ? "#ffffff" : "transparent",
                color: timeframe === t ? "var(--text-primary)" : "var(--text-muted)",
                fontWeight: timeframe === t ? 600 : 500,
                fontSize: 11.5,
                padding: "4px 10px",
                borderRadius: "var(--radius-sm)",
                boxShadow: timeframe === t ? "var(--shadow-sm)" : "none",
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* SVG Chart Surface */}
      <div style={{ position: "relative", width: "100%", height: chartHeight }}>
        <svg width="100%" height={chartHeight} viewBox={`0 0 ${chartWidth} ${chartHeight}`} preserveAspectRatio="none" style={{ overflow: "visible" }}>
          <defs>
            <linearGradient id="spendGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f97316" stopOpacity="0.25" />
              <stop offset="100%" stopColor="#f97316" stopOpacity="0.0" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          {[0, 0.25, 0.5, 0.75, 1].map((pct, i) => {
            const y = chartHeight - paddingY - pct * (chartHeight - paddingY * 2);
            const val = (pct * maxVal).toFixed(2);
            return (
              <g key={i}>
                <line x1={paddingX} y1={y} x2={chartWidth - paddingX} y2={y} stroke="var(--border-app)" strokeDasharray="3 3" strokeWidth="1" />
                <text x={paddingX - 8} y={y + 3} textAnchor="end" fill="var(--text-muted)" fontSize="10" fontFamily="var(--font-mono)">
                  ${val}
                </text>
              </g>
            );
          })}

          {/* Limit Threshold Line */}
          <path d={limitLine} fill="none" stroke="var(--danger)" strokeWidth="1.5" strokeDasharray="4 4" opacity="0.6" />

          {/* Spend Area and Line */}
          <path d={spendArea} fill="url(#spendGrad)" />
          <path d={spendLine} fill="none" stroke="#f97316" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />

          {/* Data Points */}
          {data.map((d, i) => (
            <circle
              key={i}
              cx={getX(i)}
              cy={getY(d.spend)}
              r="4"
              fill="#ffffff"
              stroke="#f97316"
              strokeWidth="2"
            />
          ))}

          {/* X Axis Labels */}
          {data.map((d, i) => (
            <text
              key={i}
              x={getX(i)}
              y={chartHeight - 4}
              textAnchor="middle"
              fill="var(--text-muted)"
              fontSize="10.5"
            >
              {d.label}
            </text>
          ))}
        </svg>
      </div>

      {/* Chart Legend */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 16, marginTop: 8, fontSize: 11.5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 3, backgroundColor: "#f97316", borderRadius: 2 }} />
          <span style={{ color: "var(--text-secondary)" }}>Committed Spend</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 2, borderTop: "2px dashed var(--danger)" }} />
          <span style={{ color: "var(--text-secondary)" }}>Budget Ceiling (Hard Cap)</span>
        </div>
      </div>
    </div>
  );
}
