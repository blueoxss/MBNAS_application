import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { ApiClient } from "../src/api/client";
import { CandidateView } from "../src/components/CandidateView";
import { DeviceTargetsTable, RegistryCapsTable, ResolvedVariantBadge, SearchSpaceState } from "../src/components/Capabilities";
import { cand } from "./fixtures";

afterEach(cleanup);

const targets = [
  { id: "host-gpu-0", name: "Host GPU", kind: "host_gpu", available: true, reason: null },
  { id: "jetson-orin-nx", name: "Jetson Orin NX", kind: "jetson", available: false, reason: "no Jetson attached to this host" },
];
const reg: any[] = [
  {
    id: "yolo11s", family: "yolo", status: "AVAILABLE", reason: null,
    capabilities: {
      export_trt: { status: "VERIFIED_GPU", reason: null },
      devices: { "host-gpu-0": { status: "VERIFIED_TRT", reason: null }, "jetson-orin-nx": { status: "UNAVAILABLE", reason: "no jetson" } },
      adapter_class: "UltralyticsAdapter",
    },
  },
  { id: "etri-hybrid", family: "etri", status: "UNVERIFIED", reason: "x", capabilities: { export_trt: { status: "NOT_RUN", reason: "no TRT evidence" } } },
];

describe("S3-F7 server capabilities visible", () => {
  it("device targets table shows id, name, available as text, reason", () => {
    render(<DeviceTargetsTable targets={targets} />);
    const t = screen.getByTestId("device-targets-table");
    expect(t.textContent).toContain("jetson-orin-nx");
    expect(t.textContent).toContain("Jetson Orin NX");
    expect(t.textContent).toContain("no Jetson attached to this host");
    expect(within(t).getAllByText("available").length).toBe(1);
    expect(within(t).getAllByText("unavailable").length).toBe(1);
  });
  it("registry rows show export_trt and host-gpu-0 status text, not truncated", () => {
    render(<RegistryCapsTable rows={reg} />);
    const t = screen.getByTestId("registry-table");
    expect(within(t).getByText("VERIFIED_GPU")).toBeInTheDocument();
    expect(within(t).getByText("VERIFIED_TRT")).toBeInTheDocument();
    expect(within(t).getByText("NOT_RUN")).toBeInTheDocument();
    expect(t.textContent).toContain("no TRT evidence");
    expect(t.textContent).toContain("UltralyticsAdapter");
    expect(t.textContent).not.toContain("...");
  });
  it("search space state shows variant and reason", () => {
    render(<SearchSpaceState space={{ variant: "unavailable", reason: "ETRI root missing" }} />);
    const s = screen.getByTestId("search-space-state");
    expect(s).toHaveTextContent("unavailable");
    expect(s).toHaveTextContent("ETRI root missing");
  });
  it("resolved variant badge reads search_space_resolved.variant of a run", () => {
    const run: any = { resolved_protocol: { console: { search: { search_space_resolved: { variant: "native_fallback" } } } } };
    render(<ResolvedVariantBadge run={run} />);
    expect(screen.getByTestId("resolved-variant")).toHaveTextContent("native_fallback");
    cleanup();
    const { container } = render(<ResolvedVariantBadge run={null} />);
    expect(container.textContent).toBe("");
  });
  it("baseline candidate shows baseline (no search audit), not a dash audit panel", async () => {
    const c = cand({ arm: "Baseline", family: "yolo11s" } as any);
    const client = { candidateDetail: async () => ({ candidate_id: "c", blocks: [], lineage: {}, evaluations: [], candidate_status: "EVALUATED", audit: { search_space_variant: null } }) } as unknown as ApiClient;
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={client} />);
    expect(await screen.findByTestId("candidate-audit-baseline")).toHaveTextContent("baseline (no search audit)");
    expect(screen.queryByTestId("candidate-audit")).toBeNull();
  });
});

describe("S3-F7 settings opt-in", () => {
  it("allow_native_fallback checkbox exists, default unchecked, help text, edits field", async () => {
    const { SearchCard } = await import("../src/screens/Settings");
    const edits: [string, unknown][] = [];
    const app: any = { policy: { console: { search: { arms: ["GA"] } } }, editField: (p: string, v: unknown) => edits.push([p, v]), resolvedHints: {}, locked: false, validation: { response: null } };
    render(<SearchCard app={app} issues={[]} />);
    const cb = screen.getByLabelText(/allow native fallback/i) as HTMLInputElement;
    expect(cb.checked).toBe(false);
    expect(document.body.textContent).toMatch(/CNN \+ native attention fallback space only/);
    await userEvent.click(cb);
    expect(edits).toContainEqual(["console.search.allow_native_fallback", true]);
  });
});
