// Applies the saved theme before paint (no flash), shared by the static legal pages.
// External (not inline) so the site's Content-Security-Policy can stay script-src 'self'.
try {
  var t = localStorage.getItem('es-theme');
  if (t) document.documentElement.setAttribute('data-theme', t);
} catch (e) {}
