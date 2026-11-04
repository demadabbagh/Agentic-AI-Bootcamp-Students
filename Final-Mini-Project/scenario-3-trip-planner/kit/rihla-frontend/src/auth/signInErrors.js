// What to tell the traveller when Google sign-in does not finish. null means "say nothing": they closed the
// popup themselves, and the modal simply stays open.
const MESSAGES = {
  'auth/popup-blocked': 'Your browser blocked the Google sign-in window. Allow pop-ups for this site, then try again.',
  'auth/unauthorized-domain': () =>
    `Google sign-in is not allowed from ${globalThis.location?.hostname || 'this address'} yet. The Firebase ` +
    'project owner must add it under Firebase console > Authentication > Settings > Authorized domains ' +
    '(localhost is allowed by default; open Rihla at http://localhost:<port>, not 127.0.0.1 or a LAN address).',
  'auth/operation-not-allowed': 'Google sign-in is not enabled for this Firebase project yet.',
  'auth/network-request-failed': 'Could not reach Google. Check your connection, then try again.',
  'auth/too-many-requests': 'Too many sign-in attempts. Please wait a moment, then try again.',
  'auth/user-disabled': 'This Google account has been disabled for Rihla.',
  'auth/account-exists-with-different-credential': 'This email is already linked to another sign-in method.',
  // `missing`: the setting names the backend reported (firebaseClient.resolveFirebaseConfig), never values.
  'auth/not-configured': (error) => {
    if (error?.serverReached === false) {
      return 'Could not reach the Rihla backend to load the Google sign-in settings. Start the backend (see the ' +
        'README), then try again. Alternatively set VITE_FIREBASE_* in rihla-frontend/.env.local and restart the frontend.'
    }
    const names = Array.isArray(error?.missing) && error.missing.length ? error.missing.join(', ') : null
    const hints = Array.isArray(error?.hints) && error.hints.length ? `${error.hints.join(' ')} ` : ''
    return 'Google sign-in is not set up on this Rihla server yet. ' +
      (names ? `The server is missing: ${names}. ` : '') + hints +
      'Add the VITE_FIREBASE_* settings to rihla-frontend/.env.local (see .env.local.example) or the RIHLA_FIREBASE_* ones to the backend ' +
      '.env (see .env.example), then restart.'
  },
  'auth/project-mismatch':
    'This frontend is set up for a different Firebase project than the backend. Make VITE_FIREBASE_PROJECT_ID in ' +
    'rihla-frontend/.env.local the same as RIHLA_FIREBASE_PROJECT_ID in the backend .env, then restart both.',
}

const SILENT = new Set(['auth/popup-closed-by-user', 'auth/cancelled-popup-request', 'auth/user-cancelled'])

export function signInErrorMessage(error) {
  const code = typeof error?.code === 'string' ? error.code : ''
  if (SILENT.has(code)) return null
  const message = MESSAGES[code]
  if (typeof message === 'function') return message(error)
  if (message) return message
  if (!code && error instanceof Error && error.message) return error.message // the backend's own explanation
  return 'Google sign-in did not finish. Please try again.'
}
