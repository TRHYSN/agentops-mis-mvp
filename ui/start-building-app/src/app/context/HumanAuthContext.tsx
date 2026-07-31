import { createContext, useContext } from "react";
import type { HumanAuthUser } from "../data/liveApi";

export interface HumanAuthContextValue {
  required: boolean;
  user: HumanAuthUser | null;
  logout: () => Promise<void>;
}

export const HumanAuthContext = createContext<HumanAuthContextValue | null>(null);

export function useHumanAuth() {
  const value = useContext(HumanAuthContext);
  if (!value) throw new Error("useHumanAuth must be used inside AuthGate");
  return value;
}
