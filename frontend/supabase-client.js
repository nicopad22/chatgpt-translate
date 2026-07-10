// ===== Supabase Client =====
// Shared module — must be loaded AFTER the Supabase CDN script

(function () {
  var SUPABASE_URL = 'https://hbdgitevyptizksjewkz.supabase.co';
  var SUPABASE_KEY = 'sb_publishable_7i8iyZznOao7WfqHLH126Q_bOkfpAvL';

  // Create client using global `supabase` from CDN
  window.supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

  /**
   * Get the current session's access token.
   * Returns null if no session exists.
   */
  window.getAccessToken = async function () {
    var result = await window.supabaseClient.auth.getSession();
    var session = result.data.session;
    return session ? session.access_token : null;
  };

  /**
   * Get the current authenticated user info.
   * Returns { id, email, created_at, user_metadata } or null.
   */
  window.getCurrentUser = async function () {
    var result = await window.supabaseClient.auth.getSession();
    var session = result.data.session;
    if (!session) return null;

    return {
      id: session.user.id,
      email: session.user.email,
      created_at: session.user.created_at,
      user_metadata: session.user.user_metadata || {}
    };
  };

  /**
   * Sign out the current user and redirect to the requested page.
   */
  window.signOut = async function (options) {
    var redirectTo = (options && options.redirectTo) ? options.redirectTo : '/';
    await window.supabaseClient.auth.signOut();
    window.location.href = redirectTo;
  };
})();
