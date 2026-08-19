import { CatalogTable } from "../../../components/CatalogTable";
import { ProviderCard } from "../../../components/ProviderCard";
import { SetupWizard } from "../../../components/SetupWizard";
import { getCatalogModels, getProviders, getReadiness } from "../../../lib/api";
import type { CatalogModel, ProviderConfig, Readiness } from "../../../lib/types";

export const dynamic = "force-dynamic";

export default async function ProvidersSettingsPage() {
  let providers: ProviderConfig[] = [];
  let catalogModels: CatalogModel[] = [];
  let readiness: Readiness | null = null;
  let error: string | null = null;

  try {
    const [provRes, catRes, readRes] = await Promise.allSettled([
      getProviders(),
      getCatalogModels(),
      getReadiness(),
    ]);

    if (provRes.status === "fulfilled") {
      providers = provRes.value;
    }
    if (catRes.status === "fulfilled") {
      catalogModels = catRes.value;
    }
    if (readRes.status === "fulfilled") {
      readiness = readRes.value;
    }
  } catch (err: any) {
    error = err.message || String(err);
  }

  const productionProviders = providers.filter(
    (p) => p.is_production_ready && p.configured && p.enabled,
  );
  const hasProductionProvider = productionProviders.length > 0;

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Provider Configuration &amp; Price Catalog</h1>
          <p className="page-description">
            Configure, test, and govern real LLM providers (Amazon Bedrock, OpenAI, Anthropic) and deterministic test doubles.
          </p>
        </div>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          Failed to load provider configuration: {error}
        </div>
      )}

      {/* Production Provider Warning Banner */}
      {!hasProductionProvider && (
        <div className="notice-box warning" style={{ marginBottom: 20 }}>
          <div>
            <strong style={{ color: "var(--warn)" }}>No Production Provider Configured:</strong> The gateway is running with only the local test double active. Connect Amazon Bedrock (via IAM) or OpenAI / Anthropic (via API Key) to route real workloads.
          </div>
        </div>
      )}

      {/* First-Run Setup Wizard */}
      {!hasProductionProvider && (
        <SetupWizard
          providers={providers}
          catalogModels={catalogModels}
        />
      )}

      {/* Supported Providers Grid */}
      <div style={{ marginBottom: 32 }}>
        <div className="section-header">
          <span className="section-title">Supported Providers ({providers.length})</span>
        </div>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          {providers.map((p) => (
            <ProviderCard
              key={p.provider}
              initialConfig={p}
              catalogModels={catalogModels}
            />
          ))}
        </div>
      </div>

      {/* Pricing & Model Catalog Section */}
      <div style={{ marginTop: 36 }}>
        <div className="section-header">
          <span className="section-title">Pricing &amp; Model Catalog</span>
          <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            Catalog Version: <code className="font-mono">{readiness?.detail?.catalog_version || "2026-08-19.1"}</code>
          </span>
        </div>
        <div style={{ color: "var(--text-secondary)", fontSize: 12, marginBottom: 12 }}>
          Every request is metered against these pinned rates. Models not present in this catalog are rejected before inference.
        </div>
        <CatalogTable models={catalogModels} />
      </div>
    </main>
  );
}
