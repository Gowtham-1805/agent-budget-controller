import { NextResponse } from "next/server";
import { disableProvider } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ provider: string }> },
) {
  try {
    const { provider } = await params;
    const result = await disableProvider(provider);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to disable provider" },
      { status: err.status || 500 },
    );
  }
}
