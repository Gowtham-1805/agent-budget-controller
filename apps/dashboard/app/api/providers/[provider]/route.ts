import { NextResponse } from "next/server";
import { updateProvider } from "../../../../lib/api";

export const dynamic = "force-dynamic";

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ provider: string }> },
) {
  try {
    const { provider } = await params;
    const body = await request.json();
    const result = await updateProvider(provider, body);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to update provider configuration" },
      { status: err.status || 500 },
    );
  }
}
