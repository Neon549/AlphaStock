import { create } from 'zustand'

function consumeAuthHandoff() {
  const params = new URLSearchParams(window.location.search)
  const token = params.get('token')
  const username = params.get('username')
  if (!token || !username) return null
  const displayName = params.get('display_name') || username

  // The landing page uses a one-time URL handoff. Move it to session storage
  // immediately and remove it from the address bar before the app renders.
  sessionStorage.setItem('token', token)
  sessionStorage.setItem('username', username)
  sessionStorage.setItem('display_name', displayName)
  localStorage.removeItem('token')
  localStorage.removeItem('username')
  window.history.replaceState({}, document.title, window.location.pathname + window.location.hash)
  return { token, username, displayName }
}

const authHandoff = consumeAuthHandoff()

export const useAuth = create((set, get) => ({
  // Keep authentication only for the current browser session. Closing the
  // browser/tab starts a fresh session and requires a new login.
  token: authHandoff?.token || sessionStorage.getItem('token') || null,
  username: authHandoff?.username || sessionStorage.getItem('username') || null,
  displayName: authHandoff?.displayName || sessionStorage.getItem('display_name') || null,

  login(token, username, displayName = username) {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    sessionStorage.setItem('token', token)
    sessionStorage.setItem('username', username)
    sessionStorage.setItem('display_name', displayName || username)
    set({ token, username, displayName: displayName || username })
  },

  logout() {
    sessionStorage.removeItem('token')
    sessionStorage.removeItem('username')
    sessionStorage.removeItem('display_name')
    // Clear legacy persistent credentials as well, so a logged-out session
    // cannot be resurrected by a stale handoff or older client state.
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    set({ token: null, username: null, displayName: null })
  },

  isLoggedIn: () => !!get().token,
}))

export const useModal = create((set) => ({
  isOpen: false,
  tab: 'login',
  open: (tab = 'login') => set({ isOpen: true, tab }),
  close: () => set({ isOpen: false }),
}))
