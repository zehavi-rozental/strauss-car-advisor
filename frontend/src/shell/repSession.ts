// The representative's sign-in as the top bar sees it (no style): whether a token is stored for this tab, and
// the event the user menu sends to leave the rep area (RepPage listens and clears the token).
const REP_TOKEN_KEY = 'sca.repToken'
export const REP_LOGOUT_EVENT = 'sca:rep-logout'

export function hasRepToken(): boolean {
  try {
    return Boolean(sessionStorage.getItem(REP_TOKEN_KEY))
  } catch {
    return false
  }
}

export function requestRepLogout(): void {
  window.dispatchEvent(new Event(REP_LOGOUT_EVENT))
}
