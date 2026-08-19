import { NextResponse } from "next/server";
import { testProvider } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ provider: string }> },
) {
  try {
    const { provider } = await params;
    const body = await request.json().catch(() => ({}));
    const result = await testProvider(provider, body.model);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to test provider connection" },
      { status: err.status || 500 },
    );
  }
}
