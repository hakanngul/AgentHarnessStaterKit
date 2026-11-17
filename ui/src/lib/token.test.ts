import { takeToken } from "./token";

function fakeWindow(hash: string, search = "") {
  const replaceState = vi.fn();
  return {
    win: { location: { hash, pathname: "/ui/", search }, history: { replaceState } } as never,
    replaceState,
  };
}

describe("takeToken", () => {
  it("reads the token from the fragment and clears the fragment from the address bar", () => {
    const { win, replaceState } = fakeWindow("#token=abc-DEF_123");
    expect(takeToken(win)).toBe("abc-DEF_123");
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(null, "", "/ui/");
  });

  it("keeps the query string when it clears the fragment", () => {
    const { win, replaceState } = fakeWindow("#token=t", "?a=1");
    takeToken(win);
    expect(replaceState).toHaveBeenCalledWith(null, "", "/ui/?a=1");
  });

  it("decodes a percent-encoded token", () => {
    const { win } = fakeWindow("#token=a%2Bb%3D");
    expect(takeToken(win)).toBe("a+b=");
  });

  it("finds the token among other fragment parameters", () => {
    const { win } = fakeWindow("#x=1&token=tok&y=2");
    expect(takeToken(win)).toBe("tok");
  });

  it("ignores a token in the query string (that part goes to the server)", () => {
    const { win, replaceState } = fakeWindow("", "?token=leaky");
    expect(takeToken(win)).toBeNull();
    expect(replaceState).not.toHaveBeenCalled();
  });

  it("returns null and leaves the address alone when there is no token", () => {
    for (const hash of ["", "#", "#/jobs", "#other=1"]) {
      const { win, replaceState } = fakeWindow(hash);
      expect(takeToken(win), hash).toBeNull();
      expect(replaceState, hash).not.toHaveBeenCalled();
    }
  });

  it("clears the fragment even when the token is empty, and reports none", () => {
    const { win, replaceState } = fakeWindow("#token=");
    expect(takeToken(win)).toBeNull();
    expect(replaceState).toHaveBeenCalledTimes(1);
  });

  it("keeps a literal + in the token (not turned into a space)", () => {
    const { win } = fakeWindow("#token=ab+cd%2Bef");
    expect(takeToken(win)).toBe("ab+cd+ef");
  });

  it("uses a token with broken percent-encoding as typed", () => {
    const { win } = fakeWindow("#token=100%25%zz");
    expect(takeToken(win)).toBe("100%25%zz");
  });

  it("finds the token among other fragment parameters", () => {
    const { win } = fakeWindow("#a=1&token=xyz");
    expect(takeToken(win)).toBe("xyz");
  });
});
