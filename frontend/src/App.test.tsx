import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { SEED_CARPARKS } from "./seed/seedCarparks";
import { MOCK_FRESH_PAYLOAD, MOCK_STALE_PAYLOAD } from "./test/fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetchAlwaysResolves(body: unknown, status = 200): void {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(body, status)));
}

async function selectViaSearch(user: UserEvent, typed: string, buttonName: string): Promise<void> {
  const input = screen.getByLabelText("Search for a mall");
  await user.clear(input);
  await user.type(input, typed);
  const suggestion = await screen.findByRole("button", { name: buttonName });
  await user.click(suggestion);
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
});

describe("landing state", () => {
  it("shows the search bar, shortcuts, and an inviting prompt -- never a pre-selected carpark", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    render(<App />);

    expect(screen.getByLabelText("Search for a mall")).toBeInTheDocument();
    expect(
      screen.getByText("Search a mall or tap a shortcut to see its forecast"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Suntec City" })).not.toBeInTheDocument();
  });
});

describe("required test slice", () => {
  it("[integration] search -> select -> forecast card renders from mock payload", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    const user = userEvent.setup();
    render(<App />);

    await selectViaSearch(user, "Suntec", "Suntec City");

    expect(await screen.findByRole("heading", { name: "Suntec City" })).toBeInTheDocument();
    // carpark_id "1" in the fixture: state "ml", forecast_lots=120, tier="limited", live_lots=100
    expect(await screen.findByText("~120 lots free in 20 min")).toBeInTheDocument();
    expect(screen.getByText("Limited")).toBeInTheDocument();
    expect(screen.getByText("100 lots available now")).toBeInTheDocument();
    expect(
      screen.getByText("Learned from historical patterns (model lgbm-2026-07-01)"),
    ).toBeInTheDocument();
  });

  it("shortcut add -> persists across remount, cap of 3, pick-count ordering with recency tiebreak", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    const user = userEvent.setup();
    const view = render(<App />);

    // Real, separately-timed picks (each past the anti-double-tap guard
    // window) so this exercises genuine repeat visits, not a single tap
    // misfiring twice -- that distinct scenario has its own test below.
    await selectViaSearch(user, "Suntec", "Suntec City"); // id 1, count -> 1
    await wait(450);
    await selectViaSearch(user, "Suntec", "Suntec City"); // id 1, count -> 2
    await wait(450);
    await selectViaSearch(user, "Marina Square", "Marina Square"); // id 2, count 1
    await wait(450);
    await selectViaSearch(user, "Raffles City", "Raffles City"); // id 3, count 1
    await wait(450);
    await selectViaSearch(user, "Cineleisure", "Cineleisure"); // id 11, count 1 (4th distinct pick)

    const shortcuts = await screen.findByRole("region", { name: "Shortcuts" });
    const chipNames = within(shortcuts)
      .getAllByRole("button")
      .map((button) => button.textContent);
    // id 1 (count 2) wins outright; among the count=1 ties, most-recently
    // picked wins -- Cineleisure then Raffles City; Marina Square (picked
    // longest ago among the ties) is squeezed out of the 3-item cap.
    expect(chipNames).toEqual(["Suntec City", "Cineleisure", "Raffles City"]);

    view.unmount();
    render(<App />);

    const shortcutsAfterRemount = await screen.findByRole("region", { name: "Shortcuts" });
    const chipNamesAfterRemount = within(shortcutsAfterRemount)
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(chipNamesAfterRemount).toEqual(["Suntec City", "Cineleisure", "Raffles City"]);
  });

  it("network failure -> offline copy, retry works", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse(MOCK_FRESH_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<App />);

    await selectViaSearch(user, "Suntec", "Suntec City");

    expect(
      await screen.findByText("No internet connection - showing last-seen data"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("~120 lots free in 20 min")).toBeInTheDocument();
  });

  it('outside-seed-list search shows "not covered yet" + every supported mall', async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Search for a mall"), "Nonexistent Mall Name");

    expect(
      await screen.findByText(
        `No results - try one of the ${SEED_CARPARKS.length} supported malls:`,
      ),
    ).toBeInTheDocument();
    for (const carpark of SEED_CARPARKS) {
      expect(screen.getByRole("button", { name: carpark.name })).toBeInTheDocument();
    }
  });

  it("rapid double-tap on a suggestion is debounced to a single selection", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Search for a mall"), "Suntec");
    const suggestion = await screen.findByRole("button", { name: "Suntec City" });

    // Two clicks fired back-to-back on the same target, well inside the
    // anti-double-tap guard window -- simulates one physical tap
    // misregistering twice (touch+synthetic-click, etc).
    fireEvent.click(suggestion);
    fireEvent.click(suggestion);

    await screen.findByText("~120 lots free in 20 min");

    const stored: unknown = JSON.parse(
      window.localStorage.getItem("gotparking:shortcuts-v1") ?? "[]",
    );
    expect(stored).toEqual([expect.objectContaining({ carparkId: "1", count: 1 })]);
  });

  it("stale payload (generated_at > 15 min) shows the data-delayed caveat, not a fresh number", async () => {
    mockFetchAlwaysResolves(MOCK_STALE_PAYLOAD);
    const user = userEvent.setup();
    render(<App />);

    await selectViaSearch(user, "Suntec", "Suntec City");

    expect(await screen.findByText(/Data delayed - updated \d+m ago/)).toBeInTheDocument();
    expect(screen.getByText("100 lots available now")).toBeInTheDocument();
    expect(screen.queryByText("~120 lots free in 20 min")).not.toBeInTheDocument();
  });

  it("cold_start card shows collecting-data copy + live count, never a forecast number", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD); // carpark_id "3" Raffles City is cold_start
    const user = userEvent.setup();
    render(<App />);

    await selectViaSearch(user, "Raffles", "Raffles City");

    expect(await screen.findByText("Collecting data - check back in a few days")).toBeInTheDocument();
    expect(screen.getByText("50 lots available now")).toBeInTheDocument();
    expect(screen.queryByText(/lots free in 20 min/)).not.toBeInTheDocument();
  });

  it("503 shows distinct server-degraded copy from the offline copy", async () => {
    mockFetchAlwaysResolves(
      { error: "predictions_unavailable", message: "Predictions temporarily unavailable" },
      503,
    );
    const user = userEvent.setup();
    render(<App />);

    await selectViaSearch(user, "Suntec", "Suntec City");

    expect(await screen.findByText("Predictions temporarily unavailable")).toBeInTheDocument();
    expect(
      screen.queryByText("No internet connection - showing last-seen data"),
    ).not.toBeInTheDocument();
  });
});

describe("share link", () => {
  it("a valid ?carpark= id selects that carpark on load", async () => {
    window.history.pushState({}, "", "/?carpark=16"); // VivoCity P3
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "VivoCity P3" })).toBeInTheDocument();
  });

  it("an invalid/garbage ?carpark= id shows a clear error state, never a crash", async () => {
    window.history.pushState({}, "", "/?carpark=not-a-real-id");
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);

    render(<App />);

    expect(
      await screen.findByText("Carpark not found - this link may be outdated or invalid."),
    ).toBeInTheDocument();
    // The rest of the app still works underneath the error banner.
    expect(screen.getByText("Search a mall or tap a shortcut to see its forecast")).toBeInTheDocument();
    expect(screen.getByLabelText("Search for a mall")).toBeInTheDocument();
  });

  it("the share button copies the current carpark's link to the clipboard", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    // userEvent.setup() installs its own clipboard stub on navigator.clipboard,
    // so this redefine must happen AFTER setup() (and stay configurable) or
    // user-event's stub silently wins.
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    render(<App />);
    await selectViaSearch(user, "Suntec", "Suntec City");

    await user.click(screen.getByRole("button", { name: "Share" }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("carpark=1"));
    expect(await screen.findByText("Copied!")).toBeInTheDocument();
  });
});

describe("localStorage unavailable", () => {
  it("shortcuts show a clear degraded message while search still works", async () => {
    mockFetchAlwaysResolves(MOCK_FRESH_PAYLOAD);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    const user = userEvent.setup();
    render(<App />);

    expect(
      await screen.findByText("Shortcuts unavailable in this browser mode"),
    ).toBeInTheDocument();

    await selectViaSearch(user, "Suntec", "Suntec City");
    expect(await screen.findByRole("heading", { name: "Suntec City" })).toBeInTheDocument();

    spy.mockRestore();
  });
});
