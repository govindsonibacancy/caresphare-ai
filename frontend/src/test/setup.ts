import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'

// Explicit cleanup: this project's vitest config does not enable global
// test functions (`globals: true`), so @testing-library/react's own
// automatic-cleanup mechanism (which relies on a global `afterEach`) never
// registers itself. Without this, every test's rendered DOM would remain
// mounted for the rest of the file, causing later queries to match
// elements left over from earlier tests.
afterEach(() => {
  cleanup()
})
