import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { resolveFirebaseConfig } from './firebaseClient.js'
import { signInErrorMessage } from './signInErrors.js'

// Placeholders only: the real web config never appears in code or tests.
const WEB = { apiKey: 'test-key', authDomain: 'demo.firebaseapp.com', projectId: 'demo', appId: '1:2:web:3' }
const NONE = { apiKey: undefined, authDomain: undefined, projectId: undefined, appId: undefined }

const answer = (status, body) => async (url) => {
  assert.equal(url, '/api/auth/config')
  return { ok: status === 200, status, json: async () => body }
}

describe('Firebase settings: any copy of Rihla can sign in when its backend is set up', () => {
  it("uses the backend's web config, even without a .env.local", async () => {
    assert.deepEqual(await resolveFirebaseConfig(answer(200, { web: WEB, missing: [] }), NONE), WEB)
  })

  it("prefers the backend's config (the project it verifies sign-ins against) over the build's", async () => {
    const build = { ...WEB, projectId: 'another-project' }
    assert.equal((await resolveFirebaseConfig(answer(200, { web: WEB }), build)).projectId, 'demo')
  })

  it("falls back to the build's VITE_FIREBASE_* config when the backend has none or cannot be reached", async () => {
    assert.deepEqual(await resolveFirebaseConfig(answer(200, { web: null, missing: ['RIHLA_FIREBASE_APP_ID'] }), WEB), WEB)
    const offline = async () => { throw new TypeError('Failed to fetch') }
    assert.deepEqual(await resolveFirebaseConfig(offline, WEB), WEB)
  })

  it('without any config, says exactly which settings the server is missing, never a value', async () => {
    const missing = ['RIHLA_FIREBASE_WEB_API_KEY', 'RIHLA_FIREBASE_APP_ID']
    const error = await resolveFirebaseConfig(answer(200, { web: null, missing }), NONE).catch((e) => e)
    assert.equal(error.code, 'auth/not-configured')
    const message = signInErrorMessage(error)
    assert.match(message, /missing: RIHLA_FIREBASE_WEB_API_KEY, RIHLA_FIREBASE_APP_ID/)
    assert.match(message, /\.env\.example/)
  })

  it('when the backend cannot be reached, says to start it instead of blaming the settings', async () => {
    const offline = async () => { throw new TypeError('Failed to fetch') }
    const error = await resolveFirebaseConfig(offline, NONE).catch((e) => e)
    assert.equal(error.code, 'auth/not-configured')
    assert.match(signInErrorMessage(error), /Could not reach the Rihla backend/)
  })

  it('an incomplete config is never used', async () => {
    const partial = { ...WEB, appId: '' }
    const error = await resolveFirebaseConfig(answer(200, { web: partial }), partial).catch((e) => e)
    assert.equal(error.code, 'auth/not-configured')
  })

  it('refuses a build set up for another project, instead of a sign-in the backend would reject', async () => {
    const error = await resolveFirebaseConfig(answer(200, { web: null, project_id: 'the-backends-project' }), WEB)
      .catch((e) => e)
    assert.equal(error.code, 'auth/project-mismatch')
    assert.match(signInErrorMessage(error), /VITE_FIREBASE_PROJECT_ID.*RIHLA_FIREBASE_PROJECT_ID/)
    assert.deepEqual(await resolveFirebaseConfig(answer(200, { web: null, project_id: 'demo' }), WEB), WEB)
  })

  it("passes on the server's hint about a wrongly named setting", async () => {
    const hints = ['FIREBASE_API_KEY is not a name Rihla reads: use RIHLA_FIREBASE_WEB_API_KEY.']
    const error = await resolveFirebaseConfig(answer(200, { web: null, missing: ['RIHLA_FIREBASE_WEB_API_KEY'], hints }),
      NONE).catch((e) => e)
    assert.match(signInErrorMessage(error), /FIREBASE_API_KEY is not a name Rihla reads/)
  })
})
