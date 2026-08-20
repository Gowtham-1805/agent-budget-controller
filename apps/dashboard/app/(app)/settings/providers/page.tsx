import { CatalogTable } from "@/components/CatalogTable";
import { ProviderCard } from "@/components/ProviderCard";
import { SetupWizard } from "@/components/SetupWizard";
import { getCatalogModels, getProviders, getReadiness } from "@/lib/api";
import type { CatalogModel, ProviderConfig, Readiness } from "@/lib/types";
import { SettingsIcon, CheckCircleIcon, AlertCircleIcon, ShieldIcon } from "@/components/Icons";

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
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Provider Adapters &amp; Model Pricing Catalog</h1>
          <p className="page-description">
            Configure, test, and govern real LLM providers (Amazon Bedrock, OpenAI, Anthropic) and deterministic test doubles against pinned pricing.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="badge badge-indigo" style={{ fontSize: 12, padding: "4px 10px" }}>
            <ShieldIcon size={13} />
            <span>Catalog: {readiness?.detail?.catalog_version || "unknown"}</span>
          </span>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>Failed to load provider configuration: {error}</span>
        </div>
      )}

      {/* Production Provider Warning Banner */}
      {!hasProductionProvider && (
        <div className="notice-box warning">
          <AlertCircleIcon size={16} />
          <div>
            <strong>Development Test Double Active:</strong> The gateway is currently operating with the deterministic local test double. Connect Amazon Bedrock (via IAM) or OpenAI / Anthropic (via API Key) below to route production inference.
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
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Provider Adapters ({providers.length})
            </h2>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
              Active inference endpoints, authentication modes, and credentials
            </p>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
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
      <div style={{ marginTop: 8 }}>
        <div style={{ marginBottom: 14 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
            Deterministic Pricing &amp; Capability Catalog
          </h2>
          <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
            Every inference request is pre-metered and financial limits are enforced strictly against these pinned rates.
          </p>
        </div>

        <CatalogTable models={catalogModels} />
      </div>
    </div>
  );
}
