'use strict';
/* Sign-in page: Google button, email magic link, and the verify step for links (/login#token=...). */

const $ = (id) => document.getElementById(id);
const ERRORS = {
  google: 'Google sign-in didn’t complete. Please try again.',
  google_off: 'Google sign-in isn’t set up on this server.',
  unverified: 'Your Google account’s email isn’t verified.',
  not_allowed: 'This account isn’t allowed to sign in here. Ask the admin to add your email.',
};

function show(id, text) {
  const el = $(id);
  el.textContent = text;
  el.hidden = !text;
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Tracker': '1' },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status})`);
  return data;
}

async function verifyFromHash() {
  const m = location.hash.match(/token=([\w-]+)/);
  if (!m) return false;
  history.replaceState(null, '', '/login');  // keep the token out of history
  $('methods').hidden = true;
  $('title').textContent = 'Signing you in…';
  $('subtitle').textContent = '';
  try {
    await post('/auth/verify', { token: m[1] });
    location.replace('/');
  } catch (err) {
    $('title').textContent = 'Sign in';
    $('methods').hidden = false;
    show('error', err.message);
  }
  return true;
}

async function boot() {
  const err = new URLSearchParams(location.search).get('error');
  if (err) show('error', ERRORS[err] || 'Sign-in failed. Please try again.');

  let cfg = { google: false, email: false, app_name: 'Internship Tracker' };
  try { cfg = await (await fetch('/auth/config')).json(); } catch { /* keep defaults */ }
  $('appName').textContent = cfg.app_name;
  document.title = `Sign in · ${cfg.app_name}`;
  $('googleBtn').hidden = !cfg.google;
  $('emailForm').hidden = !cfg.email;
  $('divider').hidden = !(cfg.google && cfg.email);
  $('noMethods').hidden = cfg.google || cfg.email;

  if (await verifyFromHash()) return;

  $('emailForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = $('email').value.trim();
    if (!email) { $('email').focus(); return; }
    const btn = $('emailBtn');
    btn.disabled = true;
    show('error', '');
    try {
      await post('/auth/email', { email });
      show('notice', `Check ${email} for a sign-in link. It expires in 15 minutes.`);
      $('emailForm').hidden = true;
    } catch (ex) {
      show('error', ex.message);
      btn.disabled = false;
    }
  });
}

boot();
