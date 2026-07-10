// ===== Auth Guard =====
// Protects pages that require authentication.
// Must be loaded AFTER supabase-client.js.

(function () {
  // Check session on load
  window.supabaseClient.auth.getSession().then(function (result) {
    var session = result.data.session;
    if (!session) {
      window.location.href = '/login';
      return;
    }
  });

  // Listen for auth state changes (token refresh, logout, etc.)
  window.supabaseClient.auth.onAuthStateChange(function (event, session) {
    if (event === 'SIGNED_OUT' || !session) {
      window.location.href = '/login';
    }
  });

  /**
   * Returns authorization headers with the current access token.
   * Use this for all backend API calls on protected pages.
   */
  window.getAuthHeaders = async function () {
    var token = await window.getAccessToken();
    return { 'Authorization': 'Bearer ' + token };
  };
})();
