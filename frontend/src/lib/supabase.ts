import { createClient } from '@supabase/supabase-js'

const envUrl = import.meta.env.VITE_SUPABASE_URL
const envAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!envUrl || !envAnonKey) {
  console.warn(
    'VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are not configured - authentication will not work until they are set in frontend/.env.',
  )
}

// createClient() validates the URL eagerly, so a placeholder keeps the app
// runnable (build/dev/lint) before real Supabase credentials are set.
export const supabase = createClient(envUrl || 'http://localhost:54321', envAnonKey || 'not-configured')
