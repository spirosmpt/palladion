import { describe, expect, it } from "vitest";

import { compass, formatMinutes } from "./i18n";
import { fraction, hhmm, minutesBetween } from "./time";

describe("time helpers", () => {
  it("formats in the scenario time zone", () => {
    expect(hhmm("2018-07-23T13:41:00Z")).toBe("16:41");
  });
  it("computes fractions and minutes", () => {
    const s = "2018-07-23T16:30:00+03:00";
    const e = "2018-07-23T20:30:00+03:00";
    expect(fraction("2018-07-23T18:30:00+03:00", s, e)).toBeCloseTo(0.5);
    expect(fraction("2018-07-23T10:00:00+03:00", s, e)).toBe(0);
    expect(minutesBetween(s, e)).toBe(240);
  });
});

describe("i18n helpers", () => {
  it("names compass points in both languages", () => {
    expect(compass(292.5, "en")).toBe("WNW");
    expect(compass(292.5, "el")).toBe("ΔΒΔ");
  });
  it("formats durations", () => {
    expect(formatMinutes(17)).toBe("17 λεπτά");
    expect(formatMinutes(97, "en")).toBe("1 h 37 min");
  });
});
