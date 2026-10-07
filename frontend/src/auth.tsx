import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { getToken, setToken, setUnauthorizedHandler } from "./api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  signIn: (token: string) => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setTokenState] = useState<string | null>(() => getToken());

  const signIn = useCallback((next: string) => {
    setToken(next);
    setTokenState(next);
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setTokenState(null);
    // Cached responses belong to the previous user's tenant.
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setUnauthorizedHandler(signOut);
  }, [signOut]);

  const value = useMemo(
    () => ({ isAuthenticated: token !== null, signIn, signOut }),
    [token, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
