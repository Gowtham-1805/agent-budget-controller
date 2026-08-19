import React from "react";

interface MetricCardProps {
  label: string;
  value: string;
  subValue?: string;
  trend?: {
    value: string;
    isPositive?: boolean;
    isNeutral?: boolean;
  };
  colorTheme?: "coral" | "cyan" | "indigo" | "amber" | "emerald";
  icon: React.ComponentType<{ size?: number; className?: string }>;
  sparklineData?: number[];
}

export function MetricCard({
  label,
  value,
  subValue,
  trend,
  colorTheme = "indigo",
  icon: Icon,
  sparklineData = [30, 45, 35, 60, 55, 75, 65, 90],
}: MetricCardProps) {
  const themeColors = {
    coral: { stroke: "#f97316", fill: "rgba(249, 115, 22, 0.12)", badgeBg: "var(--coral-soft)", badgeText: "var(--coral)", badgeBorder: "var(--coral-border)" },
    cyan: { stroke: "#0891b2", fill: "rgba(8, 145, 178, 0.12)", badgeBg: "var(--cyan-soft)", badgeText: "var(--cyan)", badgeBorder: "var(--cyan-border)" },
    indigo: { stroke: "#4f46e5", fill: "rgba(79, 70, 229, 0.12)", badgeBg: "var(--indigo-soft)", badgeText: "var(--indigo)", badgeBorder: "var(--indigo-border)" },
    amber: { stroke: "#d97706", fill: "rgba(217, 119, 6, 0.12)", badgeBg: "var(--warning-soft)", badgeText: "var(--warning)", badgeBorder: "var(--warning-border)" },
    emerald: { stroke: "#16a34a", fill: "rgba(22, 163, 74, 0.12)", badgeBg: "var(--ok-soft)", badgeText: "var(--ok)", badgeBorder: "var(--ok-border)" },
  }[colorTheme];

  // Generate SVG path for sparkline
  const width = 280;
  const height = 44;
  const min = Math.min(...sparklineData);
  const max = Math.max(...sparklineData);
  const range = max - min || 1;

  const points = sparklineData.map((val, idx) => {
    const x = (idx / (sparklineData.length - 1)) * width;
    const y = height - ((val - min) / range) * (height - 8) - 4;
    return `${x},${y}`;
  });

  const linePath = `M ${points.join(" L ")}`;
  const areaPath = `M 0,${height} L ${points.join(" L ")} L ${width},${height} Z`;

  return (
    <div className="shadcn-card" style={{ padding: "18px 20px 14px", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
      <div>
        {/* Card Header: Label and Icon */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-secondary)" }}>
            {label}
          </span>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: "var(--radius-md)",
              backgroundColor: themeColors.badgeBg,
              color: themeColors.badgeText,
              border: `1px solid ${themeColors.badgeBorder}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Icon size={16} />
          </div>
        </div>

        {/* Value and Trend */}
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div className="money" style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.02em" }}>
            {value}
          </div>
          {subValue && (
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
              {subValue}
            </div>
          )}
        </div>

        {trend && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: trend.isNeutral ? "var(--text-secondary)" : trend.isPositive ? "var(--ok)" : "var(--danger)",
                backgroundColor: trend.isNeutral ? "var(--bg-muted)" : trend.isPositive ? "var(--ok-soft)" : "var(--danger-soft)",
                padding: "1px 6px",
                borderRadius: "var(--radius-xs)",
              }}
            >
              {trend.value}
            </span>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>vs last window</span>
          </div>
        )}
      </div>

      {/* Embedded SVG Sparkline */}
      <div style={{ marginTop: 14, overflow: "hidden" }}>
        <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ display: "block" }}>
          <defs>
            <linearGradient id={`grad-${colorTheme}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={themeColors.stroke} stopOpacity="0.2" />
              <stop offset="100%" stopColor={themeColors.stroke} stopOpacity="0.0" />
            </linearGradient>
          </defs>
          <path d={areaPath} fill={`url(#grad-${colorTheme})`} />
          <path d={linePath} fill="none" stroke={themeColors.stroke} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  );
}
