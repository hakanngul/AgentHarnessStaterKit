/** The token reaches the page in the URL fragment (`#token=...`): a fragment is never sent to a
 * server or written to its log. It is read once, removed from the address bar and kept in memory
 * only. A token in the query string is ignored on purpose: that part goes to the server. */

export function takeToken(win: Pick<Window, "location" | "history">): string | null {
  const hash = win.location.hash.startsWith("#") ? win.location.hash.slice(1) : win.location.hash;
  const token = new URLSearchParams(hash).get("token");
  if (token === null) return null;
  // drop the whole fragment, also when the token is empty, so nothing secret stays in the history
  win.history.replaceState(null, "", win.location.pathname + win.location.search);
  return token.length > 0 ? token : null;
}
