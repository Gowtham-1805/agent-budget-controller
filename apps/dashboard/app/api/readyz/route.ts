import { NextResponse } from "next/server";
import { getReadiness } from "../../../lib/api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await getReadiness();
    return NextResponse.json(data);
  } catch (err: any) {
    // Never fabricate a plausible-looking catalog_version here -- the whole
    // point of this branch is that the gateway couldn't be reached, so any
    // version string would misrepresent a genuine unknown as a known-good one.
    return NextResponse.json(
      {
        status: "error",
        checks: {},
        detail: { catalog_version: "unknown", error: err.message },
      },
      { status: 500 },
    );
  }
}
