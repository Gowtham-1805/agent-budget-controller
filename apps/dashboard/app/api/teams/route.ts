import { NextResponse } from "next/server";
import { createTeam, getTeams } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const teams = await getTeams();
    return NextResponse.json(teams);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch teams" },
      { status: err.status || 500 },
    );
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await createTeam(body);
    return NextResponse.json(result, { status: 201 });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to create team" },
      { status: err.status || 500 },
    );
  }
}
