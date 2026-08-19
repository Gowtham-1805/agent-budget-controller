import { NextResponse } from "next/server";
import { getCatalogModels } from "../../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const provider = searchParams.get("provider") || undefined;
    const models = await getCatalogModels(provider);
    return NextResponse.json(models);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch catalog models" },
      { status: err.status || 500 },
    );
  }
}
