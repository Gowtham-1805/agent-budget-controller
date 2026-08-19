import { NextResponse } from "next/server";
import { updateAgentBudget } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ agent: string }> },
) {
  try {
    const { agent } = await params;
    const body = await request.json();
    const result = await updateAgentBudget(agent, body);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to update agent budget" },
      { status: err.status || 500 },
    );
  }
}
