import { NextResponse } from "next/server";
import { runPlaygroundChat } from "../../../../lib/api";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await runPlaygroundChat(body);
    return NextResponse.json(result);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Failed to execute governed playground inference" },
      { status: err.status || 500 },
    );
  }
}
