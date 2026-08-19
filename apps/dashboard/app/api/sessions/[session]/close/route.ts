import { NextResponse } from "next/server";
import { closeSession } from "../../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ session: string }> },
) {
  try {
    const { session } = await params;
    const result = await closeSession(session);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to close session" },
      { status: err.status || 500 },
    );
  }
}
