// Firebase runs Google's sign-in popup and returns a fresh ID token for the Rihla backend to verify. The Rihla
// session is the backend's HttpOnly cookie, so Firebase keeps nothing in this browser (in-memory persistence)
// and is signed out again as soon as the token is handed over.
//
// The web config is public by design (it identifies the project, it grants nothing), and it never comes from
// code; there is no demo fallback. Where it comes from (resolveFirebaseConfig):
//   1. the Rihla backend, GET /api/auth/config, from the server's environment: any copy of Rihla works without
//      its own .env.local, and the project is always the one the backend verifies sign-ins against;
//   2. else this build's rihla-frontend/.env.local (VITE_FIREBASE_*, see .env.example).
const ENV = import.meta.env || {} // Vite fills it in; plain Node (the tests) has none
const BUILD_CONFIG = {
  apiKey: ENV.VITE_FIREBASE_API_KEY,
  authDomain: ENV.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: ENV.VITE_FIREBASE_PROJECT_ID,
  appId: ENV.VITE_FIREBASE_APP_ID,
}

export function completeConfig(config) {
  return Boolean(config?.apiKey && config.authDomain && config.projectId && config.appId)
}

function notConfigured(missing, serverReached, hints = []) {
  return Object.assign(new Error('not configured'), { code: 'auth/not-configured', missing, serverReached, hints })
}

// The config to sign in with, or a rejection with code auth/not-configured (and `missing`: the setting names the
// server reported, never values). Exported for tests: `fetchImpl` and `build` replace the browser's.
export async function resolveFirebaseConfig(fetchImpl = globalThis.fetch, build = BUILD_CONFIG) {
  let server = null
  let reached = false // the backend answered at all (an older one answers without `web`)
  try {
    const response = await fetchImpl('/api/auth/config', { headers: { Accept: 'application/json' } })
    reached = true
    if (response.ok) server = await response.json()
  } catch {
    // not reachable, or not JSON: this build's own config may still do
  }
  if (completeConfig(server?.web)) return server.web
  if (completeConfig(build)) {
    // A sign-in to another project would be refused by the backend, with a far less clear message.
    if (server?.project_id && server.project_id !== build.projectId) {
      throw Object.assign(new Error('other project'), { code: 'auth/project-mismatch' })
    }
    return build
  }
  throw notConfigured(Array.isArray(server?.missing) ? server.missing : [], reached,
    Array.isArray(server?.hints) ? server.hints : [])
}

let firebasePromise = null

// Loaded on first use (the home page does not pay for it); call it when the login modal opens so the popup
// still opens inside the click that asked for it.
export function loadFirebaseAuth() {
  firebasePromise ??= Promise.all([resolveFirebaseConfig(), import('firebase/app'), import('firebase/auth')])
    .then(([config, { initializeApp }, firebaseAuth]) => {
      const app = initializeApp(config, 'rihla')
      const auth = firebaseAuth.initializeAuth(app, {
        persistence: firebaseAuth.inMemoryPersistence,
        popupRedirectResolver: firebaseAuth.browserPopupRedirectResolver,
      })
      return { auth, firebaseAuth }
    })
    .catch((error) => {
      firebasePromise = null
      throw error
    })
  return firebasePromise
}

// Logout: make sure Firebase holds no user in this tab. It normally holds none (in-memory persistence, and
// signed out right after each token), and nothing is loaded when the user never opened the login modal here.
export async function signOutOfFirebase() {
  if (!firebasePromise) return
  const { auth, firebaseAuth } = await firebasePromise
  await firebaseAuth.signOut(auth)
}

// Google sign-in in a popup; resolves with the Firebase ID token (valid for an hour, sent to the backend once).
export async function signInWithGoogleForIdToken() {
  const { auth, firebaseAuth } = await loadFirebaseAuth()
  const provider = new firebaseAuth.GoogleAuthProvider()
  provider.setCustomParameters({ prompt: 'select_account' })
  const result = await firebaseAuth.signInWithPopup(auth, provider)
  try {
    return await result.user.getIdToken()
  } finally {
    await firebaseAuth.signOut(auth).catch(() => {})
  }
}
