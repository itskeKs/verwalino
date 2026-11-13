import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SecondFactorEnrolment } from "./SecondFactorEnrolment";
import * as endpoints from "../api/endpoints";

vi.mock("qrcode", () => ({ default: { toDataURL: async () => "data:image/png;base64,qr" } }));

const codes = ["AAAA-1111", "BBBB-2222", "CCCC-3333", "DDDD-4444", "EEEE-5555",
  "FFFF-6666", "GGGG-7777", "HHHH-8888", "JJJJ-9999", "KKKK-2222"];

beforeEach(() => {
  vi.restoreAllMocks();
});

it("keeps the recovery codes visible until the user presses Fertig", async () => {
  vi.spyOn(endpoints, "startSecondFactorEnrolment").mockResolvedValue({ secret: "SECRETKEY", otpauth_uri: "otpauth://totp/x" });
  vi.spyOn(endpoints, "confirmSecondFactorEnrolment").mockResolvedValue({ recovery_codes: codes });
  const onDone = vi.fn();
  render(<SecondFactorEnrolment onDone={onDone} />);

  fireEvent.click(screen.getByRole("button", { name: "Zweiten Faktor einrichten" }));
  fireEvent.change(await screen.findByLabelText("Aktueller 6-stelliger Code"), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Zweiten Faktor bestätigen" }));

  // The codes are shown once; nothing reloads or hides them on its own.
  expect(await screen.findByText("AAAA-1111")).toBeInTheDocument();
  await waitFor(() => expect(onDone).not.toHaveBeenCalled());
  expect(screen.getByText("KKKK-2222")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Fertig" }));
  expect(onDone).toHaveBeenCalledTimes(1);
});
