import { NextResponse } from "next/server";
import { updateTeamBudget } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ team: string }> },
) {
  try {
    const { team } = await params;
    const body = await request.json();
    const result = await updateTeamBudget(team, body);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to update team budget" },
      { status: err.status || 500 },
    );
  }
}
