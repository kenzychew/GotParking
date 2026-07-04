import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useShortcuts } from "./useShortcuts";

describe("useShortcuts", () => {
  it("starts empty with no shortcuts", () => {
    const { result } = renderHook(() => useShortcuts());
    expect(result.current.available).toBe(true);
    expect(result.current.items).toEqual([]);
  });

  it("adding a pick surfaces it as a shortcut with its display name", () => {
    const { result } = renderHook(() => useShortcuts());
    act(() => result.current.pick("1"));
    expect(result.current.items).toEqual([{ carparkId: "1", name: "Suntec City" }]);
  });

  it("persists across an unmount + fresh mount (simulated reload)", () => {
    const first = renderHook(() => useShortcuts());
    act(() => first.result.current.pick("2"));
    first.unmount();

    const second = renderHook(() => useShortcuts());
    expect(second.result.current.items).toEqual([{ carparkId: "2", name: "Marina Square" }]);
  });

  it("caps at 3 shortcuts and ranks by pick-count with recency tiebreak", () => {
    const { result } = renderHook(() => useShortcuts());
    act(() => {
      result.current.pick("1");
      result.current.pick("2");
      result.current.pick("2"); // "2" now count=2, most-picked
      result.current.pick("3");
      result.current.pick("11");
    });
    expect(result.current.items).toHaveLength(3);
    expect(result.current.items[0].carparkId).toBe("2"); // highest count wins
    // "3" and "11" are tied at count=1; "11" was picked more recently.
    expect(result.current.items.map((i) => i.carparkId)).toEqual(["2", "11", "3"]);
  });

  it('reports unavailable and leaves items empty when localStorage throws', () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    const { result } = renderHook(() => useShortcuts());
    expect(result.current.available).toBe(false);

    act(() => result.current.pick("1"));
    expect(result.current.items).toEqual([]);

    spy.mockRestore();
  });
});
