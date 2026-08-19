"use client";

import { useState } from "react";
import type { CatalogModel } from "../lib/types";
import { SearchIcon, CheckCircleIcon, CpuIcon } from "./Icons";

interface CatalogTableProps {
  models: CatalogModel[];
}

export function CatalogTable({ models }: CatalogTableProps) {
  const [filterProvider, setFilterProvider] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const providers = Array.from(new Set(models.map((m) => m.provider)));

  const filtered = models.filter((m) => {
    if (filterProvider !== "ALL" && m.provider.toLowerCase() !== filterProvider.toLowerCase()) {
      return false;
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      return (
        m.model.toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <div className="shadcn-card" style={{ display: "flex", flexDirection: "column", gap: 16, padding: 20 }}>
      {/* Header Filters & Search Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        {/* Provider Segmented Filter Pills */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            className={`btn btn-sm ${filterProvider === "ALL" ? "btn-primary" : "btn-outline"}`}
            onClick={() => setFilterProvider("ALL")}
          >
            All Models ({models.length})
          </button>
          {providers.map((p) => (
            <button
              key={p}
              type="button"
              className={`btn btn-sm ${filterProvider.toLowerCase() === p.toLowerCase() ? "btn-primary" : "btn-outline"}`}
              onClick={() => setFilterProvider(p)}
              style={{ textTransform: "capitalize" }}
            >
              {p} ({models.filter((m) => m.provider.toLowerCase() === p.toLowerCase()).length})
            </button>
          ))}
        </div>

        {/* Search Input */}
        <div style={{ position: "relative", minWidth: 220 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            className="form-input font-mono"
            placeholder="Filter models..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ paddingLeft: 30, fontSize: 12.5 }}
          />
        </div>
      </div>

      {/* Models Table */}
      <div className="table-container" style={{ border: "none" }}>
        <table className="shadcn-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model Identifier</th>
              <th>Max Context</th>
              <th>Max Output</th>
              <th>Input / 1M</th>
              <th>Output / 1M</th>
              <th>Cached / 1M</th>
              <th>Capabilities</th>
              <th style={{ textAlign: "right" }}>Enforcement</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={9} style={{ textAlign: "center", color: "var(--text-muted)", padding: "36px" }}>
                  No models match the filter &quot;{searchQuery}&quot;.
                </td>
              </tr>
            ) : (
              filtered.map((m) => (
                <tr key={`${m.provider}:${m.model}`}>
                  <td>
                    <span className="badge badge-neutral" style={{ fontWeight: 600, textTransform: "capitalize", fontSize: 11.5 }}>
                      {m.provider}
                    </span>
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <code style={{ fontSize: 12, fontWeight: 600, color: "var(--brand-blue)" }}>
                        {m.model}
                      </code>
                    </div>
                  </td>
                  <td className="money" style={{ fontSize: 12.5 }}>
                    {m.max_context_tokens.toLocaleString()}
                  </td>
                  <td className="money" style={{ fontSize: 12.5 }}>
                    {m.max_output_tokens.toLocaleString()}
                  </td>
                  <td className="money" style={{ fontWeight: 600, fontSize: 12.5 }}>
                    ${m.input_per_million}
                  </td>
                  <td className="money" style={{ fontWeight: 600, fontSize: 12.5 }}>
                    ${m.output_per_million}
                  </td>
                  <td className="money" style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    ${m.cached_input_per_million}
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {m.supports_tools && <span className="badge badge-neutral" style={{ fontSize: 10 }}>tools</span>}
                      {m.supports_structured_output && (
                        <span className="badge badge-neutral" style={{ fontSize: 10 }}>json</span>
                      )}
                      {m.supports_vision && <span className="badge badge-neutral" style={{ fontSize: 10 }}>vision</span>}
                      {m.supports_reasoning && (
                        <span className="badge badge-indigo" style={{ fontSize: 10 }}>reasoning</span>
                      )}
                    </div>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <span className="badge badge-ok" style={{ fontSize: 10.5 }}>
                      <CheckCircleIcon size={11} />
                      <span>Pinned</span>
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
