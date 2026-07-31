import { NextRequest, NextResponse } from "next/server";

import { boundedJsonObject } from "@/server/controlPlane/boundedJson";
import { controlPlaneMode } from "@/server/controlPlane/config";
import { errorPayload } from "@/server/controlPlane/http";
import {
  issueWorkspaceEntitlementAdminChallenge,
} from "@/server/controlPlane/workspaceEntitlementAdminChallenges";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ workspaceId: string }>;
};

const PRIVATE_HEADERS = {
  "Cache-Control": "no-store",
  Vary: "Cookie, Origin, X-AgentOps-Workspace-Id, X-AgentOps-CSRF",
};

export async function OPTIONS() {
  return new NextResponse(null, {
    status: 204,
    headers: {
      ...PRIVATE_HEADERS,
      Allow: "POST, OPTIONS",
    },
  });
}

export async function POST(request: NextRequest, context: RouteContext) {
  try {
    const { workspaceId } = await context.params;
    const body = await boundedJsonObject(request, {
      maxBytes: 16 * 1024,
      label: "Workspace entitlement administration challenge",
    });
    if (controlPlaneMode() !== "postgres") {
      return NextResponse.json(
        {
          ok: false,
          error: "entitlement_challenge_postgres_required",
          message: "Entitlement challenges require the TypeScript Postgres control plane.",
          token_omitted: true,
          python_started: false,
        },
        { status: 409, headers: PRIVATE_HEADERS },
      );
    }
    const result = await issueWorkspaceEntitlementAdminChallenge(
      request.headers,
      workspaceId,
      body,
    );
    return NextResponse.json(result.body, {
      status: result.status,
      headers: {
        ...PRIVATE_HEADERS,
        "Set-Cookie": result.setCookie,
      },
    });
  } catch (error) {
    const failure = errorPayload(error);
    return NextResponse.json(failure.body, {
      status: failure.status,
      headers: PRIVATE_HEADERS,
    });
  }
}
