import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppSummary, Release, ReleaseDetail } from "../../api/types";
import { renderWithQuery, stubApi } from "../../test/render";
import { DeploymentsTab } from "./DeploymentsTab";

const app: AppSummary = {
  id: 7,
  name: "web",
  repo_full_name: "acme/web",
  branch: "main",
  port: 8080,
  health_path: "/",
  min_replicas: 1,
  max_replicas: 3,
  hostname: "web.acme.apps.example.test",
  current_release_id: 12,
  current_version: 2,
  latest_status: "failed",
  created_at: "2025-01-01T12:00:00Z",
};

function release(overrides: Partial<Release>): Release {
  return {
    id: 1,
    version: 1,
    kind: "build",
    status: "superseded",
    commit_sha: "a".repeat(40),
    commit_message: "Initial commit",
    triggered_by: "octocat",
    image: "registry/apps@sha256:1",
    error: null,
    source_release_id: null,
    can_rollback: false,
    created_at: "2025-01-01T12:00:00Z",
    finished_at: "2025-01-01T12:02:00Z",
    ...overrides,
  };
}

const releases: Release[] = [
  release({
    id: 13,
    version: 3,
    status: "failed",
    commit_message: "Break everything",
    error: "rollout failed: new pods did not become ready; still serving v2",
    image: null,
  }),
  release({ id: 12, version: 2, status: "healthy", commit_message: "Add checkout" }),
  release({ id: 11, version: 1, can_rollback: true }),
];

function detail(id: number, logs: string): ReleaseDetail {
  return { ...releases.find((candidate) => candidate.id === id)!, logs };
}

afterEach(() => vi.unstubAllGlobals());

describe("DeploymentsTab", () => {
  it("lists releases, marks the live one and shows the newest release's logs", async () => {
    stubApi({
      "GET /apps/7/releases": () => releases,
      "GET /apps/7/releases/13": () => detail(13, "error: readiness probe failed"),
    });
    renderWithQuery(<DeploymentsTab app={app} />);

    const liveRow = (await screen.findByText("Add checkout")).closest("tr")!;
    expect(within(liveRow).getByText("live")).toBeInTheDocument();
    expect(screen.getByText(/still serving v2/)).toBeInTheDocument();
    expect(await screen.findByText("error: readiness probe failed")).toBeInTheDocument();
  });

  it("offers rollback only for releases the API marks as restorable", async () => {
    stubApi({
      "GET /apps/7/releases": () => releases,
      "GET /apps/7/releases/13": () => detail(13, ""),
    });
    renderWithQuery(<DeploymentsTab app={app} />);

    await screen.findByText("Initial commit");
    const buttons = screen.getAllByRole("button", { name: "Roll back" });
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.closest("tr")).toHaveTextContent("v1");
  });

  it("rolls back after confirmation", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    const calls = stubApi({
      "GET /apps/7/releases": () => releases,
      "GET /apps/7/releases/13": () => detail(13, ""),
      "GET /apps/7/releases/14": () => ({
        ...release({ id: 14, version: 4, kind: "rollback", status: "pending" }),
        logs: "",
      }),
      "POST /apps/7/releases/11/rollback": () =>
        release({ id: 14, version: 4, kind: "rollback", status: "pending", source_release_id: 11 }),
    });
    renderWithQuery(<DeploymentsTab app={app} />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Roll back" }));

    expect(window.confirm).toHaveBeenCalledOnce();
    expect(calls.some((call) => call.route === "POST /apps/7/releases/11/rollback")).toBe(true);
  });

  it("does nothing when the rollback confirmation is dismissed", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    const calls = stubApi({
      "GET /apps/7/releases": () => releases,
      "GET /apps/7/releases/13": () => detail(13, ""),
    });
    renderWithQuery(<DeploymentsTab app={app} />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Roll back" }));

    expect(calls.every((call) => call.route.startsWith("GET"))).toBe(true);
  });
});
