import { NextResponse } from "next/server";
import { getLedger } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const agentId = searchParams.get("agent_id") || undefined;
    const limit = Number(searchParams.get("limit") || "100");
    const entries = await getLedger(agentId, limit);
    return NextResponse.json(entries);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch ledger" },
      { status: err.status || 500 },
    );
  }
}
