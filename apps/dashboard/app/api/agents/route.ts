import { NextResponse } from "next/server";
import { createAgent, getAgents } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const agents = await getAgents();
    return NextResponse.json(agents);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch agents" },
      { status: err.status || 500 },
    );
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await createAgent(body);
    return NextResponse.json(result, { status: 201 });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to create agent" },
      { status: err.status || 500 },
    );
  }
}
