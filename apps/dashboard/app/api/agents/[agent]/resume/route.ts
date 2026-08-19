import { NextResponse } from "next/server";
import { resumeAgent } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ agent: string }> },
) {
  try {
    const { agent } = await params;
    const body = await request.json();
    const result = await resumeAgent(agent, body.reason || "Manual operator resume");
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to resume agent" },
      { status: err.status || 500 },
    );
  }
}
