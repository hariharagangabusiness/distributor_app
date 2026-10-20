// Captures this device's location every 30 minutes and sends it to the
// server, for as long as this tab stays open on a tracked-role login (Staff/
// Supervisor/Manager - see base.html, which only includes this file for
// those roles). Only fires during the working days/hours configured in
// Company Settings (window.LOCATION_WORKING_DAYS, injected by base.html),
// by the DEVICE's own clock/day - the server independently re-checks its
// own clock (and its own copy of the same setting) before storing anything,
// so a wrong device clock can only cause a missed or extra attempt, never a
// bad write.
//
// Limits, by the nature of a website (not a native app): this can only run
// while the tab is open and in a browser that's still executing JS for it -
// closing the tab, force-quitting the browser, or the phone locking/sleeping
// for long enough to suspend the tab all stop tracking until it's reopened.
// There is no way for a normal website to track location in the background.
//
// A small status pill (bottom-right) shows what's actually happening -
// capturing silently and failing silently is exactly how this kind of
// feature goes unnoticed as broken for weeks, so every outcome (success,
// permission denied, GPS timeout, outside working hours, server rejected
// it) is surfaced somewhere the person using the phone can see it and
// report back, instead of vanishing into a browser console nobody opens.
(function () {
  if (!window.LOCATION_TRACKING_ENABLED) return;

  var PING_INTERVAL_MS = 30 * 60 * 1000; // 30 minutes
  var WORK_START_HOUR = 9;
  var WORK_END_HOUR = 20;   // 8 PM
  // Date.getDay(): Sunday=0, Monday=1, ... Saturday=6.
  var DAY_ABBR = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var WORKING_DAYS = (window.LOCATION_WORKING_DAYS && window.LOCATION_WORKING_DAYS.length)
    ? window.LOCATION_WORKING_DAYS : ['Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  var pill = null;
  function showStatus(text, kind) {
    if (!pill) {
      pill = document.createElement('div');
      pill.id = 'location-tracking-pill';
      pill.style.cssText = 'position:fixed;bottom:14px;right:14px;z-index:2000;' +
        'padding:.4rem .7rem;border-radius:999px;font-size:.75rem;font-family:sans-serif;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.2);cursor:default;max-width:80vw;' +
        'transition:opacity .3s;opacity:0.9;';
      document.body.appendChild(pill);
    }
    var colors = {
      ok: '#1f5c4d', wait: '#607d8b', warn: '#c9971e', error: '#c0392b',
    };
    pill.style.background = colors[kind] || colors.wait;
    pill.style.color = '#fff';
    pill.textContent = '📍 ' + text;
    pill.style.opacity = '0.9';
  }

  function withinWorkingHours() {
    var d = new Date();
    return WORKING_DAYS.indexOf(DAY_ABBR[d.getDay()]) !== -1
      && d.getHours() >= WORK_START_HOUR && d.getHours() < WORK_END_HOUR;
  }

  if (!navigator.geolocation) {
    showStatus('Location tracking not supported by this browser', 'error');
    return;
  }

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var latest = null;   // newest fix from watchPosition
  var lastSentAt = 0;  // ms; throttles uploads to one per PING_INTERVAL_MS

  function upload(pos) {
    lastSentAt = Date.now();
    var headers = { 'Content-Type': 'application/json' };
    if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;
    fetch('/api/location-ping', {
      method: 'POST',
      headers: headers,
      credentials: 'same-origin',
      body: JSON.stringify({
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data && data.ok) {
        var t = new Date();
        showStatus('Location sent ' + t.getHours() + ':' + String(t.getMinutes()).padStart(2, '0'), 'ok');
      } else {
        var reasons = {
          'not-tracked': "Server didn't recognize this login as tracked",
          'outside-hours': 'Server says outside working hours (check server clock/timezone)',
          'bad-coords': 'Device sent invalid coordinates',
        };
        showStatus('Not saved: ' + (reasons[(data && data.reason)] || (data && data.reason) || 'unknown reason'), 'warn');
      }
    }).catch(function () {
      lastSentAt = 0; // let the next tick retry instead of waiting a full interval
      showStatus('Could not reach server (offline?) — will retry shortly', 'error');
    });
  }

  // Sends the newest fix if a full interval has passed and we're inside working
  // hours. Called on every watch update and by a 1-minute timer (a stationary
  // device may get no watch updates at all).
  function maybeUpload() {
    if (!latest) return;
    if (!withinWorkingHours()) {
      showStatus('Location tracking paused (outside 9 AM–8 PM, ' + WORKING_DAYS.join('/') + ')', 'wait');
      return;
    }
    if (Date.now() - lastSentAt >= PING_INTERVAL_MS) upload(latest);
  }

  function start() {
    showStatus('Starting location tracking…', 'wait');
    var watchId = navigator.geolocation.watchPosition(function (pos) {
      latest = pos;
      maybeUpload();
    }, function (err) {
      var messages = {
        1: 'Location permission denied — enable it in browser/site settings',
        2: 'Position unavailable — GPS/network location off?',
        3: 'Location request timed out',
      };
      showStatus(messages[err.code] || ('Location error: ' + err.message), 'error');
      if (err.code === 1) navigator.geolocation.clearWatch(watchId); // denied: retrying can't help
    }, { enableHighAccuracy: false, timeout: 30000, maximumAge: 5 * 60 * 1000 });
    setInterval(maybeUpload, 60 * 1000);
  }

  // Runs on every page load for a tracked-role login (base.html only includes
  // this file for those roles, after consent) - no button or manual step.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
