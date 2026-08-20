import { NextResponse } from "next/server";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function GET() {
  const session = await getSession();
  if (!session) {
    return NextResponse.json(
      { error: { type: "unauthenticated", message: "not logged in" } },
      { status: 401 },
    );
  }
  return NextResponse.json(session);
}
