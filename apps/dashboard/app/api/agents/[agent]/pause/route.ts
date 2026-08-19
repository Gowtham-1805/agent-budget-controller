import { NextResponse } from "next/server";
import { pauseAgent } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ agent: string }> },
) {
  try {
    const { agent } = await params;
    const body = await request.json();
    const result = await pauseAgent(agent, body.reason || "Manual operator pause");
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to pause agent" },
      { status: err.status || 500 },
    );
  }
}
