import { expect, it } from "vitest";
import { rentAmount } from "./rentAmount";

it.each([
  ["750,00", "750.00"], ["150.00", "150.00"], ["2.250,00", "2250.00"],
  [" 000750,29 ", "750.29"], ["0", "0.00"], ["60,5", "60.50"],
  ["999.999.999.999,99", "999999999999.99"], ["999999999999.99", "999999999999.99"],
])("normalizes exact form amount %s", (input, expected) => {
  expect(rentAmount(input)).toBe(expected);
});

it.each(["", "abc", "NaN", "Infinity", "-1", "1e400", "0.001", "1.000", "2.25,00", "2,250.00", "2.250,001", "1000000000000", "1.000.000.000.000,00"])("refuses invalid or ambiguous form amount %s", input => {
  expect(rentAmount(input)).toBeNull();
});
