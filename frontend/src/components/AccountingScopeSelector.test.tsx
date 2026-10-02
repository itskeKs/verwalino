import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AccountingScopeSelector } from "./AccountingScopeSelector";

const scope = (id: string) => ({ id, label: id, scope_type: "WEG", valid_from: "2025-01-01", valid_to: null });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Form() {
  const [property, setProperty] = useState("property-a");
  const [value, setValue] = useState<string | null>(null);
  return <><button onClick={() => { setProperty("property-b"); setValue(null); }}>Change property</button>
    <AccountingScopeSelector key={property} propertyId={property} value={value} onChange={setValue} kind="WEG" />
    <output>{value || "No scope selected"}</output></>;
}

it("requests exact preparable choices, preserves searched selection and clears it on property change", async () => {
  const requests: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = new URL(input, "http://test"); requests.push(url);
    const id = url.searchParams.get("property_id") === "property-b" ? "scope-b" : url.searchParams.get("q") ? "scope-search" : "scope-a";
    return new Response(JSON.stringify({ items: [scope(id)], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  render(<Form />);
  await screen.findByRole("option", { name: /scope-a/ });
  const choice = screen.getByRole("combobox", { name: "Abrechnungskreis" });
  fireEvent.change(choice, { target: { value: "scope-a" } });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "search" } });
  await screen.findByRole("option", { name: /scope-search/ });
  expect(choice).toHaveValue("scope-a");
  expect(requests[0].searchParams.get("capability")).toBe("prepare");
  expect(requests[0].searchParams.get("financial_creation")).toBe("true");
  expect(requests[0].searchParams.get("scope_type")).toBe("WEG");
  fireEvent.click(screen.getByText("Change property"));
  await screen.findByRole("option", { name: /scope-b/ });
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("");
  expect(screen.queryByRole("option", { name: /scope-a/ })).toBeNull();
  expect(screen.getByText("No scope selected")).toBeInTheDocument();
});
