import { NextResponse } from "next/server";
import { getEvents } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const limit = Number(searchParams.get("limit") || "100");
    const events = await getEvents(limit);
    return NextResponse.json(events);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to fetch events" },
      { status: err.status || 500 },
    );
  }
}
