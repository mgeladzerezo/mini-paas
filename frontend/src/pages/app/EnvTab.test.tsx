import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppSummary, EnvVar } from "../../api/types";
import { renderWithQuery, stubApi } from "../../test/render";
import { EnvTab } from "./EnvTab";

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
  current_release_id: 3,
  current_version: 3,
  latest_status: "healthy",
  created_at: "2025-01-01T12:00:00Z",
};

const stored: EnvVar[] = [
  { key: "DB_PASSWORD", value: null, is_secret: true },
  { key: "LOG_LEVEL", value: "info", is_secret: false },
];

afterEach(() => vi.unstubAllGlobals());

describe("EnvTab", () => {
  it("never shows a stored secret and keeps it when saving other changes", async () => {
    const calls = stubApi({
      "GET /apps/7/env": () => stored,
      "PUT /apps/7/env": () => stored,
    });
    renderWithQuery(<EnvTab app={app} />);

    const secretField = await screen.findByLabelText("Value of DB_PASSWORD");
    expect(secretField).toHaveValue("");
    expect(secretField).toHaveAttribute("type", "password");

    const user = userEvent.setup();
    const logLevel = screen.getByLabelText("Value of LOG_LEVEL");
    await user.clear(logLevel);
    await user.type(logLevel, "debug");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/apply from the next deployment/);
    const put = calls.find((call) => call.route === "PUT /apps/7/env");
    expect(put?.body).toEqual({
      variables: [
        { key: "DB_PASSWORD", value: null, is_secret: true },
        { key: "LOG_LEVEL", value: "debug", is_secret: false },
      ],
    });
  });

  it("blocks saving an invalid name without calling the API", async () => {
    const calls = stubApi({ "GET /apps/7/env": () => [] });
    renderWithQuery(<EnvTab app={app} />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add variable" }));
    await user.type(screen.getByLabelText("Name"), "NOT-VALID");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not a valid name/);
    expect(calls.map((call) => call.route)).toEqual(["GET /apps/7/env"]);
  });

  it("saves and then redeploys the live image", async () => {
    const calls = stubApi({
      "GET /apps/7/env": () => stored,
      "PUT /apps/7/env": () => stored,
      "POST /apps/7/releases": () => ({ id: 9, version: 4, status: "pending" }),
    });
    renderWithQuery(<EnvTab app={app} />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Save and redeploy" }));

    await screen.findByText(/Release v4 is rolling out/);
    await waitFor(() =>
      expect(calls.find((call) => call.route === "POST /apps/7/releases")?.body).toEqual({
        reuse_image: true,
      }),
    );
  });

  it("cannot redeploy an app that has never been deployed", async () => {
    stubApi({ "GET /apps/7/env": () => [] });
    renderWithQuery(<EnvTab app={{ ...app, current_release_id: null, current_version: null }} />);
    expect(await screen.findByRole("button", { name: "Save and redeploy" })).toBeDisabled();
  });
});
