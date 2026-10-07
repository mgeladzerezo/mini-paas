import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export function renderWithQuery(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

type Handler = (body: unknown) => unknown;

/** Stubs fetch with a table of "METHOD /path" handlers and records the calls made. */
export function stubApi(routes: Record<string, Handler>) {
  const calls: { route: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const route = `${init?.method ?? "GET"} ${String(input).replace(/^\/api/, "")}`;
      const body: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ route, body });
      const handler = routes[route];
      if (!handler) {
        return new Response(JSON.stringify({ detail: `no stub for ${route}` }), { status: 500 });
      }
      return new Response(JSON.stringify(handler(body)), { status: 200 });
    }),
  );
  return calls;
}
