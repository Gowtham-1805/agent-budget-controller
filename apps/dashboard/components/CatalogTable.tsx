"use client";

import type { CatalogModel } from "../lib/types";

interface CatalogTableProps {
  models: CatalogModel[];
}

export function CatalogTable({ models }: CatalogTableProps) {
  if (models.length === 0) {
    return (
      <div className="table-container">
        <div className="empty-state">No models loaded in price catalog.</div>
      </div>
    );
  }

  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model ID</th>
            <th>Max Context</th>
            <th>Max Output</th>
            <th>Input / 1M</th>
            <th>Output / 1M</th>
            <th>Cached / 1M</th>
            <th>Capabilities</th>
            <th>Preflight Metering</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={`${m.provider}:${m.model}`}>
              <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>{m.provider}</td>
              <td className="money" style={{ color: "var(--info)" }}>
                {m.model}
              </td>
              <td className="money">{m.max_context_tokens.toLocaleString()}</td>
              <td className="money">{m.max_output_tokens.toLocaleString()}</td>
              <td className="money">${m.input_per_million}</td>
              <td className="money">${m.output_per_million}</td>
              <td className="money" style={{ color: "var(--text-muted)" }}>
                ${m.cached_input_per_million}
              </td>
              <td>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {m.supports_tools && <span className="badge muted">tools</span>}
                  {m.supports_structured_output && (
                    <span className="badge muted">json</span>
                  )}
                  {m.supports_vision && <span className="badge muted">vision</span>}
                  {m.supports_reasoning && (
                    <span className="badge muted">reasoning</span>
                  )}
                </div>
              </td>
              <td>
                <span className="badge ok">Ready</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
