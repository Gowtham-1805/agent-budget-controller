import { NextResponse } from "next/server";
import { getProviders } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const providers = await getProviders();
    return NextResponse.json(providers);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch providers" },
      { status: err.status || 500 },
    );
  }
}
