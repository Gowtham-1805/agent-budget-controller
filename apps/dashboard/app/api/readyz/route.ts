import { NextResponse } from "next/server";
import { getReadiness } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await getReadiness();
    return NextResponse.json(data);
  } catch (err: any) {
    return NextResponse.json(
      {
        status: "error",
        checks: {},
        detail: { catalog_version: "2026-08-19.1", error: err.message },
      },
      { status: 500 },
    );
  }
}
