import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CodeView } from "../components/CodeView";
import { LoginBackground } from "../components/LoginBackground";
import { LayerPill } from "../components/ui";
import { renderMarkdown } from "../markdown";
import { can, permissionsOf } from "../permissions";

describe("permissions", () => {
  it("ranks none < read < write per area", () => {
    const viewer = { permissions: { repositories: "read", insights: "read", settings: "none" } } as const;
    expect(can(viewer, "repositories", "read")).toBe(true);
    expect(can(viewer, "repositories", "write")).toBe(false);
    expect(can(viewer, "settings")).toBe(false);
    expect(permissionsOf(null)).toEqual({ repositories: "none", insights: "none", settings: "none" });
  });
});

describe("markdown", () => {
  it("escapes raw HTML from LLM answers and opens links in a new tab", () => {
    const html = renderMarkdown("Hello <script>alert(1)</script> [docs](https://example.com)");
    expect(html).not.toContain("<script>");
    expect(html).toContain('target="_blank"');
  });
});

describe("code view", () => {
  it("numbers lines and colours keywords, strings and comments", () => {
    const { container } = render(<CodeView content={'def run():\n    return "ok"  # done'} highlight={[2, 2]} />);
    expect(container.querySelectorAll("tr")).toHaveLength(2);
    expect(container.querySelector(".tok-k")?.textContent).toBe("def");
    expect(container.querySelector(".tok-s")?.textContent).toBe('"ok"');
    expect(container.querySelector(".tok-c")?.textContent).toContain("# done");
    expect(container.querySelectorAll("tr.active")).toHaveLength(1);
  });
});

describe("layer pill", () => {
  it("shows the canonical layer and count", () => {
    const { getByText } = render(<LayerPill layer="service" count={4} />);
    expect(getByText("service")).toBeTruthy();
    expect(getByText("4")).toBeTruthy();
  });
});

describe("api errors", () => {
  it("signs out only when the session itself is rejected", async () => {
    const { api } = await import("../api");
    localStorage.setItem("ca-token", "t");
    const original = globalThis.fetch;
    globalThis.fetch = (async () => new Response(JSON.stringify({ detail: "Bitbucket Cloud rejected these credentials." }), { status: 401, headers: { "content-type": "application/json" } })) as typeof fetch;
    await expect(api("/api/git/credentials", { method: "POST", body: "{}" })).rejects.toThrow("rejected");
    expect(localStorage.getItem("ca-token")).toBe("t");
    globalThis.fetch = (async () => new Response(JSON.stringify({ detail: "Not authenticated" }), { status: 401, headers: { "content-type": "application/json" } })) as typeof fetch;
    await expect(api("/api/repositories")).rejects.toThrow();
    expect(localStorage.getItem("ca-token")).toBeNull();
    globalThis.fetch = original;
  });
});

describe("login background", () => {
  it("renders isometric C4 blueprint in light mode and ghost code extraction in dark mode", () => {
    const light = render(<LoginBackground theme="light" />);
    expect(light.getByTestId("login-bg-light")).toBeTruthy();
    expect(light.container.textContent).toContain("C4-L1");

    const dark = render(<LoginBackground theme="dark" />);
    expect(dark.getByTestId("login-bg-dark")).toBeTruthy();
    expect(dark.container.textContent).toContain("ingest_repository");
  });
});
