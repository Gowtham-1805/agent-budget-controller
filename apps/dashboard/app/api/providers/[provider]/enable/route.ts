import { NextResponse } from "next/server";
import { enableProvider } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ provider: string }> },
) {
  try {
    const { provider } = await params;
    const result = await enableProvider(provider);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to enable provider" },
      { status: err.status || 500 },
    );
  }
}
