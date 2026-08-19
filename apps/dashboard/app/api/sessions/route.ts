import { NextResponse } from "next/server";
import { createSession, getSessions } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const agentId = searchParams.get("agent_id") || undefined;
    const sessions = await getSessions(agentId);
    return NextResponse.json(sessions);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch sessions" },
      { status: err.status || 500 },
    );
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await createSession(body);
    return NextResponse.json(result, { status: 201 });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to create session" },
      { status: err.status || 500 },
    );
  }
}
