import { NextRequest, NextResponse } from "next/server";

import { proxyFreeLocalMutation } from "@/server/controlPlane/agentGatewayRoute";
import {
  ENROLLMENT_APPROVAL_MAX_BODY_BYTES,
  requestGatewayEnrollment,
} from "@/server/controlPlane/agentGatewayEnrollmentApprovals";
import { controlPlaneMode } from "@/server/controlPlane/config";
import { errorPayload } from "@/server/controlPlane/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PRIVATE_HEADERS = {
  "Cache-Control": "no-store",
  Vary: "Authorization, X-AgentOps-Workspace-Id, X-AgentOps-Agent-Id, Idempotency-Key",
};

export async function POST(request: NextRequest) {
  try {
    if (controlPlaneMode() === "proxy") {
      return proxyFreeLocalMutation(
        request,
        "/agent-gateway/enrollment/request",
        {
          maxBytes: ENROLLMENT_APPROVAL_MAX_BODY_BYTES,
          label: "Gateway enrollment request",
        },
      );
    }
    const result = await requestGatewayEnrollment(request);
    return NextResponse.json(result.body, {
      status: result.status,
      headers: PRIVATE_HEADERS,
    });
  } catch (error) {
    const failure = errorPayload(error);
    return NextResponse.json(failure.body, {
      status: failure.status,
      headers: PRIVATE_HEADERS,
    });
  }
}
